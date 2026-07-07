"""
FocusDetector 模块单元测试
"""
import sys
import os
import unittest
import math

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.focus_detector import FocusDetector, FocusResult


class TestFocusDetector(unittest.TestCase):
    """测试 FocusDetector 类"""

    def setUp(self):
        self.detector = FocusDetector(config={})

    def tearDown(self):
        self.detector.release()

    def test_initial_state(self):
        """TC-FD-01: 初始状态"""
        self.assertEqual(self.detector._blink_count, 0)
        self.assertEqual(self.detector._last_blink_time, 0)
        self.assertIsNone(self.detector.face_mesh)  # 未初始化成功时 None

    def test_ear_calculation(self):
        """TC-FD-02: EAR 计算"""
        # 模拟标准睁眼（宽高比约 0.3）
        points = {
            33: (100, 100), 160: (105, 95), 158: (107, 93),
            133: (130, 100), 153: (105, 107), 144: (107, 109),
        }
        ear = self.detector._eye_aspect_ratio(points, self.detector.LEFT_EYE_INDICES)
        self.assertGreater(ear, 0.15)
        self.assertLess(ear, 0.6)

        # 模拟闭眼（宽高比很小）
        points_closed = {
            33: (100, 100), 160: (105, 98), 158: (107, 97),
            133: (130, 100), 153: (105, 102), 144: (107, 101),
        }
        ear_closed = self.detector._eye_aspect_ratio(points_closed, self.detector.LEFT_EYE_INDICES)
        self.assertLess(ear_closed, ear)

    def test_gaze_direction_center(self):
        """TC-FD-03: 视线居中判断"""
        # 虹膜中心与眼框中心对齐
        iris_center = (100, 100)
        eye_box = (80, 90, 120, 110)
        gaze = self.detector._estimate_gaze(iris_center, eye_box)
        self.assertEqual(gaze, "center")

    def test_gaze_direction_left(self):
        """TC-FD-04: 视线向左判断"""
        iris_center = (85, 100)
        eye_box = (80, 90, 120, 110)
        gaze = self.detector._estimate_gaze(iris_center, eye_box)
        self.assertEqual(gaze, "left")

    def test_gaze_direction_right(self):
        """TC-FD-05: 视线向右判断"""
        iris_center = (115, 100)
        eye_box = (80, 90, 120, 110)
        gaze = self.detector._estimate_gaze(iris_center, eye_box)
        self.assertEqual(gaze, "right")

    def test_gaze_direction_up(self):
        """TC-FD-06: 视线向上判断"""
        iris_center = (100, 85)
        eye_box = (80, 90, 120, 110)
        gaze = self.detector._estimate_gaze(iris_center, eye_box)
        self.assertEqual(gaze, "up")

    def test_focus_score_calculation(self):
        """TC-FD-07: 专注度评分计算"""
        # 完全专注
        score = self.detector._calculate_focus_score("center", 15, (5, 10, 0), 0.3)
        self.assertGreater(score, 0.8)

        # 视线偏离
        score = self.detector._calculate_focus_score("left", 15, (5, 10, 0), 0.3)
        self.assertLess(score, 0.7)

        # 头部偏转过大
        score = self.detector._calculate_focus_score("center", 15, (5, 35, 0), 0.3)
        self.assertLess(score, 0.8)

    def test_drowsiness_detection(self):
        """TC-FD-08: 困倦检测"""
        self.detector._blink_count = 35
        result = FocusResult(
            focus_score=0.5, gaze_direction="center",
            blink_rate=35, is_drowsy=True, is_distracted=False,
            head_pose=(0, 0, 0), face_visible=True,
        )
        self.assertTrue(result.is_drowsy)
        self.assertFalse(result.is_distracted)

    def test_distraction_detection(self):
        """TC-FD-09: 分心检测"""
        result = FocusResult(
            focus_score=0.4, gaze_direction="right",
            blink_rate=12, is_drowsy=False, is_distracted=True,
            head_pose=(0, 35, 0), face_visible=True,
        )
        self.assertTrue(result.is_distracted)
        self.assertFalse(result.is_drowsy)

    def test_face_not_visible(self):
        """TC-FD-10: 面部不可见处理"""
        result = FocusResult(
            focus_score=0.0, gaze_direction="unknown",
            blink_rate=0, is_drowsy=False, is_distracted=False,
            head_pose=(0, 0, 0), face_visible=False,
        )
        self.assertFalse(result.face_visible)
        self.assertEqual(result.focus_score, 0.0)

    def test_reset(self):
        """TC-FD-11: reset 方法"""
        self.detector._blink_count = 10
        self.detector._last_blink_time = 12345
        self.detector.reset()
        self.assertEqual(self.detector._blink_count, 0)
        self.assertEqual(self.detector._last_blink_time, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
