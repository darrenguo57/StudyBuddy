"""
PostureDetector 模块单元测试
"""
import sys
import os
import unittest
import time
import math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.posture_detector import PostureDetector, PostureState, PostureResult


class MockLandmark:
    """模拟 MediaPipe 关键点"""
    def __init__(self, x, y, z=0):
        self.x = x
        self.y = y
        self.z = z


class TestPostureDetector(unittest.TestCase):
    """测试 PostureDetector 类"""

    def setUp(self):
        # 使用默认配置，延迟加载 MediaPipe
        self.detector = PostureDetector.__new__(PostureDetector)
        self.detector.config = {}
        self.detector.missing_frame_count = 0
        self.detector.max_missing_frames = 100
        self.detector._state_history = []
        self.detector._history_size = 10
        self.detector._remind_cooldowns = {}
        self.detector._remind_count = 0
        self.detector._angle_buffer = {
            "head_forward": [],
            "head_tilt": [],
            "body_tilt": [],
            "nose_z": [],
        }
        self.detector._buffer_size = 5

    def _make_landmarks(self, **kwargs):
        """创建模拟关键点"""
        defaults = {
            "nose": (0.5, 0.3, -0.1),
            "left_ear": (0.4, 0.25, 0),
            "right_ear": (0.6, 0.25, 0),
            "left_shoulder": (0.4, 0.5, 0),
            "right_shoulder": (0.6, 0.5, 0),
            "left_hip": (0.4, 0.7, 0),
            "right_hip": (0.6, 0.7, 0),
        }
        defaults.update(kwargs)
        lm = []
        for i in range(33):  # MediaPipe 33关键点
            lm.append(MockLandmark(0.5, 0.5, 0))
        lm[0] = MockLandmark(*defaults["nose"])
        lm[7] = MockLandmark(*defaults["left_ear"])
        lm[8] = MockLandmark(*defaults["right_ear"])
        lm[11] = MockLandmark(*defaults["left_shoulder"])
        lm[12] = MockLandmark(*defaults["right_shoulder"])
        lm[23] = MockLandmark(*defaults["left_hip"])
        lm[24] = MockLandmark(*defaults["right_hip"])
        return lm

    def test_normal_posture(self):
        """TC-PD-01: 标准坐姿应无违规"""
        # 鼻子在肩中点上方(y更小)，z不深，头不歪，肩平
        lm = self._make_landmarks(
            nose=(0.5, 0.2, -0.05),
            left_ear=(0.45, 0.18, 0),
            right_ear=(0.55, 0.18, 0),
            left_shoulder=(0.4, 0.5, 0),
            right_shoulder=(0.6, 0.5, 0),
        )
        result = self.detector._analyze_posture(lm)
        self.assertTrue(result.compliance)
        self.assertEqual(len(result.violations), 0)

    def test_head_forward(self):
        """TC-PD-02: 头部前倾检测"""
        # 鼻子向前(z变小)且向下(y变大)
        lm = self._make_landmarks(nose=(0.5, 0.6, -0.3))
        result = self.detector._analyze_posture(lm)
        types = [v.violation_type for v in result.violations]
        self.assertIn("head_forward", types)

    def test_head_tilt(self):
        """TC-PD-03: 歪头检测"""
        lm = self._make_landmarks(left_ear=(0.4, 0.2, 0), right_ear=(0.6, 0.35, 0))
        result = self.detector._analyze_posture(lm)
        types = [v.violation_type for v in result.violations]
        self.assertIn("head_tilt", types)

    def test_body_tilt(self):
        """TC-PD-04: 身体倾斜检测"""
        lm = self._make_landmarks(left_shoulder=(0.4, 0.4, 0), right_shoulder=(0.6, 0.55, 0))
        result = self.detector._analyze_posture(lm)
        types = [v.violation_type for v in result.violations]
        self.assertIn("body_tilt", types)

    def test_too_close(self):
        """TC-PD-05: 距离过近检测"""
        lm = self._make_landmarks(nose=(0.5, 0.3, -0.2))
        result = self.detector._analyze_posture(lm)
        types = [v.violation_type for v in result.violations]
        self.assertIn("too_close", types)

    def test_lying_down(self):
        """TC-PD-06: 趴桌检测"""
        # 鼻子y > 肩中点y
        lm = self._make_landmarks(nose=(0.5, 0.55, -0.1), left_shoulder=(0.4, 0.5, 0), right_shoulder=(0.6, 0.5, 0))
        result = self.detector._analyze_posture(lm)
        types = [v.violation_type for v in result.violations]
        self.assertIn("lying_down", types)

    def test_state_machine_normal(self):
        """TC-PD-07: 状态机 - 正常状态"""
        result = PostureResult()
        result.compliance = True
        self.detector._update_state(result)
        self.assertEqual(result.state, PostureState.NORMAL)

    def test_state_machine_warning(self):
        """TC-PD-08: 状态机 - Warning状态"""
        result = PostureResult()
        result.compliance = False
        result.violations = [type('V', (), {'severity': 'warning'})()]
        # 连续3帧
        for _ in range(3):
            self.detector._update_state(result)
        self.assertEqual(result.state, PostureState.WARNING)

    def test_state_machine_critical(self):
        """TC-PD-09: 状态机 - Critical状态"""
        result = PostureResult()
        result.compliance = False
        result.violations = [type('V', (), {'severity': 'critical'})()]
        for _ in range(3):
            self.detector._update_state(result)
        self.assertEqual(result.state, PostureState.CRITICAL)

    def test_remind_cooldown(self):
        """TC-PD-10: 提醒冷却机制"""
        self.detector.reset_session()
        self.assertTrue(self.detector.should_remind("head_forward"))
        self.assertFalse(self.detector.should_remind("head_forward"))  # 冷却中

    def test_max_reminds(self):
        """TC-PD-11: 最大提醒次数"""
        self.detector.reset_session()
        self.detector._remind_count = self.detector.max_reminds
        self.assertFalse(self.detector.should_remind("head_forward"))

    def test_face_missing(self):
        """TC-PD-12: 人脸丢失检测"""
        self.detector.missing_frame_count = 0
        self.assertFalse(self.detector.is_face_missing())
        self.detector.missing_frame_count = 150
        self.assertTrue(self.detector.is_face_missing())

    def test_reset_session(self):
        """TC-PD-13: 会话重置"""
        self.detector._state_history.append(PostureState.WARNING)
        self.detector._remind_count = 5
        self.detector.missing_frame_count = 50
        self.detector.reset_session()
        self.assertEqual(len(self.detector._state_history), 0)
        self.assertEqual(self.detector._remind_count, 0)
        self.assertEqual(self.detector.missing_frame_count, 0)

    def test_angle_between(self):
        """TC-PD-14: 角度计算工具
        _angle_between 计算的是两点连线与垂直线(y轴)的夹角。
        垂直方向(dy大, horizontal小) -> 角度接近0
        水平方向(dy=0, horizontal大) -> 角度90
        """
        # 垂直方向 -> 角度接近0
        angle = PostureDetector._angle_between((0, 0, 0), (0, 1, 0))
        self.assertAlmostEqual(angle, 0.0, places=1)

        # 水平方向 -> 角度90
        angle = PostureDetector._angle_between((0, 0, 0), (1, 0, 0))
        self.assertAlmostEqual(angle, 90.0, places=1)

        # 45度方向 (dx=1, dy=1)
        angle = PostureDetector._angle_between((0, 0, 0), (1, 1, 0))
        self.assertAlmostEqual(angle, 45.0, places=1)

        # 模拟标准坐姿角度（肩中点到鼻子）
        angle = PostureDetector._angle_between((0.5, 0.5, 0), (0.5, 0.2, -0.05))
        self.assertLess(angle, 25.0)  # 应小于阈值

    def test_midpoint(self):
        """TC-PD-15: 中点计算"""
        a = MockLandmark(0, 0, 0)
        b = MockLandmark(1, 2, 3)
        m = PostureDetector._midpoint(a, b)
        self.assertEqual(m.x, 0.5)
        self.assertEqual(m.y, 1.0)
        self.assertEqual(m.z, 1.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
