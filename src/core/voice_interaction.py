"""
语音交互模块 - 最终修复版
使用 Windows SAPI5 COM 直接调用（无弹窗），pyttsx3 作为跨平台回退
"""
import time
import random
import logging
import threading
import queue
from typing import Optional

logger = logging.getLogger(__name__)


class VoiceInteraction:
    """语音交互管理器 - SAPI5 + pyttsx3 双引擎"""

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
        "head_tilt": ["头歪了！摆正！", "头！摆正！"],
        "body_tilt": ["身体歪了！坐正！", "坐正！坐正！"],
        "too_close": ["太近了！往后退！", "离远点！往后退！"],
        "lying_down": ["坐起来！别趴着！", "快坐起来！立刻！"],
    }

    ENCOURAGE_PHRASES = [
        "已经坚持了{minutes}分钟了，非常棒，继续加油！",
        "坐姿保持得很好，给你点赞！",
        "专注写作业的你真帅！",
        "继续保持，你一定能按时完成！",
        "棒棒哒，已经完成一半啦，再加把劲！",
    ]

    INTRO_PHRASE = "请面向摄像头坐好，让我看看你的坐姿是不是标准"
    START_PHRASE = "很好，坐姿达标！现在可以开始写作业了，加油！"
    COMPLETE_PHRASE = "作业完成啦！你一共坚持了{minutes}分钟，坐姿表现{grade}！"
    CAMERA_LOST = "咦，我怎么看不见你了呢？请坐回摄像头前哦"

    def __init__(self, config: dict = None):
        self.config = config or {}
        self._last_encourage_time = 0.0
        self._speak_queue: queue.Queue = queue.Queue(maxsize=10)
        self._speak_worker: Optional[threading.Thread] = None
        self._speak_running = False
        self._engine_type: Optional[str] = None  # "sapi5" | "pyttsx3" | None
        self._start_worker()

    def _detect_engine(self) -> Optional[str]:
        """检测可用语音引擎（仅执行一次）"""
        if self._engine_type is not None:
            return self._engine_type

        # 1. 尝试 SAPI5 (Windows, 无弹窗)
        try:
            import comtypes.client
            import pythoncom
            pythoncom.CoInitialize()
            try:
                engine = comtypes.client.CreateObject("SAPI.SpVoice")
                engine.Speak("", 3)  # 空文本测试
                self._engine_type = "sapi5"
                logger.info("语音引擎: SAPI5")
                return "sapi5"
            finally:
                pythoncom.CoUninitialize()
        except Exception:
            pass

        # 2. 尝试 pyttsx3 (跨平台)
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.stop()
            del engine
            self._engine_type = "pyttsx3"
            logger.info("语音引擎: pyttsx3")
            return "pyttsx3"
        except Exception:
            pass

        self._engine_type = None
        logger.warning("无可用语音引擎")
        return None

    def _speak_with_engine(self, text: str):
        """使用检测到的引擎播报"""
        engine_type = self._detect_engine()
        if engine_type is None:
            logger.info(f"[语音] {text}")
            return

        try:
            if engine_type == "sapi5":
                self._speak_sapi5(text)
            elif engine_type == "pyttsx3":
                self._speak_pyttsx3(text)
        except Exception as e:
            logger.error(f"语音播报失败: {e}")

    def _speak_sapi5(self, text: str):
        """SAPI5 播报（异步模式，不阻塞）"""
        import comtypes.client
        import pythoncom
        pythoncom.CoInitialize()
        try:
            engine = comtypes.client.CreateObject("SAPI.SpVoice")
            # 选择中文语音
            voices = engine.GetVoices()
            for i in range(voices.Count):
                v = voices.Item(i)
                desc = v.GetDescription()
                if "Chinese" in desc or "ZH" in desc.upper() or "中文" in desc:
                    engine.Voice = v
                    break
            engine.Rate = 1
            engine.Volume = 80
            # 异步播报 (3 = SVSFPurgeBeforeSpeak | SVSFlagsAsync)
            engine.Speak(text, 3)
            # 等待完成
            while engine.Status.RunningState == 2:
                time.sleep(0.05)
        finally:
            pythoncom.CoUninitialize()

    def _speak_pyttsx3(self, text: str):
        """pyttsx3 播报（每次新建引擎，避免线程问题）"""
        import pyttsx3
        engine = pyttsx3.init()
        try:
            voices = engine.getProperty('voices')
            for voice in voices:
                if 'chinese' in voice.name.lower() or 'zh' in voice.id.lower():
                    engine.setProperty('voice', voice.id)
                    break
            engine.setProperty('rate', 180)
            engine.setProperty('volume', 0.8)
            engine.say(text)
            engine.runAndWait()
        finally:
            engine.stop()
            del engine

    def _start_worker(self):
        """启动语音工作线程"""
        if self._speak_running:
            return
        self._speak_running = True
        self._speak_worker = threading.Thread(target=self._speak_loop, daemon=True)
        self._speak_worker.start()

    def _speak_loop(self):
        """语音工作线程"""
        while self._speak_running:
            try:
                text = self._speak_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if text is None:
                break
            self._speak_with_engine(text)

    @property
    def encourage_interval(self) -> int:
        return self.config.get("encourage_interval_minutes", 15) * 60

    def speak(self, text: str):
        """语音播报（非阻塞，放入队列）"""
        try:
            self._speak_queue.put_nowait(text)
        except queue.Full:
            logger.warning("语音队列已满")

    def intro(self):
        self.speak(self.INTRO_PHRASE)

    def start_encourage(self):
        self.speak(self.START_PHRASE)

    def remind_violation(self, violation_type: str):
        phrases = self.VIOLATION_PHRASES.get(violation_type, ["请注意坐姿哦"])
        self.speak(random.choice(phrases))

    def alarm_violation(self, violation_type: str):
        phrases = self.ALARM_PHRASES.get(violation_type, ["坐好！坐好！"])
        self.speak(random.choice(phrases))

    def remind_camera_lost(self):
        self.speak(self.CAMERA_LOST)

    def encourage(self, minutes: int):
        now = time.time()
        if now - self._last_encourage_time >= self.encourage_interval:
            text = random.choice(self.ENCOURAGE_PHRASES).format(minutes=minutes)
            self.speak(text)
            self._last_encourage_time = now

    def complete(self, minutes: int, grade: str):
        grade_text = {"S": "超级优秀", "A": "非常棒", "B": "表现良好",
                       "C": "还需要改进", "D": "要加油哦"}.get(grade, "不错")
        text = self.COMPLETE_PHRASE.format(minutes=int(minutes), grade=grade_text)
        self.speak(text)

    def reset(self):
        self._last_encourage_time = 0.0

    def release(self):
        self._speak_running = False
        try:
            self._speak_queue.put_nowait(None)
        except queue.Full:
            pass
        if self._speak_worker and self._speak_worker.is_alive():
            self._speak_worker.join(timeout=2.0)
