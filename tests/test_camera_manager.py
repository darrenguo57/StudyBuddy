"""
CameraManager 模块单元测试
"""
import sys
import os
import unittest
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.camera_manager import CameraManager, CameraConfig


class TestCameraManager(unittest.TestCase):
    """测试 CameraManager 类（不依赖真实摄像头）"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.config = CameraConfig(
            index=0,
            resolution=(1280, 720),
            record_fps=15.0,
            preview_fps=30.0,
        )
        self.cam = CameraManager(self.config, Path(self.temp_dir))

    def tearDown(self):
        self.cam.release()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_initial_state(self):
        """TC-CM-01: 初始状态检查"""
        self.assertFalse(self.cam.is_connected)
        self.assertFalse(self.cam.is_recording)
        self.assertEqual(self.cam.recording_duration, 0.0)
        self.assertEqual(self.cam._frame_count, 0)

    def test_recording_dir_created(self):
        """TC-CM-02: 录制目录自动创建"""
        self.assertTrue(Path(self.temp_dir).exists())

    def test_config_properties(self):
        """TC-CM-03: 配置属性"""
        self.assertEqual(self.cam.config.resolution, (1280, 720))
        self.assertEqual(self.cam.config.record_fps, 15.0)

    def test_start_recording_without_preview_raises(self):
        """TC-CM-04: 未启动预览时开始录制应抛异常"""
        with self.assertRaises(RuntimeError):
            self.cam.start_recording()

    def test_pause_resume(self):
        """TC-CM-05: 暂停/恢复状态"""
        self.cam._recording = True
        self.cam.pause_recording()
        self.assertTrue(self.cam._paused)
        self.cam.resume_recording()
        self.assertFalse(self.cam._paused)

    def test_output_params(self):
        """TC-CM-06: 输出编码参数检查"""
        self.assertEqual(self.cam.config.record_fps, 15.0)
        self.assertEqual(self.cam.recording_dir, Path(self.temp_dir))


if __name__ == "__main__":
    unittest.main(verbosity=2)
