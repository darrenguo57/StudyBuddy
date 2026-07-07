"""
VoiceInteraction 模块单元测试
"""
import sys
import os
import unittest
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.voice_interaction import VoiceInteraction


class TestVoiceInteraction(unittest.TestCase):
    """测试 VoiceInteraction 类"""

    def setUp(self):
        self.voice = VoiceInteraction(config={
            "remind_cooldown": 30,
            "max_reminds_per_session": 10,
            "encourage_interval_minutes": 15,
        })

    def test_violation_phrases_exist(self):
        """TC-VI-01: 违规提醒模板存在"""
        for vtype in ["head_forward", "head_tilt", "body_tilt", "too_close", "lying_down"]:
            phrases = self.voice.VIOLATION_PHRASES.get(vtype)
            self.assertIsNotNone(phrases)
            self.assertGreater(len(phrases), 0)

    def test_encourage_phrases_exist(self):
        """TC-VI-02: 鼓励模板存在"""
        self.assertGreater(len(self.voice.ENCOURAGE_PHRASES), 0)

    def test_remind_violation(self):
        """TC-VI-03: 违规提醒选择模板"""
        # 不实际播放，仅验证不抛异常
        try:
            self.voice.remind_violation("head_forward")
        except Exception as e:
            self.fail(f"remind_violation 抛出异常: {e}")

    def test_encourage_interval(self):
        """TC-VI-04: 鼓励间隔配置"""
        self.assertEqual(self.voice.encourage_interval, 15 * 60)

    def test_encourage_respects_interval(self):
        """TC-VI-05: 鼓励遵守间隔"""
        now = time.time()
        self.voice._last_encourage_time = now
        # 刚鼓励过，不应再次鼓励
        self.voice.encourage(10)
        # 由于间隔未到，_last_encourage_time 应保持不变
        self.assertEqual(self.voice._last_encourage_time, now)

    def test_complete_format(self):
        """TC-VI-06: 完成语音格式化"""
        try:
            self.voice.complete(30, "A")
        except Exception as e:
            self.fail(f"complete 抛出异常: {e}")

    def test_reset(self):
        """TC-VI-07: 重置状态"""
        self.voice._last_encourage_time = time.time()
        self.voice.reset()
        self.assertEqual(self.voice._last_encourage_time, 0.0)

    def test_intro_phrase(self):
        """TC-VI-08: 引导语存在"""
        self.assertIsNotNone(self.voice.INTRO_PHRASE)
        self.assertGreater(len(self.voice.INTRO_PHRASE), 0)

    def test_start_phrase(self):
        """TC-VI-09: 开始鼓励语存在"""
        self.assertIsNotNone(self.voice.START_PHRASE)
        self.assertGreater(len(self.voice.START_PHRASE), 0)

    def test_camera_lost_phrase(self):
        """TC-VI-10: 摄像头丢失提醒存在"""
        self.assertIsNotNone(self.voice.CAMERA_LOST)
        self.assertGreater(len(self.voice.CAMERA_LOST), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
