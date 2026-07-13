"""
视频剪辑模块 - 基于 MoviePy + FFmpeg + Pillow
新流程：封面 → 主视频(PIP可选) → 总结封底 → 黑屏关键词
手机端存在时以手机端为主画面，PC端为画中画
"""
import logging
import time
import random
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# 中文字体回退
_CN_FONT = "simhei.ttf"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ROUNDED_FONT_CANDIDATES = [
    r"G:\StudyBuddy\assets\fonts\龚帆酸奶布丁体.ttf",
    str(_PROJECT_ROOT / "assets" / "fonts" / "ZCOOLKuaiLe-Regular.ttf"),
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
]


def _find_font(prefer_rounded: bool = True) -> str:
    """查找可用的中文字体，prefer_rounded 时优先圆润字体"""
    if prefer_rounded:
        for fp in _ROUNDED_FONT_CANDIDATES:
            if os.path.exists(fp):
                return fp
    if os.path.exists(f"C:/Windows/Fonts/{_CN_FONT}"):
        return f"C:/Windows/Fonts/{_CN_FONT}"
    return "Arial"


@dataclass
class ClipConfig:
    """剪辑参数配置"""
    effective_speed: float = 4.0
    violation_speed: float = 2.0
    exit_speed: float = 16.0
    output_fps: int = 30
    output_resolution: Tuple[int, int] = (1920, 1080)
    bitrate: str = "4000k"
    show_timer: bool = True
    show_compliance_bar: bool = True
    highlight_violations: bool = True
    violation_duration_sec: float = 3.0
    cover_enabled: bool = True
    cover_text: str = "StudyBuddy 学习记录"
    cover_image_path: str = ""           # 已废弃（新封面随机选自 assets/images/1-3.png），保留兼容
    bgm_path: str = ""
    bgm_volume: float = 0.12


@dataclass
class Sequence:
    """视频片段"""
    start_time: float
    end_time: float
    speed: float
    segment_type: str  # 'effective' | 'violation' | 'exit'
    violations: List[Dict] = field(default_factory=list)


class VideoClipper:
    """视频剪辑器 — 新流程：封面 → 主视频PIP → 总结封底 → 黑屏关键词"""

    def __init__(self, config: ClipConfig = None):
        self.config = config or ClipConfig()

    # ──────────────────────────────────────────────
    # 主入口
    # ──────────────────────────────────────────────

    def clip(
        self,
        raw_video_path: Path,
        output_path: Path,
        posture_events: List[Dict],
        score_report: dict,
        best_frame_path: str = None,
        progress_callback: callable = None,
        mobile_video_path: str = "",
    ) -> Path:
        """
        剪辑主流程（新流程）
        """
        try:
            from moviepy import VideoFileClip
            import imageio_ffmpeg
        except ImportError as e:
            logger.error(f"MoviePy 导入失败: {e}")
            raise

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        has_mobile = bool(mobile_video_path and os.path.exists(mobile_video_path))

        # 确定主视频源和 PIP 源
        if has_mobile:
            main_video_path = Path(mobile_video_path)
            pip_video_path = Path(raw_video_path)
        else:
            main_video_path = Path(raw_video_path)
            pip_video_path = None

        self._report("加载视频...", 0, progress_callback)
        main_clip = VideoFileClip(str(main_video_path))
        duration = main_clip.duration
        resolution = main_clip.size
        logger.info(f"主视频: {duration:.1f}s, {resolution}, 手机端={'是' if has_mobile else '否'}")

        # BGM 自动选择 - 默认使用 NIGHT DANCER
        if not self.config.bgm_path:
            default_bgm = Path(__file__).resolve().parent.parent.parent / "assets" / "audio" / "NIGHT DANCER - imase.mp3"
            if default_bgm.exists():
                self.config.bgm_path = str(default_bgm)
                logger.info(f"自动选择 BGM: {self.config.bgm_path}")
            else:
                # 回退到按等级选择
                if isinstance(score_report, dict):
                    grade = score_report.get("grade", "B")
                else:
                    grade = getattr(score_report, "grade", "B")
                self.config.bgm_path = self._select_bgm_by_grade(grade)
                if self.config.bgm_path:
                    logger.info(f"回退 BGM(按等级): {self.config.bgm_path}")

        # 1. 划分片段
        self._report("分析违规片段...", 5, progress_callback)
        sequences = self._build_sequences(posture_events, duration)

        # 2. 逐段处理（倍速 + 违规标注）
        temp_dir = Path(tempfile.mkdtemp(prefix="sb_clip_"))
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()

        try:
            segment_files = self._process_segments(
                main_clip, sequences, temp_dir, progress_callback
            )

            # 3. FFmpeg 合成分段 → body.mp4
            self._report("合成主视频...", 85, progress_callback)
            body_path = self._concat_segments(ffmpeg, segment_files, temp_dir, "body.mp4")

            # 4. 如果有手机端：PIP 合成（PC 视频叠加到主视频右下角）
            if has_mobile and pip_video_path and pip_video_path.exists():
                self._report("合成画中画...", 90, progress_callback)
                pip_body_path = self._overlay_pip(
                    ffmpeg, body_path, pip_video_path, resolution, temp_dir
                )
                if pip_body_path:
                    body_path = pip_body_path

            # 5. 生成封面页（1s）、总结封底（2s）、黑屏关键词（1s）
            self._report("生成封面与封底...", 92, progress_callback)
            cover_path = self._generate_cover_clip(ffmpeg, resolution, temp_dir, has_mobile)
            end_path = self._generate_end_clip(ffmpeg, resolution, score_report, temp_dir)
            black_path = self._make_keyword_black(ffmpeg, temp_dir, resolution)
            black_path = black_path or self._generate_silent_black(ffmpeg, resolution, temp_dir)

            # 6. 最终拼接：封面 + 主视频 + 封底 + 黑屏，混入 BGM
            self._report("最终合成 + 混音...", 95, progress_callback)
            self._final_concat_with_bgm(
                ffmpeg,
                cover_path=cover_path,
                body_path=body_path,
                end_path=end_path,
                black_path=black_path,
                output_path=output_path,
                progress_callback=progress_callback,
            )

        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

        # 清理
        main_clip.close()

        self._report("完成", 100, progress_callback)
        logger.info(f"剪辑完成: {output_path}")
        return output_path

    # ──────────────────────────────────────────────
    # 1. 分段处理（MoviePy）
    # ──────────────────────────────────────────────

    def _process_segments(
        self,
        main_clip,
        sequences: List[Sequence],
        temp_dir: Path,
        progress_callback,
    ) -> List[Path]:
        """用 MoviePy 逐段处理（倍速 + 违规标注）并导出为临时 MP4"""
        duration = main_clip.duration
        epsilon = 0.05
        segment_files = []

        for i, seq in enumerate(sequences):
            start_t = max(0, min(seq.start_time, duration - epsilon))
            end_t = max(0, min(seq.end_time, duration - epsilon))
            if end_t <= start_t + 0.01:
                continue

            pct = 5 + int(80 * i / max(1, len(sequences)))
            self._report(f"处理片段 {i+1}/{len(sequences)} ({seq.segment_type})", pct, progress_callback)

            sub = main_clip.subclipped(start_t, end_t)

            if seq.speed != 1.0:
                sub = sub.with_speed_scaled(seq.speed)

            has_overlay = self.config.highlight_violations and seq.violations
            if has_overlay:
                sub = self._add_violation_overlay(sub, seq)

            temp_path = temp_dir / f"seg_{i:04d}.mp4"
            success = self._safe_write_videofile(sub, temp_path, seq, i)

            if not success and has_overlay:
                # FFmpeg 处理带标注的片段失败，回退到无标注版本重试
                logger.warning(
                    f"片段 {i+1}（带标注）写入失败，尝试回退到无标注版本"
                )
                sub.close()
                sub = main_clip.subclipped(start_t, end_t)
                if seq.speed != 1.0:
                    sub = sub.with_speed_scaled(seq.speed)
                success = self._safe_write_videofile(sub, temp_path, seq, i)

            if success:
                segment_files.append(temp_path)
            else:
                logger.error(f"片段 {i+1} 处理彻底失败，跳过")
                # 生成一个静音黑屏占位，避免后续拼接时 concat 列表不连续
                placeholder = self._generate_placeholder_segment(temp_dir, i, sub)
                if placeholder:
                    segment_files.append(placeholder)
            sub.close()

        return segment_files

    def _safe_write_videofile(self, clip, temp_path: Path, seq: Sequence, idx: int) -> bool:
        """安全写入视频片段，捕获 FFmpeg 进程异常并返回成功/失败"""
        import shutil

        # 写入前检查磁盘空间
        free_bytes = shutil.disk_usage(temp_path.parent).free
        if free_bytes < 500 * 1024 * 1024:  # 不足 500MB
            logger.error(
                f"磁盘空间不足: 仅剩 {free_bytes / 1024 / 1024:.0f}MB，"
                f"无法写入片段 {idx + 1} ({seq.segment_type})"
            )
            return False

        try:
            clip.write_videofile(
                str(temp_path),
                fps=self.config.output_fps,
                codec="libx264",
                audio_codec="aac",
                preset="ultrafast",
                logger=None,
            )
            return True
        except AttributeError as e:
            # 捕获 'NoneType' object has no attribute 'stdout' 等 FFmpeg 进程异常
            logger.error(
                f"片段 {idx + 1} ({seq.segment_type}) FFmpeg 进程异常: {e}\n"
                f"  时间范围: {seq.start_time:.1f}s - {seq.end_time:.1f}s, "
                f"倍速: {seq.speed}x, "
                f"标注: {'是' if seq.violations else '否'}, "
                f"临时目录: {temp_path.parent}"
            )
            # 清理可能产生的半成品文件
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
            return False
        except Exception as e:
            logger.error(
                f"片段 {idx + 1} ({seq.segment_type}) 写入失败: {type(e).__name__}: {e}"
            )
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except Exception:
                    pass
            return False

    def _generate_placeholder_segment(
        self, temp_dir: Path, idx: int, ref_clip
    ) -> Optional[Path]:
        """生成静音黑屏占位片段，避免 concat 列表缺位"""
        import imageio_ffmpeg

        try:
            w, h = ref_clip.size
            dur = max(0.1, ref_clip.duration)
        except Exception:
            w, h = self.config.output_resolution
            dur = 1.0

        placeholder = temp_dir / f"seg_{idx:04d}_placeholder.mp4"
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        try:
            cmd = [
                ffmpeg, "-y",
                "-f", "lavfi",
                "-i", f"color=c=black:s={w}x{h}:d={dur}:r={self.config.output_fps}",
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-crf", "18",
                "-pix_fmt", "yuv420p",
                "-an",
                str(placeholder),
            ]
            subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=True)
            logger.warning(f"已生成占位片段: {placeholder}")
            return placeholder
        except Exception as e:
            logger.error(f"占位片段生成失败: {e}")
            return None

    # ──────────────────────────────────────────────
    # 2. FFmpeg 拼接分段
    # ──────────────────────────────────────────────

    def _concat_segments(
        self, ffmpeg: str, segment_files: List[Path], temp_dir: Path, output_name: str
    ) -> Path:
        """用 FFmpeg concat demuxer 拼接分段"""
        concat_list = temp_dir / "concat_body.txt"
        with open(concat_list, "w", encoding="utf-8") as f:
            for sf in segment_files:
                f.write(f"file '{sf.as_posix()}'\n")

        output = temp_dir / output_name
        cmd = [
            ffmpeg, "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-c", "copy",
            str(output),
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=True)
        logger.info(f"分段拼接完成: {output}")
        return output

    # ──────────────────────────────────────────────
    # 3. PIP 合成（手机端为主画面时）
    # ──────────────────────────────────────────────

    def _overlay_pip(
        self,
        ffmpeg: str,
        body_path: Path,
        pc_video_path: Path,
        main_resolution: Tuple[int, int],
        temp_dir: Path,
    ) -> Optional[Path]:
        """将 PC 端视频作为画中画叠加到主视频右下角。

        PIP 尺寸：宽度为主画面宽度的 1/2，高度按 PC 视频原始比例。
        位置：右下角对齐，PIP 右侧边和底边分别对齐主视频右侧边和底边。
        音频使用主视频原有轨道（手机端音频），丢弃 PC 端音频。
        """
        main_w, main_h = main_resolution

        # 获取 PC 视频的实际分辨率
        probe = subprocess.run(
            [ffmpeg, "-i", str(pc_video_path), "-f", "null", "-"],
            capture_output=True, text=True, timeout=30,
        )
        pc_w, pc_h = 640, 480
        for line in probe.stderr.split("\n"):
            if "Stream" in line and "Video:" in line:
                # 解析类似 "1280x720" 的分辨率
                import re
                m = re.search(r"(\d{2,})x(\d{2,})", line)
                if m:
                    pc_w, pc_h = int(m.group(1)), int(m.group(2))
                    break

        # PIP 宽度 = 主画面宽度的 1/2，高度按 PC 视频比例
        pip_w = max(80, main_w // 2)
        pip_h = max(60, int(pip_w * pc_h / pc_w))

        # 右下角对齐
        pip_x = main_w - pip_w
        pip_y = main_h - pip_h

        logger.info(
            f"PIP 参数: main={main_w}x{main_h}, pip={pip_w}x{pip_h}, "
            f"pos=({pip_x},{pip_y}), PC原始={pc_w}x{pc_h}"
        )

        # 获取两个视频的时长，取较短者
        def _get_duration(p: Path) -> float:
            probe2 = subprocess.run(
                [ffmpeg, "-i", str(p), "-f", "null", "-"],
                capture_output=True, text=True, timeout=30,
            )
            for line in probe2.stderr.split("\n"):
                if "Duration:" in line:
                    parts = line.split("Duration:")[1].strip().split(",")[0]
                    h, m, s = parts.split(":")
                    return float(h) * 3600 + float(m) * 60 + float(s)
            return 0.0

        body_dur = _get_duration(body_path)
        pc_dur = _get_duration(pc_video_path)
        target_dur = min(body_dur, pc_dur) if body_dur > 0 and pc_dur > 0 else body_dur

        # 滤镜链：主视频直接使用（body 已是处理后的分辨率），
        # PC 视频缩放到 PIP 尺寸，叠加到右下角
        filter_complex = (
            f"[0:v]setpts=PTS-STARTPTS[main];"
            f"[1:v]scale={pip_w}:{pip_h},setpts=PTS-STARTPTS[pip];"
            f"[main][pip]overlay={pip_x}:{pip_y}:shortest=1[v]"
        )

        output = temp_dir / "body_pip.mp4"
        cmd = [
            ffmpeg, "-y",
            "-i", str(body_path),
            "-i", str(pc_video_path),
            "-filter_complex", filter_complex,
            "-map", "[v]",
            "-map", "0:a",          # 使用主视频（手机端）音频
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "128k",
            "-pix_fmt", "yuv420p",
            "-t", str(target_dur),
            str(output),
        ]

        logger.info(f"PIP FFmpeg 开始")
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            logger.error(f"PIP 合成失败: {proc.stderr[-500:]}")
            return None

        logger.info(f"PIP 合成完成: {output}")
        return output

    # ──────────────────────────────────────────────
    # 4. 封面页生成（Pillow 绘图 → FFmpeg 转视频）
    # ──────────────────────────────────────────────

    def _generate_cover_clip(
        self, ffmpeg: str, resolution: Tuple[int, int], temp_dir: Path, has_mobile: bool = False
    ) -> Path:
        """生成封面页：随机背景图 + 圆润手写风格文字，1 秒"""
        png_path = temp_dir / "cover.png"
        self._render_cover_image(resolution, png_path, has_mobile)

        output = temp_dir / "cover.mp4"
        cmd = [
            ffmpeg, "-y",
            "-loop", "1", "-i", str(png_path),
            "-c:v", "libx264",
            "-t", "1",
            "-pix_fmt", "yuv420p",
            "-r", str(self.config.output_fps),
            str(output),
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)
        logger.info(f"封面页生成: {output}")
        return output

    def _render_cover_image(self, resolution: Tuple[int, int], output_path: Path, has_mobile: bool = False):
        """用 Pillow 绘制封面图片"""
        from PIL import Image, ImageDraw, ImageFont

        w, h = resolution

        # 随机选取背景：有手机端用 1/2/3.png，无手机端用 4/5/6.png
        asset_dir = Path(__file__).resolve().parent.parent.parent / "assets" / "images"
        bg_numbers = ["1.png", "2.png", "3.png"] if has_mobile else ["4.png", "5.png", "6.png"]
        bg_candidates = [asset_dir / n for n in bg_numbers]
        bg_path = random.choice([p for p in bg_candidates if p.exists()] or bg_candidates)
        try:
            bg = Image.open(bg_path).convert("RGB").resize((w, h), Image.LANCZOS)
        except Exception:
            bg = Image.new("RGB", (w, h), (255, 248, 240))

        draw = ImageDraw.Draw(bg)

        # 加载圆润字体
        font_path = _find_font(prefer_rounded=True)

        def _load_font(size: int) -> ImageFont.FreeTypeFont:
            try:
                return ImageFont.truetype(font_path, size)
            except Exception:
                return ImageFont.load_default()

        cover_text = self.config.cover_text or "StudyBuddy 学习记录"

        # ── 文字布局 ──
        # 前置小字 "欢迎收看"（约 60-80px @ 1080×1920 竖屏）
        pre_text = "欢迎收看"
        pre_font = _load_font(max(28, h // 28))
        pre_bbox = draw.textbbox((0, 0), pre_text, font=pre_font)
        pre_tw = pre_bbox[2] - pre_bbox[0]
        pre_x = (w - pre_tw) // 2
        pre_y = h // 3 - 10

        # 主标题（封面文案，约 120-150px @ 1080×1920 竖屏）
        main_font = _load_font(max(48, h // 14))
        main_bbox = draw.textbbox((0, 0), cover_text, font=main_font)
        main_tw = main_bbox[2] - main_bbox[0]
        main_th = main_bbox[3] - main_bbox[1]
        main_x = (w - main_tw) // 2
        main_y = pre_y + (h // 8)

        # 英文配套（约 40-50px）
        eng_text = "studybuddy learning journal"
        eng_font = _load_font(max(20, h // 42))
        eng_bbox = draw.textbbox((0, 0), eng_text, font=eng_font)
        eng_tw = eng_bbox[2] - eng_bbox[0]
        eng_x = (w - eng_tw) // 2
        eng_y = main_y + main_th + h // 16

        # ── 绘制（投影 + 主体）──
        shadow_offset = max(2, h // 200)
        shadow_color = (180, 180, 180, 80)  # 浅灰
        fill_color = (255, 255, 255)        # 纯白

        for (text, font, tx, ty) in [
            (pre_text, pre_font, pre_x, pre_y),
            (cover_text, main_font, main_x, main_y),
            (eng_text, eng_font, eng_x, eng_y),
        ]:
            # 浅灰外投影（多层叠加模拟柔和投影 + 简易描边）
            for sd in range(3, 0, -1):
                offset = shadow_offset * sd
                # RGBA 模式需要转换
                bg_rgba = bg.convert("RGBA")
                overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
                overlay_draw = ImageDraw.Draw(overlay)
                alpha = 60 // sd
                overlay_draw.text(
                    (tx + offset, ty + offset),
                    text,
                    font=font,
                    fill=(180, 180, 180, alpha),
                )
                bg_rgba = Image.alpha_composite(bg_rgba, overlay)
                bg = bg_rgba.convert("RGB")
                draw = ImageDraw.Draw(bg)

            # 主体文字（白色填充）
            draw.text((tx, ty), text, font=font, fill=fill_color)

        bg.save(output_path, "PNG")
        logger.info(f"封面图片已渲染: {output_path} (背景={bg_path.name})")

    # ──────────────────────────────────────────────
    # 5. 总结封底生成
    # ──────────────────────────────────────────────

    def _generate_end_clip(
        self,
        ffmpeg: str,
        resolution: Tuple[int, int],
        score_report,
        temp_dir: Path,
    ) -> Path:
        """生成总结封底：评分奖章 + 鼓励话 + 感谢观看 + 祝福话，2 秒"""
        png_path = temp_dir / "end.png"
        self._render_end_image(resolution, score_report, png_path)

        output = temp_dir / "end.mp4"
        cmd = [
            ffmpeg, "-y",
            "-loop", "1", "-i", str(png_path),
            "-c:v", "libx264",
            "-t", "2",
            "-pix_fmt", "yuv420p",
            "-r", str(self.config.output_fps),
            str(output),
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)
        logger.info(f"总结封底生成: {output}")
        return output

    def _render_end_image(self, resolution: Tuple[int, int], score_report, output_path: Path):
        """用 Pillow 绘制总结封底图片"""
        from PIL import Image, ImageDraw, ImageFont
        import numpy as np

        w, h = resolution

        def _get(key, default=0):
            if hasattr(score_report, key):
                return getattr(score_report, key)
            if isinstance(score_report, dict):
                return score_report.get(key, default)
            return default

        total = _get("total_score", 0)
        grade = _get("grade", "B")

        # 暖色渐变背景
        top_color = (255, 248, 240)
        bot_color = (255, 228, 209)
        bg = Image.new("RGB", (w, h))
        for y in range(h):
            ratio = y / h
            r = int(top_color[0] * (1 - ratio) + bot_color[0] * ratio)
            g = int(top_color[1] * (1 - ratio) + bot_color[1] * ratio)
            b = int(top_color[2] * (1 - ratio) + bot_color[2] * ratio)
            for x in range(w):
                bg.putpixel((x, y), (r, g, b))

        draw = ImageDraw.Draw(bg)
        font_path = _find_font(prefer_rounded=True)

        def _load_font(size: int) -> ImageFont.FreeTypeFont:
            try:
                return ImageFont.truetype(font_path, size)
            except Exception:
                return ImageFont.load_default()

        # 鼓励语映射
        encouragements = {
            "A+": "太棒了！坐姿小达人！",
            "A-": "表现优秀！继续保持！",
            "A": "表现优秀！继续保持！",
            "B": "不错哦，下次可以更好！",
        }
        encourage = encouragements.get(grade, "继续加油！")

        # ── 绘制文字 ──
        def _center_text(text, font, y, fill=(60, 60, 60)):
            bbox = draw.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
            x = (w - tw) // 2
            draw.text((x, y), text, font=font, fill=fill)

        # 等级徽章区域（用大号文字模拟）
        # 字号适配 1080×1920 竖屏：分母基于高度 h=1920，翻倍字号
        grade_font = _load_font(max(48, h // 10))
        grade_colors = {"A+": "#34C759", "A-": "#66BB6A", "A": "#34C759",
                        "B": "#0A84FF"}
        grade_color = grade_colors.get(grade, "#E8753A")

        # 大号评分
        score_text = f"{total:.0f} 分"
        score_font = _load_font(max(48, h // 10))
        _center_text(score_text, score_font, h // 5, fill=grade_color)

        # 等级
        grade_text = f"等级 {grade}"
        grade_display_font = _load_font(max(28, h // 20))
        _center_text(grade_text, grade_display_font, h * 2 // 5, fill="#E8753A")

        # 鼓励话
        encourage_font = _load_font(max(24, h // 22))
        _center_text(encourage, encourage_font, h // 2, fill="#333333")

        # 感谢观看
        thanks_font = _load_font(max(22, h // 26))
        _center_text("感谢观看", thanks_font, h * 2 // 3, fill="#666666")

        # 祝福话
        blessings = [
            "继续加油，下次更棒！",
            "保持好习惯，天天向上！",
            "今天的努力，明天的收获！",
        ]
        blessing = random.choice(blessings)
        bless_font = _load_font(max(18, h // 32))
        _center_text(blessing, bless_font, h * 3 // 4, fill="#888888")

        bg.save(output_path, "PNG")
        logger.info(f"总结封底图片已渲染: {output_path}")

    # ──────────────────────────────────────────────
    # 6. 黑屏 + 关键词
    # ──────────────────────────────────────────────

    def _generate_silent_black(
        self, ffmpeg: str, resolution: Tuple[int, int], temp_dir: Path
    ) -> Path:
        """生成 1 秒静音黑屏（当 keywords.txt 不存在时作为占位）"""
        w, h = resolution
        output = temp_dir / "silent_black.mp4"
        cmd = [
            ffmpeg, "-y",
            "-f", "lavfi",
            "-i", f"color=c=black:s={w}x{h}:d=1:r={self.config.output_fps}",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-an",
            str(output),
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True)
        return output

    # ──────────────────────────────────────────────
    # 7. 最终拼接 + BGM 混音
    # ──────────────────────────────────────────────

    def _final_concat_with_bgm(
        self,
        ffmpeg: str,
        cover_path: Path,
        body_path: Path,
        end_path: Path,
        black_path: Path,
        output_path: Path,
        progress_callback=None,
    ):
        """最终拼接：封面 + 主体 + 封底 + 黑屏，同时混入 BGM"""
        concat_list = Path(tempfile.mkdtemp(prefix="sb_final_")) / "concat_final.txt"
        concat_list.parent.mkdir(parents=True, exist_ok=True)

        with open(concat_list, "w", encoding="utf-8") as f:
            f.write(f"file '{cover_path.as_posix()}'\n")
            f.write(f"file '{body_path.as_posix()}'\n")
            f.write(f"file '{end_path.as_posix()}'\n")
            f.write(f"file '{black_path.as_posix()}'\n")

        has_bgm = self.config.bgm_path and os.path.exists(self.config.bgm_path)

        self._report("合并所有片段...", 97, progress_callback)

        if has_bgm:
            # 先拼接所有视频片段
            pre_output = concat_list.parent / "pre_final.mp4"
            cmd1 = [
                ffmpeg, "-y",
                "-f", "concat", "-safe", "0", "-i", str(concat_list),
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", "23",
                "-an",
                "-pix_fmt", "yuv420p",
                str(pre_output),
            ]
            proc = subprocess.run(cmd1, capture_output=True, text=True, timeout=600)
            if proc.returncode != 0:
                logger.error(f"视频拼接失败: {proc.stderr[-500:]}")
                raise RuntimeError(f"FFmpeg 拼接失败 (code={proc.returncode})")

            # 获取视频时长用于 BGM 循环
            duration_probe = subprocess.run(
                [ffmpeg, "-i", str(pre_output), "-f", "null", "-"],
                capture_output=True, text=True, timeout=30,
            )
            total_dur = 10.0
            for line in duration_probe.stderr.split("\n"):
                if "Duration:" in line:
                    parts = line.split("Duration:")[1].strip().split(",")[0]
                    h, m, s = parts.split(":")
                    total_dur = float(h) * 3600 + float(m) * 60 + float(s)
                    break

            self._report("混入背景音乐...", 99, progress_callback)

            # BGM 作为唯一音轨（不混入麦克风录音）
            cmd2 = [
                ffmpeg, "-y",
                "-i", str(pre_output),
                "-i", self.config.bgm_path,
                "-filter_complex",
                f"[1:0]aloop=loop=-1:size=2e9,atrim=duration={total_dur:.2f},"
                f"volume={self.config.bgm_volume}[a]",
                "-map", "0:v",
                "-map", "[a]",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "128k",
                "-pix_fmt", "yuv420p",
                str(output_path),
            ]
            proc2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=600)
            if proc2.returncode != 0:
                logger.error(f"BGM 混音失败: {proc2.stderr[-500:]}")
                # 回退：使用无 BGM 版本
                import shutil
                shutil.move(str(pre_output), str(output_path))
            else:
                try:
                    import shutil
                    shutil.rmtree(concat_list.parent, ignore_errors=True)
                except Exception:
                    pass

        else:
            cmd = [
                ffmpeg, "-y",
                "-f", "concat", "-safe", "0", "-i", str(concat_list),
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", "23",
                "-an",
                "-pix_fmt", "yuv420p",
                str(output_path),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if proc.returncode != 0:
                logger.error(f"视频拼接失败: {proc.stderr[-500:]}")
                raise RuntimeError(f"FFmpeg 拼接失败 (code={proc.returncode})")
            try:
                import shutil
                shutil.rmtree(concat_list.parent, ignore_errors=True)
            except Exception:
                pass

        logger.info(f"最终合成完成: {output_path}")

    # ──────────────────────────────────────────────
    # 辅助方法
    # ──────────────────────────────────────────────

    def _select_bgm_by_grade(self, grade: str) -> str:
        """根据评分等级自动选择 BGM 文件路径"""
        asset_dir = Path(__file__).parent.parent.parent / "assets" / "audio"
        mapping = {
            "S": asset_dir / "bgm_01_happy.wav",
            "A+": asset_dir / "bgm_01_happy.wav",
            "A-": asset_dir / "bgm_01_happy.wav",
            "A": asset_dir / "bgm_01_happy.wav",
            "B": asset_dir / "bgm_02_warm.wav",
        }
        path = mapping.get(grade, asset_dir / "bgm_02_warm.wav")
        return str(path) if path.exists() else ""

    def _build_sequences(
        self, events: List[Dict], total_duration: float
    ) -> List[Sequence]:
        """根据违规事件划分片段。"""
        violation_window = self.config.violation_duration_sec

        if not events or total_duration <= 0:
            return [Sequence(0, total_duration, self.config.effective_speed, "effective")]

        # 时间戳归一化
        raw_timestamps = [ev.get("timestamp", 0) for ev in events if ev.get("timestamp") is not None]
        if raw_timestamps:
            max_raw_ts = max(raw_timestamps)
            min_raw_ts = min(raw_timestamps)
            if max_raw_ts > total_duration * 10 and max_raw_ts > 1_000_000_000:
                logger.info(f"检测到 Unix 时间戳，自动归一化")
                events = [
                    {**ev, "timestamp": max(0, ev.get("timestamp", 0) - min_raw_ts)}
                    for ev in events
                ]

        max_ts = total_duration + violation_window
        valid_events = [ev for ev in events if 0 <= ev.get("timestamp", 0) <= max_ts]
        if not valid_events:
            return [Sequence(0, total_duration, self.config.effective_speed, "effective")]

        intervals = set()
        for ev in valid_events:
            t = ev.get("timestamp", 0)
            start = max(0, t - violation_window)
            end = min(total_duration, t + violation_window)
            if start < end:
                intervals.add((round(start, 2), round(end, 2)))

        merge_gap = max(1.5 * violation_window, 2.0)
        merged = []
        for s, e in sorted(intervals):
            if merged and s <= merged[-1][1] + merge_gap:
                merged[-1] = (merged[-1][0], max(merged[-1][1], e))
            else:
                merged.append((s, e))

        sequences = []
        cursor = 0.0
        exit_threshold = 5.0

        for vs, ve in merged:
            gap = vs - cursor
            if gap > 0.05:
                gap_type = "exit" if gap > exit_threshold else "effective"
                gap_speed = self.config.exit_speed if gap_type == "exit" else self.config.effective_speed
                sequences.append(Sequence(cursor, vs, gap_speed, gap_type))
            seq_violations = [ev for ev in valid_events if vs - 0.5 <= ev.get("timestamp", 0) <= ve + 0.5]
            sequences.append(Sequence(vs, ve, self.config.violation_speed, "violation", seq_violations))
            cursor = ve

        tail_gap = total_duration - cursor
        if tail_gap > 0.05:
            tail_type = "exit" if tail_gap > exit_threshold else "effective"
            tail_speed = self.config.exit_speed if tail_type == "exit" else self.config.effective_speed
            sequences.append(Sequence(cursor, total_duration, tail_speed, tail_type))

        # 合并相邻同类型片段
        collapsed = []
        for seq in sequences:
            if collapsed and collapsed[-1].segment_type == seq.segment_type:
                collapsed[-1].end_time = seq.end_time
                if seq.violations:
                    collapsed[-1].violations.extend(seq.violations)
            else:
                collapsed.append(seq)
        sequences = collapsed

        # 硬上限保护
        MAX_SEQUENCES = 200
        if len(sequences) > MAX_SEQUENCES:
            logger.warning(f"片段数 {len(sequences)} 超过上限 {MAX_SEQUENCES}，强制合并")
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

    def _add_violation_overlay(self, clip, seq: Sequence):
        """在违规片段上叠加彩色边框和文字标注"""
        try:
            from moviepy import TextClip, CompositeVideoClip, ColorClip

            w, h = clip.size

            vtype = seq.violations[0].get("violation_type", "violation") if seq.violations else "violation"
            color_map = {
                "head_forward": (245, 158, 11),
                "head_tilt": (59, 130, 246),
                "body_tilt": (232, 117, 58),
                "too_close": (139, 92, 246),
                "lying_down": (239, 68, 68),
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

            border_size = 6
            # 用 ColorClip 模拟边框：创建略大的背景色块，视频居中放置
            border_bg = ColorClip(
                size=(w + border_size * 2, h + border_size * 2),
                color=border_color,
            ).with_duration(clip.duration)
            bordered = clip.with_position((border_size, border_size))

            tag_bg = ColorClip(size=(180, 52), color=border_color).with_duration(clip.duration).with_opacity(0.9)
            tag_bg = tag_bg.with_position(("center", 16))

            txt = TextClip(
                text=label,
                font_size=32,
                color="white",
                stroke_color="black",
                stroke_width=2,
                font=_CN_FONT,
            ).with_position(("center", 24)).with_duration(clip.duration)

            return CompositeVideoClip(
                [border_bg, bordered, tag_bg, txt],
                size=(w + border_size * 2, h + border_size * 2),
            )
        except Exception as e:
            logger.warning(f"违规标注叠加失败: {e}")
            return clip

    def _make_keyword_black(self, ffmpeg: str, temp_dir: Path, resolution: tuple = None) -> Optional[Path]:
        """生成 1 秒纯黑画面 + keywords.txt 文本叠加（透明度极低）"""
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

            if resolution is None:
                resolution = self.config.output_resolution
            w, h = int(resolution[0]), int(resolution[1])

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

    def _report(self, msg: str, pct: int, callback):
        logger.info(f"[剪辑进度 {pct}%] {msg}")
        if callback:
            try:
                callback(pct, msg)
            except Exception:
                pass
