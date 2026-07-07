"""
音频播放模块 - QSoundEffect 驱动，绕过 FFmpeg 探测缺陷

QSoundEffect 直接走 WASAPI（Windows），无 FFmpeg 后端 probe buffer 问题，
对预录制短 WAV 文件零缺陷。加载为同步操作，setSource+play 即时完成。
"""

import logging
import random
from pathlib import Path
from typing import Optional, List

from PyQt6.QtCore import QUrl, QObject, pyqtSignal
from PyQt6.QtMultimedia import QSoundEffect

logger = logging.getLogger(__name__)


class AudioPlayer(QObject):
    """预录制音频播放器 - QSoundEffect 驱动，稳定可靠"""

    playback_failed = pyqtSignal(str)

    def __init__(self, audio_dir: Optional[Path] = None, parent=None):
        super().__init__(parent)
        self._audio_dir = Path(audio_dir or (Path(__file__).resolve().parent.parent.parent / "assets" / "audio"))
        self._volume = 0.8
        self._effect: Optional[QSoundEffect] = None

    def _get_effect(self) -> QSoundEffect:
        """懒加载 QSoundEffect"""
        if self._effect is None:
            self._effect = QSoundEffect(self)
            self._effect.setVolume(self._volume)
        return self._effect

    # ── 公共 API ──

    def intro(self):
        self._play("intro.wav")

    def start_encourage(self):
        self._play("start.wav")

    def remind_violation(self, violation_type: str):
        self._play_random_group("violation", violation_type, 0, 2)

    def alarm_violation(self, violation_type: str):
        self._play_random_group("alarm", violation_type, 0, 1)

    def remind_camera_lost(self):
        self._play("camera_lost.wav")

    def encourage(self, minutes: int):
        closest = self._closest_minutes(minutes, [5, 15, 30, 45, 60])
        self._play_random_group("encourage", str(closest), 0, 4)

    def complete(self, minutes: int, grade: str):
        grade = grade.upper() if grade and grade.upper() in "SABCD" else "A"
        closest = self._closest_minutes(minutes, [5, 15, 30, 45, 60])
        self._play(f"complete_{grade}_{closest}.wav")

    def reset(self):
        pass

    def release(self):
        if self._effect is not None:
            self._effect.stop()
            self._effect.deleteLater()
            self._effect = None

    def set_volume(self, vol: float):
        self._volume = max(0.0, min(1.0, vol))
        if self._effect is not None:
            self._effect.setVolume(self._volume)

    def speak(self, text: str):
        logger.info(f"[音频] 文本合成已禁用: {text[:30]}...")

    @property
    def encourage_interval(self) -> int:
        return 15 * 60

    # ── 内部实现 ──

    def _play(self, file_name: str):
        """同步播放 WAV 文件"""
        path = self._audio_dir / file_name
        if not path.exists():
            logger.warning(f"音频文件不存在: {path}")
            self.playback_failed.emit(file_name)
            return
        try:
            effect = self._get_effect()
            # 停止当前播放，立即加载并播放新音频
            effect.stop()
            url = QUrl.fromLocalFile(str(path))
            effect.setSource(url)
            effect.play()
            logger.debug(f"播放: {file_name}")
        except Exception as e:
            logger.error(f"播放失败 [{file_name}]: {e}")
            self.playback_failed.emit(file_name)

    def _play_random_group(self, prefix: str, key: str, lo: int, hi: int):
        """从指定组中随机选一条播放"""
        max_existing = lo
        for i in range(lo, hi + 1):
            if (self._audio_dir / f"{prefix}_{key}_{i}.wav").exists():
                max_existing = i
        idx = random.randint(lo, max_existing)
        file_name = f"{prefix}_{key}_{idx}.wav"
        self._play(file_name)

    @staticmethod
    def _closest_minutes(target: int, candidates: List[int]) -> int:
        return min(candidates, key=lambda x: abs(x - target))


def ensure_audio_exists(audio_dir: Optional[Path] = None) -> bool:
    """检测音频目录是否包含关键文件。"""
    audio_dir = Path(audio_dir or (Path(__file__).resolve().parent.parent.parent / "assets" / "audio"))
    required = ["intro.wav", "start.wav", "camera_lost.wav"]
    for name in required:
        if not (audio_dir / name).exists():
            return False
    return True
