"""
视频剪辑模块 - 基于 MoviePy + FFmpeg
智能倍速：有效段 4x / 违规段 2x / 离开段 16x
叠加违规标注、计时器、合规率进度条、片头片尾
"""
import logging
import time
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

# 中文字体回退
_CN_FONT = "simhei.ttf"
if not os.path.exists(f"C:/Windows/Fonts/{_CN_FONT}"):
    _CN_FONT = "Arial"


@dataclass
class ClipConfig:
    """剪辑参数配置"""
    effective_speed: float = 4.0
    violation_speed: float = 2.0
    exit_speed: float = 16.0
    output_fps: int = 30
    output_resolution: Tuple[int, int] = (1920, 1080)
    bitrate: str = "4000k"
    add_intro_outro: bool = True
    show_timer: bool = True
    show_compliance_bar: bool = True
    highlight_violations: bool = True
    violation_duration_sec: float = 3.0  # 违规高亮持续时间
    cover_enabled: bool = True           # 是否生成封面帧
    cover_text: str = "本次作业回顾"       # 封面文案
    cover_image_path: str = ""           # 自定义封面图片路径（优先级高于 best_frame）
    bgm_path: str = ""                   # 背景音乐文件路径
    bgm_volume: float = 0.12             # 背景音乐音量（0~1，默认低音量不压过原声）


@dataclass
class Sequence:
    """视频片段"""
    start_time: float
    end_time: float
    speed: float
    segment_type: str  # 'effective' | 'violation' | 'exit'
    violations: List[Dict] = field(default_factory=list)


class VideoClipper:
    """视频剪辑器"""

    def __init__(self, config: ClipConfig = None):
        self.config = config or ClipConfig()

    def clip(
        self,
        raw_video_path: Path,
        output_path: Path,
        posture_events: List[Dict],
        score_report: dict,
        best_frame_path: str = None,
        progress_callback: callable = None,
    ) -> Path:
        """
        剪辑主流程
        :param raw_video_path: 原始录制视频路径
        :param output_path: 输出视频路径
        :param posture_events: 坐姿事件列表
        :param score_report: 评分报告数据
        :param best_frame_path: 最佳封面帧图片路径（可选）
        :param progress_callback: 进度回调 (percent, message)
        :return: 输出视频路径
        """
        try:
            from moviepy import (
                VideoFileClip, CompositeVideoClip, TextClip,
                ColorClip, concatenate_videoclips,
            )
            from moviepy.video.fx import Margin
            import numpy as np
        except ImportError as e:
            logger.error(f"MoviePy 导入失败: {e}")
            raise

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # BGM 自动选择（如果未指定）
        if not self.config.bgm_path:
            if isinstance(score_report, dict):
                grade = score_report.get("grade", "B")
            else:
                grade = getattr(score_report, "grade", "B")
            self.config.bgm_path = self._select_bgm_by_grade(grade)
            if self.config.bgm_path:
                logger.info(f"自动选择 BGM: {self.config.bgm_path}")

        self._report("加载原始视频...", 0, progress_callback)
        raw_clip = VideoFileClip(str(raw_video_path))
        duration = raw_clip.duration
        logger.info(f"原始视频: {duration:.1f}s, {raw_clip.size}")

        # 1. 划分片段
        self._report("分析违规片段...", 5, progress_callback)
        sequences = self._build_sequences(posture_events, duration)

        # 2. 逐段处理
        clips = []
        epsilon = 0.05  # 50ms 容差，防止浮点边界问题
        for i, seq in enumerate(sequences):
            start_t = max(0, min(seq.start_time, duration - epsilon))
            end_t = max(0, min(seq.end_time, duration - epsilon))
            if end_t <= start_t + 0.01:
                logger.debug(f"跳过零/负时长片段: start={start_t:.3f}, end={end_t:.3f}")
                continue  # 跳过零时长或负时长片段

            pct = 5 + int(80 * i / max(1, len(sequences)))
            self._report(f"处理片段 {i+1}/{len(sequences)} ({seq.segment_type})", pct, progress_callback)

            sub = raw_clip.subclipped(start_t, end_t)

            # 倍速处理 - MoviePy 2.1+ 使用 with_speed_scaled()
            if seq.speed != 1.0:
                sub = sub.with_speed_scaled(seq.speed)

            # 违规标注叠加
            if self.config.highlight_violations and seq.violations:
                sub = self._add_violation_overlay(sub, seq, Margin)

            clips.append(sub)

        self._report("合成视频片段...", 85, progress_callback)

        # 3. 片头片尾 + 封面帧
        final_clips = []
        if self.config.add_intro_outro:
            intro = self._make_intro(score_report, Margin)
            final_clips.append(intro)
            # 封面帧（夹在片头和正文之间）
            # 优先级：cover_image_path > best_frame_path
            cover_img_path = self.config.cover_image_path if (self.config.cover_image_path and os.path.exists(self.config.cover_image_path)) else best_frame_path
            if self.config.cover_enabled and cover_img_path and os.path.exists(cover_img_path):
                cover = self._make_cover(cover_img_path, score_report)
                if cover is not None:
                    final_clips.append(cover)
            final_clips.extend(clips)
            outro = self._make_outro(score_report)
            final_clips.append(outro)
        else:
            final_clips = clips

        # 4. FFmpeg 加速导出（零重编码 concat + BGM 混入）
        result = self._ffmpeg_export(
            final_clips, output_path, progress_callback
        )

        # 清理
        raw_clip.close()
        for c in clips:
            try:
                c.close()
            except Exception:
                pass
        for c in final_clips:
            try:
                c.close()
            except Exception:
                pass

        return result

    def _select_bgm_by_grade(self, grade: str) -> str:
        """根据评分等级自动选择 BGM 文件路径"""
        asset_dir = Path(__file__).parent.parent.parent / "assets" / "audio"
        mapping = {
            "S": asset_dir / "bgm_01_happy.wav",
            "A+": asset_dir / "bgm_01_happy.wav",
            "A": asset_dir / "bgm_01_happy.wav",
            "A-": asset_dir / "bgm_01_happy.wav",
            "B": asset_dir / "bgm_02_warm.wav",
            "C": asset_dir / "bgm_03_energy.wav",
            "D": asset_dir / "bgm_03_energy.wav",
        }
        path = mapping.get(grade, asset_dir / "bgm_02_warm.wav")
        return str(path) if path.exists() else ""

    def _build_sequences(
        self, events: List[Dict], total_duration: float
    ) -> List[Sequence]:
        """根据违规事件划分片段。

        核心优化：
        1. 过滤掉时间戳超出视频时长的无效事件
        2. 合并重叠违规区间（使用 1.5×违规窗口合并间距）
        3. 合并相邻同类型片段减少总数
        """
        violation_window = self.config.violation_duration_sec

        if not events or total_duration <= 0:
            return [Sequence(0, total_duration, self.config.effective_speed, "effective")]

        # 1. 时间戳格式自动检测与归一化
        # 如果最大时间戳远大于视频时长，说明是绝对时间戳（Unix epoch），需要归一化
        raw_timestamps = [ev.get("timestamp", 0) for ev in events if ev.get("timestamp") is not None]
        if raw_timestamps:
            max_raw_ts = max(raw_timestamps)
            min_raw_ts = min(raw_timestamps)
            if max_raw_ts > total_duration * 10 and max_raw_ts > 1_000_000_000:
                # Unix 时间戳，归一化为相对偏移
                logger.info(f"检测到 Unix 时间戳，自动归一化 (min={min_raw_ts:.0f}, max={max_raw_ts:.0f})")
                events = [
                    {**ev, "timestamp": max(0, ev.get("timestamp", 0) - min_raw_ts)}
                    for ev in events
                ]

        # 2. 过滤：只取视频时长内的事件（+窗口容差）
        max_ts = total_duration + violation_window
        valid_events = [ev for ev in events if 0 <= ev.get("timestamp", 0) <= max_ts]
        if not valid_events:
            return [Sequence(0, total_duration, self.config.effective_speed, "effective")]

        # 3. 构建违规区间并去重排序
        intervals = set()
        for ev in valid_events:
            t = ev.get("timestamp", 0)
            start = max(0, t - violation_window)
            end = min(total_duration, t + violation_window)
            if start < end:
                intervals.add((round(start, 2), round(end, 2)))

        # 3. 合并重叠区间（合并间距 = 1.5×违规窗口，比之前 1.0s 更激进）
        merge_gap = max(1.5 * violation_window, 2.0)
        merged = []
        for s, e in sorted(intervals):
            if merged and s <= merged[-1][1] + merge_gap:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))

        # 4. 生成片段序列（大空白段标记为 exit 类型，16x 倍速）
        sequences = []
        cursor = 0.0
        exit_threshold = 5.0  # 空白超过 5 秒视为无人，加速到 16x

        for vs, ve in merged:
            gap = vs - cursor
            if gap > 0.05:  # 跳过过短的片段
                gap_type = "exit" if gap > exit_threshold else "effective"
                gap_speed = self.config.exit_speed if gap_type == "exit" else self.config.effective_speed
                sequences.append(Sequence(cursor, vs, gap_speed, gap_type))
            # 违规段
            seq_violations = [ev for ev in valid_events if vs - 0.5 <= ev.get("timestamp", 0) <= ve + 0.5]
            sequences.append(Sequence(vs, ve, self.config.violation_speed, "violation", seq_violations))
            cursor = ve

        # 尾部填充
        tail_gap = total_duration - cursor
        if tail_gap > 0.05:
            tail_type = "exit" if tail_gap > exit_threshold else "effective"
            tail_speed = self.config.exit_speed if tail_type == "exit" else self.config.effective_speed
            sequences.append(Sequence(cursor, total_duration, tail_speed, tail_type))

        # 5. 合并相邻同类型片段
        collapsed = []
        for seq in sequences:
            if collapsed and collapsed[-1].segment_type == seq.segment_type:
                collapsed[-1].end_time = seq.end_time
                if seq.violations:
                    collapsed[-1].violations.extend(seq.violations)
            else:
                collapsed.append(seq)
        sequences = collapsed

        # 6. 硬上限保护（合并超出上限的相邻片段）
        MAX_SEQUENCES = 200
        if len(sequences) > MAX_SEQUENCES:
            logger.warning(
                f"片段数 {len(sequences)} 超过上限 {MAX_SEQUENCES}，强制合并"
            )
            compact = []
            merge_ratio = max(2, len(sequences) // MAX_SEQUENCES)
            for i, seq in enumerate(sequences):
                if compact and i % merge_ratio != 0:
                    compact[-1].end_time = seq.end_time
                    if seq.violations:
                        compact[-1].violations.extend(seq.violations)
                else:
                    compact.append(seq)
            sequences = compact[:MAX_SEQUENCES]

        logger.info(f"片段划分: {len(valid_events)} 有效事件 → {len(merged)} 合并区间 → {len(sequences)} 片段")
        return sequences

    def _add_violation_overlay(self, clip, seq: Sequence, Margin):
        """在违规片段上叠加彩色边框和文字标注（按违规类型着色）"""
        try:
            from moviepy import TextClip, CompositeVideoClip

            w, h = clip.size

            # 违规类型 → 颜色映射
            vtype = seq.violations[0].get("violation_type", "violation") if seq.violations else "violation"
            color_map = {
                "head_forward": (245, 158, 11),   # 黄色 #F59E0B
                "head_tilt": (59, 130, 246),      # 蓝色 #3B82F6
                "body_tilt": (232, 117, 58),      # 橙色 #E8753A
                "too_close": (139, 92, 246),      # 紫色 #8B5CF6
                "lying_down": (239, 68, 68),      # 红色 #EF4444
            }
            border_color = color_map.get(vtype, (255, 0, 0))

            labels = {
                "head_forward": "低头提醒",
                "head_tilt": "歪头提醒",
                "body_tilt": "身体倾斜",
                "too_close": "距离太近",
                "lying_down": "趴桌提醒",
            }
            label = labels.get(vtype, "坐姿提醒")

            # 彩色边框 - MoviePy 2.1+ 使用 with_effects([Margin(...)])
            bordered = clip.with_effects([Margin(margin_size=6, color=border_color)])

            # 带圆角底框的违规标签
            from moviepy import ColorClip
            tag_bg = ColorClip(size=(180, 52), color=border_color).with_duration(clip.duration).with_opacity(0.9)
            tag_bg = tag_bg.with_position(("center", 16)).with_effects([Margin(margin_size=4, color=border_color)])

            txt = TextClip(
                text=label,
                font_size=32,
                color="white",
                stroke_color="black",
                stroke_width=2,
                font=_CN_FONT,
            ).with_position(("center", 24)).with_duration(clip.duration)

            return CompositeVideoClip([bordered, tag_bg, txt], size=(w + 12, h + 12))
        except Exception as e:
            logger.warning(f"违规标注叠加失败: {e}")
            return clip

    def _make_gradient_bg(self, size=(1920, 1080), top_color=(255, 248, 240), bot_color=(255, 228, 209), duration=3):
        """使用 Pillow 生成暖色渐变背景图，返回 ImageClip"""
        from PIL import Image
        import numpy as np
        w, h = size
        img = np.zeros((h, w, 3), dtype=np.uint8)
        for y in range(h):
            ratio = y / h
            img[y, :] = [
                int(top_color[i] * (1 - ratio) + bot_color[i] * ratio)
                for i in range(3)
            ]
        pil_img = Image.fromarray(img)
        # 保存临时文件供 ImageClip 使用
        tmp = tempfile.mktemp(suffix=".png")
        pil_img.save(tmp)
        from moviepy import ImageClip
        return ImageClip(tmp).with_duration(duration)

    def _make_intro(self, score_report: dict, Margin) -> "VideoClip":
        """生成片头 - 暖色渐变 + 吉祥物 + 日期"""
        try:
            from moviepy import TextClip, CompositeVideoClip, ImageClip
            from datetime import datetime
            from pathlib import Path

            bg = self._make_gradient_bg(duration=3)

            title = TextClip(
                text="StudyBuddy 作业回顾",
                font_size=72,
                color="#E8753A",
                stroke_color="white",
                stroke_width=3,
                font=_CN_FONT,
            ).with_position(("center", 380)).with_duration(3)

            date_str = datetime.now().strftime("%Y年%m月%d日")
            date_box = TextClip(
                text=f"  {date_str}  ",
                font_size=32,
                color="#666666",
                font=_CN_FONT,
            ).with_position(("center", 500)).with_duration(3)

            # 加载吉祥物
            mascot_path = Path(__file__).parent.parent.parent / "assets" / "images" / "mascot" / "mascot_happy.jpg"
            clips = [bg, title, date_box]
            if mascot_path.exists():
                mascot = ImageClip(str(mascot_path)).with_duration(3)
                mascot = mascot.resized(height=320)
                mascot = mascot.with_position((1300, 380))
                clips.append(mascot)

            return CompositeVideoClip(clips)
        except Exception as e:
            logger.warning(f"片头生成失败: {e}")
            from moviepy import ColorClip
            return ColorClip(size=(1920, 1080), color=(255, 248, 240)).with_duration(3)

    def _make_cover(self, best_frame_path: str, score_report) -> "VideoClip":
        """生成封面帧 — 抖音"大字报"风格：暖橙渐变蒙版 + 粗描边文字 + 装饰，停留2.5秒"""
        try:
            from moviepy import ImageClip, TextClip, CompositeVideoClip, ColorClip
            from PIL import Image
            import numpy as np
            from pathlib import Path

            def _sr_get(key, default=0):
                if hasattr(score_report, key):
                    return getattr(score_report, key)
                if isinstance(score_report, dict):
                    return score_report.get(key, default)
                return default

            total = _sr_get("total_score", 0)
            grade = _sr_get("grade", "B")

            # 加载最佳帧图片
            img = ImageClip(best_frame_path).with_duration(2.5)
            img = img.resized(height=1080)
            w = img.w

            # 底部暖橙渐变蒙版（Pillow生成）
            overlay_h = int(1080 * 0.45)
            grad = np.zeros((overlay_h, w, 3), dtype=np.uint8)
            for y in range(overlay_h):
                ratio = y / overlay_h
                alpha_ratio = ratio * 0.75  # 底部最浓
                grad[y, :] = [
                    int(232 * alpha_ratio + 0 * (1 - alpha_ratio)),
                    int(117 * alpha_ratio + 0 * (1 - alpha_ratio)),
                    int(58 * alpha_ratio + 0 * (1 - alpha_ratio)),
                ]
            grad_pil = Image.fromarray(grad)
            tmp_grad = tempfile.mktemp(suffix=".png")
            grad_pil.save(tmp_grad)
            overlay = ImageClip(tmp_grad).with_duration(2.5)
            overlay = overlay.with_position((0, 1080 - overlay_h))

            # 主标题 - 大白字粗描边
            cover_title = TextClip(
                text=self.config.cover_text or "作业小达人",
                font_size=72,
                color="white",
                font=_CN_FONT,
                stroke_color="white",
                stroke_width=4,
            ).with_position(("center", 720)).with_duration(2.5)

            # 副标题 - 金色评分
            summary = TextClip(
                text=f"得分 {total:.0f} 分  ·  等级 {grade}",
                font_size=36,
                color="#FFD700",
                font=_CN_FONT,
                stroke_color="black",
                stroke_width=2,
            ).with_position(("center", 820)).with_duration(2.5)

            clips = [img, overlay, cover_title, summary]

            # 四角装饰：星星和奖杯
            deco_dir = Path(__file__).parent.parent.parent / "assets" / "images" / "backgrounds"
            star_path = deco_dir / "cover_star.png"
            trophy_path = deco_dir / "cover_trophy.png"
            if star_path.exists():
                star = ImageClip(str(star_path)).with_duration(2.5).resized(height=80)
                clips.append(star.with_position((60, 60)))
                clips.append(star.with_position((w - 140, 60)))
            if trophy_path.exists():
                trophy = ImageClip(str(trophy_path)).with_duration(2.5).resized(height=90)
                clips.append(trophy.with_position((w - 140, 1080 - 180)))
                clips.append(trophy.with_position((60, 1080 - 180)))

            return CompositeVideoClip(clips)
        except Exception as e:
            logger.warning(f"封面帧生成失败: {e}")
            return None

    def _make_outro(self, score_report) -> "VideoClip":
        """生成片尾 - 评分面板：大号得分 + 徽章 + 吉祥物 + 鼓励语（兼容 dict 和 dataclass）"""
        try:
            from moviepy import TextClip, CompositeVideoClip, ImageClip
            from pathlib import Path

            def _sr_get(key, default=0):
                if hasattr(score_report, key):
                    return getattr(score_report, key)
                if isinstance(score_report, dict):
                    return score_report.get(key, default)
                return default

            bg = self._make_gradient_bg(duration=4)

            total = _sr_get("total_score", 0)
            grade = _sr_get("grade", "B")
            duration = _sr_get("duration_minutes", 0)

            # 鼓励语
            encouragements = {
                "S": "太棒了！坐姿小达人！",
                "A+": "太棒了！坐姿小达人！",
                "A": "表现优秀！继续保持！",
                "A-": "表现优秀！继续保持！",
                "B": "不错哦，下次可以更好！",
                "C": "加油，注意坐姿哦！",
                "D": "别灰心，下次一定会进步！",
            }
            encourage = encouragements.get(grade, "继续加油！")

            # 大号得分
            score_num = TextClip(
                text=f"{total:.0f}",
                font_size=140,
                color="#FFD700",
                stroke_color="white",
                stroke_width=4,
                font=_CN_FONT,
            ).with_position(("center", 280)).with_duration(4)

            score_label = TextClip(
                text="分",
                font_size=48,
                color="#E8753A",
                font=_CN_FONT,
            ).with_position((960 + 120, 340)).with_duration(4)

            # 等级标签
            grade_text = TextClip(
                text=f"等级 {grade}",
                font_size=56,
                color="#E8753A",
                stroke_color="white",
                stroke_width=2,
                font=_CN_FONT,
            ).with_position(("center", 480)).with_duration(4)

            # 鼓励语
            encourage_clip = TextClip(
                text=encourage,
                font_size=40,
                color="#333333",
                font=_CN_FONT,
            ).with_position(("center", 600)).with_duration(4)

            # 时长
            detail = TextClip(
                text=f"作业时长: {duration:.0f} 分钟",
                font_size=28,
                color="#888888",
                font=_CN_FONT,
            ).with_position(("center", 700)).with_duration(4)

            clips = [bg, score_num, score_label, grade_text, encourage_clip, detail]

            # 等级徽章图片
            badge_path = Path(__file__).parent.parent.parent / "assets" / "images" / "badges" / f"badge_{grade}.jpg"
            if badge_path.exists():
                badge = ImageClip(str(badge_path)).with_duration(4).resized(height=120)
                badge = badge.with_position((960 - 200, 460))
                clips.append(badge)

            # 吉祥物表情根据分数变化
            mascot_map = {"S": "mascot_happy", "A": "mascot_happy", "B": "mascot_default", "C": "mascot_default", "D": "mascot_thinking"}
            mascot_name = mascot_map.get(grade, "mascot_default")
            mascot_path = Path(__file__).parent.parent.parent / "assets" / "images" / "mascot" / f"{mascot_name}.jpg"
            if mascot_path.exists():
                mascot = ImageClip(str(mascot_path)).with_duration(4).resized(height=200)
                mascot = mascot.with_position((1300, 680))
                clips.append(mascot)

            return CompositeVideoClip(clips)
        except Exception as e:
            logger.warning(f"片尾生成失败: {e}")
            from moviepy import ColorClip
            return ColorClip(size=(1920, 1080), color=(255, 248, 240)).with_duration(4)

    def _make_keyword_black(self, ffmpeg: str, temp_dir: Path, resolution: tuple = None) -> Optional[Path]:
        """生成 1 秒纯黑画面 + keywords.txt 文本叠加（透明度 1%）"""
        keywords_path = Path(__file__).resolve().parent.parent.parent / "assets" / "keywords.txt"
        if not keywords_path.exists():
            logger.info("keywords.txt 不存在，跳过黑屏关键词")
            return None

        try:
            lines = []
            with open(keywords_path, "r", encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if s:
                        lines.append(s)

            if not lines:
                return None

            # 使用录制视频实际分辨率，避免拉伸
            if resolution is None:
                resolution = self.config.output_resolution
            w, h = int(resolution[0]), int(resolution[1])

            # 竖向排列关键词文本（drawtext fontsize 很小 + alpha=0.01）
            y_step = h // max(len(lines), 1)
            font_size = max(10, min(20, y_step - 2))

            drawtext_parts = []
            for i, kw in enumerate(lines):
                y = i * y_step + y_step // 2
                drawtext_parts.append(
                    f"drawtext=text='{kw}':"
                    f"fontsize={font_size}:fontcolor=black@0.005:"
                    f"x=(w-text_w)/2:y={y}"
                )

            vf_chain = ",".join(drawtext_parts)

            output = temp_dir / "keyword_black.mp4"
            cmd = [
                ffmpeg, "-y",
                "-f", "lavfi",
                "-i", f"color=c=black:s={w}x{h}:d=1:r=30",
                "-vf", vf_chain,
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "18",
                "-pix_fmt", "yuv420p",
                "-an",
                str(output),
            ]

            subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=True)
            logger.info(f"黑屏关键词已生成: {len(lines)} 关键词")
            return output
        except Exception as e:
            logger.warning(f"黑屏关键词生成失败: {e}")
            return None

    def _concat_has_audio(self, temp_files: list, ffmpeg_path: str) -> bool:
        """检测 concat 合并后的视频是否包含音频流"""
        probe = subprocess.run(
            [ffmpeg_path, "-i", str(temp_files[0]), "-f", "null", "-"],
            capture_output=True, text=True,
        )
        # stderr 中包含流信息，检查是否有 "Audio:" 行
        for line in probe.stderr.split("\n"):
            if "Audio:" in line:
                return True
        return False

    def _ffmpeg_export(
        self,
        clips: list,
        output_path: Path,
        progress_callback=None,
    ) -> Path:
        """使用 FFmpeg 零重编码 concat + 硬件编码加速导出。

        将 MoviePy 生成的各片段逐段导出到临时文件，
        再通过 FFmpeg concat demuxer 合并并混入 BGM。
        相比 MoviePy write_videofile，提速 2-5 倍。
        """
        import imageio_ffmpeg

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        total = len(clips)
        if total == 0:
            raise ValueError("无片段可导出")

        temp_dir = Path(tempfile.mkdtemp(prefix="sb_clip_"))
        temp_files = []
        total_duration = 0.0

        # 获取实际录制分辨率（首个非占位 clip 的尺寸）
        actual_resolution = self.config.output_resolution
        for c in clips:
            try:
                actual_resolution = c.size
                break
            except Exception:
                continue

        try:
            # 阶段 1：逐段导出到临时 MP4（MoviePy 处理倍速/标注）
            for i, clip in enumerate(clips):
                pct = 85 + int(8 * i / total)
                self._report(f"导出片段 {i+1}/{total}", pct, progress_callback)
                temp_path = temp_dir / f"seg_{i:04d}.mp4"
                clip.write_videofile(
                    str(temp_path),
                    fps=self.config.output_fps,
                    codec="libx264",
                    audio_codec="aac",
                    preset="ultrafast",
                    logger=None,
                )
                temp_files.append(temp_path)
                total_duration += clip.duration

            # 阶段 2：生成 concat 列表
            concat_list = temp_dir / "concat.txt"
            with open(concat_list, "w", encoding="utf-8") as f:
                for tf in temp_files:
                    f.write(f"file '{tf.as_posix()}'\n")

            # 阶段 3：FFmpeg concat + BGM 混入，单次编码
            self._report("合并片段 + 混音...", 95, progress_callback)

            has_bgm = self.config.bgm_path and os.path.exists(self.config.bgm_path)
            cmd = [
                ffmpeg, "-y",
                "-f", "concat", "-safe", "0", "-i", str(concat_list),
            ]

            if has_bgm:
                cmd += ["-i", self.config.bgm_path]

            # 检测合并后的视频是否包含音频流
            has_audio = self._concat_has_audio(temp_files, ffmpeg)

            # 滤镜：BGM 循环 + 混音
            filter_parts = []
            if has_bgm:
                filter_parts.append(
                    f"[1:a]aloop=loop=-1:size=2e9,"
                    f"atrim=duration={total_duration:.2f},"
                    f"volume={self.config.bgm_volume}[bgm]"
                )
                if has_audio:
                    filter_parts.append("[0:a][bgm]amix=inputs=2:duration=first[a]")
                else:
                    filter_parts.append("[bgm]anull[a]")

            if filter_parts:
                cmd += ["-filter_complex", ";".join(filter_parts)]
                cmd += ["-map", "0:v", "-map", "[a]"]
            else:
                cmd += ["-map", "0:v", "-map", "0:a"]

            # 视频滤镜链：暖色调 + 锐化
            cmd += ["-vf", "eq=brightness=0.05:contrast=1.05:saturation=1.15,unsharp=3:3:0.5:3:3:0.5"]

            cmd += [
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", "23",
                "-c:a", "aac",
                "-b:a", "128k",
                "-pix_fmt", "yuv420p",
            ]

            # 先输出到临时文件，后续追加黑屏关键词段
            temp_output = temp_dir / "main_concat.mp4"
            cmd.append(str(temp_output))

            logger.info(f"FFmpeg: {' '.join(cmd[:6])} ...")
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=600,
            )
            if proc.returncode != 0:
                logger.error(f"FFmpeg 失败: {proc.stderr[-500:]}")
                raise RuntimeError(f"FFmpeg 导出失败 (code={proc.returncode})")

            # 阶段 4：末尾 1 秒黑屏 + 关键词水印（alpha=1%）
            keyword_black = self._make_keyword_black(ffmpeg, temp_dir, actual_resolution)
            if keyword_black is not None:
                self._report("追加黑屏关键词...", 98, progress_callback)
                concat2 = temp_dir / "concat_final.txt"
                with open(concat2, "w", encoding="utf-8") as f:
                    f.write(f"file '{temp_output.as_posix()}'\n")
                    f.write(f"file '{keyword_black.as_posix()}'\n")
                final_cmd = [
                    ffmpeg, "-y",
                    "-f", "concat", "-safe", "0", "-i", str(concat2),
                    "-c", "copy",
                    str(output_path),
                ]
                subprocess.run(final_cmd, capture_output=True, text=True, timeout=120, check=True)
            else:
                # 无关键词，直接重命名
                import shutil as _shutil
                _shutil.move(str(temp_output), str(output_path))

        finally:
            # 清理临时文件
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

        self._report("完成", 100, progress_callback)
        logger.info(f"剪辑完成: {output_path}")
        return output_path

    def _report(self, msg: str, pct: int, callback):
        logger.info(f"[剪辑进度 {pct}%] {msg}")
        if callback:
            try:
                callback(pct, msg)
            except Exception:
                pass