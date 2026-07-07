"""
语音预生成脚本
一次性生成所有项目所需的语音播报音频文件（WAV格式），保存在 assets/audio/ 下。

使用策略（按优先级）：
  1. IndexTTS2 — 高质量中文TTS，需模型文件完整
  2. SAPI5 COM — Windows原生语音（SpFileStream 直接写 WAV）
  3. pyttsx3 — 跨平台回退

生成的文件被 src/core/audio_player.py 读取播放，项目运行时不再调用TTS引擎。

用法：
  python generate_audio.py              → 仅生成缺失的文件（默认）
  python generate_audio.py --all        → 强制重新生成全部文件
  python generate_audio.py --list       → 列出所有文本与文件状态
  python generate_audio.py --dry-run    → 预览缺失清单，不实际生成
"""

import sys
import os
import time
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("GenerateAudio")

# ── 路径 ──
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
AUDIO_DIR = PROJECT_DIR / "assets" / "audio"

# ═══════════════════════════════════════════════════════════════
#  全部文本定义（与 voice_interaction.py 严格同步，78 条）
# ═══════════════════════════════════════════════════════════════

STATIC_PHRASES = {
    "intro":        "请面向摄像头坐好，让我看看你的坐姿是不是标准",
    "start":        "很好，坐姿达标！现在可以开始写作业了，加油！",
    "camera_lost":  "咦，我怎么看不见你了呢？请坐回摄像头前哦",
}

VIOLATION_PHRASES = {
    "head_forward": [
        "小朋友，把头抬起来一点哦",
        "坐直坐直，不要低头啦",
        "脖子要伸直，抬头写作业哦",
    ],
    "head_tilt": [
        "头不要歪哦，摆正了写",
        "小脑袋放正一些",
        "把头摆正，像一棵小松树一样",
    ],
    "body_tilt": [
        "身体坐正哦，不要歪向一边",
        "把身体摆正来写作业吧",
        "腰背挺直，坐姿要端正哦",
    ],
    "too_close": [
        "距离屏幕太近了，往后靠一靠哦",
        "眼睛要离屏幕远一点",
        "离屏幕太近了，眼睛会近视的，往后坐",
    ],
    "lying_down": [
        "不可以趴在桌子上哦，快坐起来",
        "坐直了写作业，趴在桌上对眼睛不好",
        "快起来，趴着写作业对脊椎不好哦",
    ],
}

ALARM_PHRASES = {
    "head_forward": ["抬头！抬头！", "坐直！把头抬起来！"],
    "head_tilt":    ["头歪了！摆正！", "头！摆正！"],
    "body_tilt":    ["身体歪了！坐正！", "坐正！坐正！"],
    "too_close":    ["太近了！往后退！", "离远点！往后退！"],
    "lying_down":   ["坐起来！别趴着！", "快坐起来！立刻！"],
}

ENCOURAGE_TEMPLATES = [
    "已经坚持了{minutes}分钟了，非常棒，继续加油！",
    "坐姿保持得很好，给你点赞！",
    "专注写作业的你真帅！",
    "继续保持，你一定能按时完成！",
    "棒棒哒，已经完成一半啦，再加把劲！",
]

COMPLETE_PHRASE_TEMPLATE = "作业完成啦！你一共坚持了{minutes}分钟，坐姿表现{grade}！"

GRADE_TEXT_MAP = {
    "S": "超级优秀",
    "A": "非常棒",
    "B": "表现良好",
    "C": "还需要改进",
    "D": "要加油哦",
}

COMMON_MINUTES = [5, 15, 30, 45, 60]

# ══════════════════════════════════════════
#  引擎初始化
# ══════════════════════════════════════════

def init_indextts2():
    """尝试初始化 IndexTTS2"""
    index_tts_path = r"C:\Users\Administrator\index-tts"
    if index_tts_path not in sys.path:
        sys.path.insert(0, index_tts_path)
    try:
        from indextts.infer_v2 import IndexTTS2
        model_dir = str(PROJECT_DIR / "models" / "indextts_checkpoints")
        cfg_path = os.path.join(model_dir, "config.yaml")
        tts = IndexTTS2(cfg_path=cfg_path, model_dir=model_dir, use_fp16=False, device='cpu')
        logger.info("IndexTTS2 初始化成功")
        return tts
    except Exception as e:
        logger.warning(f"IndexTTS2 初始化失败: {e}")
        return None


def init_sapi5():
    """检查 SAPI5 是否可用"""
    try:
        import comtypes.client
        import pythoncom
        pythoncom.CoInitialize()
        try:
            engine = comtypes.client.CreateObject("SAPI.SpVoice")
            engine.Speak("", 3)
            logger.info("SAPI5 可用")
            return True
        finally:
            pythoncom.CoUninitialize()
    except Exception as e:
        logger.warning(f"SAPI5 不可用: {e}")
        return False


def init_pyttsx3():
    """检查 pyttsx3 是否可用"""
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.stop()
        del engine
        logger.info("pyttsx3 可用")
        return True
    except Exception as e:
        logger.warning(f"pyttsx3 不可用: {e}")
        return False


# ══════════════════════════════════════════
#  IndexTTS2 生成
# ══════════════════════════════════════════

def generate_with_indextts2(tts, text: str, output_path: Path):
    """使用 IndexTTS2 生成音频"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = str(output_path.with_suffix(".tmp.wav"))
    try:
        tts.infer(text=text, output_path=tmp_path)
        if os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 1000:
            import shutil
            shutil.move(tmp_path, str(output_path))
            return True
    except Exception as e:
        logger.warning(f"IndexTTS2 生成失败 [{output_path.name}]: {e}")
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    return False


# ══════════════════════════════════════════
#  SAPI5 生成（SpFileStream 直接写 WAV）
# ══════════════════════════════════════════

def generate_with_sapi5(text: str, output_path: Path):
    """使用 SAPI5 COM + SpFileStream 将语音保存为 WAV 文件"""
    import pythoncom
    import comtypes.client

    output_path.parent.mkdir(parents=True, exist_ok=True)
    pythoncom.CoInitialize()
    try:
        voice = comtypes.client.CreateObject("SAPI.SpVoice")
        # 选择中文语音
        voices = voice.GetVoices()
        for i in range(voices.Count):
            v = voices.Item(i)
            desc = v.GetDescription()
            if "Chinese" in desc or "ZH" in desc.upper() or "中文" in desc:
                voice.Voice = v
                break
        voice.Rate = 0
        voice.Volume = 100

        # SpFileStream 直接保存为 WAV
        stream = comtypes.client.CreateObject("SAPI.SpFileStream")
        from comtypes.gen import SpeechLib
        stream.Open(str(output_path), SpeechLib.SSFMCreateForWrite)
        voice.AudioOutputStream = stream
        voice.Speak(text)
        stream.Close()
        return True
    except Exception as e:
        logger.warning(f"SAPI5 生成失败 [{output_path.name}]: {e}")
        # 备用方法：SpMemoryStream
        try:
            voice = comtypes.client.CreateObject("SAPI.SpVoice")
            voices = voice.GetVoices()
            for i in range(voices.Count):
                v = voices.Item(i)
                desc = v.GetDescription()
                if "Chinese" in desc or "ZH" in desc.upper() or "中文" in desc:
                    voice.Voice = v
                    break
            voice.Rate = 0
            voice.Volume = 100
            mstream = comtypes.client.CreateObject("SAPI.SpMemoryStream")
            voice.AudioOutputStream = mstream
            voice.Speak(text)
            data = mstream.GetData()
            with open(str(output_path), "wb") as f:
                f.write(data)
            return True
        except Exception as e2:
            logger.warning(f"SAPI5 备用方法也失败 [{output_path.name}]: {e2}")
            return False
    finally:
        pythoncom.CoUninitialize()


# ══════════════════════════════════════════
#  pyttsx3 生成（最后回退）
# ══════════════════════════════════════════

def generate_with_pyttsx3(text: str, output_path: Path):
    """使用 pyttsx3 的 save_to_file 生成音频"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import pyttsx3
        engine = pyttsx3.init()
        try:
            voices = engine.getProperty('voices')
            for v in voices:
                if 'chinese' in v.name.lower() or 'zh' in v.id.lower():
                    engine.setProperty('voice', v.id)
                    break
            engine.setProperty('rate', 160)
            engine.setProperty('volume', 0.9)
            engine.save_to_file(text, str(output_path))
            engine.runAndWait()
            return True
        finally:
            engine.stop()
            del engine
    except Exception as e:
        logger.warning(f"pyttsx3 生成失败 [{output_path.name}]: {e}")
        return False


# ══════════════════════════════════════════
#  清单构建
# ══════════════════════════════════════════

def build_manifest():
    """构建全部 78 个 (文件名, 文本) 清单"""
    items = []

    # 1. 静态短语
    for key, text in STATIC_PHRASES.items():
        items.append((f"{key}.wav", text))

    # 2. 违规提醒 + 告警
    for vtype in VIOLATION_PHRASES:
        for idx, phrase in enumerate(VIOLATION_PHRASES[vtype]):
            items.append((f"violation_{vtype}_{idx}.wav", phrase))
        for idx, phrase in enumerate(ALARM_PHRASES[vtype]):
            items.append((f"alarm_{vtype}_{idx}.wav", phrase))

    # 3. 鼓励
    for minutes in COMMON_MINUTES:
        for idx, template in enumerate(ENCOURAGE_TEMPLATES):
            text = template.format(minutes=minutes)
            items.append((f"encourage_{minutes}_{idx}.wav", text))

    # 4. 完成
    for minutes in COMMON_MINUTES:
        for grade, grade_text in GRADE_TEXT_MAP.items():
            text = COMPLETE_PHRASE_TEMPLATE.format(minutes=minutes, grade=grade_text)
            items.append((f"complete_{grade}_{minutes}.wav", text))

    return items


def list_manifest(items):
    """列出所有条目及文件存在状态"""
    existing = 0
    missing = 0
    for filename, text in items:
        path = AUDIO_DIR / filename
        if path.exists() and path.stat().st_size > 1000:
            status = "✓"
            existing += 1
        else:
            status = "✗ MISSING"
            missing += 1
        logging.getLogger().handlers[0].setLevel(logging.WARNING)  # 抑制非关键日志
        print(f"  [{status}] {filename:40s} {text}")
    print(f"\n  存在: {existing}  缺失: {missing}  总计: {len(items)}")


# ══════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════

def generate_all(missing_only: bool = True):
    """生成所有语音文件

    Args:
        missing_only: True=仅生成缺失文件，False=强制全量重新生成
    """
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    items = build_manifest()
    total = len(items)
    success = 0
    skipped = 0
    failed = 0

    # ── 引擎检测 ──
    logger.info("=" * 50)
    logger.info("检测可用语音引擎...")
    logger.info("=" * 50)

    tts_engine = init_indextts2()
    sapi5_ok = False
    pyttsx3_ok = False

    if tts_engine is None:
        sapi5_ok = init_sapi5()
        if not sapi5_ok:
            pyttsx3_ok = init_pyttsx3()

    engine_name = "IndexTTS2" if tts_engine else ("SAPI5" if sapi5_ok else "pyttsx3" if pyttsx3_ok else "无可用引擎")
    logger.info(f"使用引擎: {engine_name}")

    if not tts_engine and not sapi5_ok and not pyttsx3_ok:
        logger.error("无可用语音引擎，无法生成音频文件")
        return False

    # ── 生成函数 ──
    def _do_generate(text: str, path: Path, filename: str) -> bool:
        nonlocal skipped
        if missing_only and path.exists() and path.stat().st_size > 1000:
            skipped += 1
            return True  # 视为"成功"（已存在）

        if tts_engine:
            return generate_with_indextts2(tts_engine, text, path)
        elif sapi5_ok:
            return generate_with_sapi5(text, path)
        elif pyttsx3_ok:
            return generate_with_pyttsx3(text, path)
        else:
            logger.warning(f"  无可用引擎，跳过: {filename}")
            return False

    # ── 逐条生成 ──
    logger.info(f"\n{'=' * 50}")
    logger.info(f"开始生成 {'(仅缺失)' if missing_only else '(全量)'} ...")
    logger.info("=" * 50)

    for idx, (filename, text) in enumerate(items, 1):
        path = AUDIO_DIR / filename
        if _do_generate(text, path, filename):
            success += 1
        else:
            failed += 1

    # ── 报告 ──
    logger.info("\n" + "=" * 50)
    logger.info(f"生成完成: 成功 {success}/{total}  跳过(已存在) {skipped}  失败 {failed}")
    logger.info(f"音频目录: {AUDIO_DIR}")
    logger.info("=" * 50)
    return failed == 0


# ══════════════════════════════════════════
#  入口
# ══════════════════════════════════════════

def main():
    logger.info("StudyBuddy 语音音频预生成工具")
    logger.info(f"输出目录: {AUDIO_DIR}")

    if "--list" in sys.argv or "--dry-run" in sys.argv:
        items = build_manifest()
        print(f"\n音频清单: {len(items)} 个文件\n")
        list_manifest(items)
        if "--dry-run" in sys.argv:
            print("\n(仅预览，未实际生成。去掉 --dry-run 以执行生成)")
        return

    missing_only = "--all" not in sys.argv and "--force" not in sys.argv
    ok = generate_all(missing_only=missing_only)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
