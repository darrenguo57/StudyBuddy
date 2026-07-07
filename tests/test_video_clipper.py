"""
VideoClipper 模块单元测试
"""
import sys
import os
import unittest
import tempfile
import shutil
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.video_clipper import VideoClipper, ClipConfig, Sequence


class TestVideoClipper(unittest.TestCase):
    """测试 VideoClipper 类"""

    def setUp(self):
        self.config = ClipConfig(
            effective_speed=4.0,
            violation_speed=2.0,
            exit_speed=16.0,
            output_fps=30,
            bitrate="4000k",
        )
        self.clipper = VideoClipper(config=self.config)

    def test_build_sequences_no_events(self):
        """TC-VC-01: 无违规事件时全程有效段"""
        sequences = self.clipper._build_sequences([], 120)
        self.assertEqual(len(sequences), 1)
        self.assertEqual(sequences[0].segment_type, "effective")
        self.assertEqual(sequences[0].speed, 4.0)
        self.assertEqual(sequences[0].start_time, 0)
        self.assertEqual(sequences[0].end_time, 120)

    def test_build_sequences_with_violation(self):
        """TC-VC-02: 有违规时划分有效段和违规段"""
        events = [{"timestamp": 30}]
        sequences = self.clipper._build_sequences(events, 120)
        types = [s.segment_type for s in sequences]
        self.assertIn("effective", types)
        self.assertIn("violation", types)

    def test_build_sequences_multiple_violations(self):
        """TC-VC-03: 多个违规事件合并区间"""
        events = [
            {"timestamp": 20},
            {"timestamp": 25},  # 与上一个重叠，应合并
            {"timestamp": 80},
        ]
        sequences = self.clipper._build_sequences(events, 120)
        # 期望: effective(0~17), violation(17~28), effective(28~77), violation(77~83), effective(83~120)
        violation_seqs = [s for s in sequences if s.segment_type == "violation"]
        self.assertEqual(len(violation_seqs), 2)

    def test_sequence_speeds(self):
        """TC-VC-04: 各段倍速正确"""
        events = [{"timestamp": 30}]
        sequences = self.clipper._build_sequences(events, 120)
        for s in sequences:
            if s.segment_type == "effective":
                self.assertEqual(s.speed, 4.0)
            elif s.segment_type == "violation":
                self.assertEqual(s.speed, 2.0)

    def test_violation_window(self):
        """TC-VC-05: 违规前后窗口"""
        events = [{"timestamp": 50}]
        sequences = self.clipper._build_sequences(events, 120)
        violation_seq = [s for s in sequences if s.segment_type == "violation"][0]
        # 默认 violation_duration_sec=3, 所以窗口是 47~53
        self.assertEqual(violation_seq.start_time, 47)
        self.assertEqual(violation_seq.end_time, 53)

    def test_config_defaults(self):
        """TC-VC-06: 默认配置"""
        c = ClipConfig()
        self.assertEqual(c.effective_speed, 4.0)
        self.assertEqual(c.violation_speed, 2.0)
        self.assertEqual(c.exit_speed, 16.0)
        self.assertEqual(c.output_fps, 30)

    def test_sequence_dataclass(self):
        """TC-VC-07: Sequence 数据类"""
        seq = Sequence(0, 10, 4.0, "effective", [])
        self.assertEqual(seq.start_time, 0)
        self.assertEqual(seq.end_time, 10)
        self.assertEqual(seq.speed, 4.0)
        self.assertEqual(seq.segment_type, "effective")


if __name__ == "__main__":
    unittest.main(verbosity=2)
