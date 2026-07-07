"""
Database 模块单元测试
"""
import sys
import os
import unittest
import tempfile
import shutil
from pathlib import Path

# 将 src 加入路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.database import Database


class TestDatabase(unittest.TestCase):
    """测试 Database 类"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = Path(self.temp_dir) / "test.db"
        self.db = Database(self.db_path)

    def tearDown(self):
        self.db = None
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_init_creates_tables(self):
        """TC-DB-01: 初始化应创建所有表"""
        import sqlite3
        conn = sqlite3.connect(str(self.db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        expected = {"sessions", "posture_events", "monthly_scores",
                    "reward_redemptions", "parent_config"}
        self.assertTrue(expected.issubset(tables),
                        f"缺少表: {expected - tables}")

    def test_create_session(self):
        """TC-DB-02: 创建会话应返回正整数 ID"""
        sid = self.db.create_session("/tmp/test.mp4")
        self.assertIsInstance(sid, int)
        self.assertGreater(sid, 0)

        session = self.db.get_session(sid)
        self.assertIsNotNone(session)
        self.assertEqual(session["status"], "in_progress")
        self.assertEqual(session["raw_video_path"], "/tmp/test.mp4")

    def test_end_session(self):
        """TC-DB-03: 结束会话应正确更新评分"""
        sid = self.db.create_session()
        self.db.end_session(
            session_id=sid,
            duration_minutes=35.5,
            posture_rate=0.92,
            focus_rate=0.88,
            efficiency_score=85.0,
            correction_rate=0.75,
            total_score=82.5,
            grade="B",
            video_path="/tmp/review.mp4",
        )

        session = self.db.get_session(sid)
        self.assertEqual(session["status"], "completed")
        self.assertEqual(session["total_score"], 82.5)
        self.assertEqual(session["grade"], "B")
        self.assertEqual(session["video_path"], "/tmp/review.mp4")

    def test_posture_event(self):
        """TC-DB-04: 坐姿事件增删改查"""
        sid = self.db.create_session()
        eid = self.db.add_posture_event(
            session_id=sid,
            timestamp=100.5,
            violation_type="head_forward",
            severity="critical",
            reminded=True,
        )
        self.assertGreater(eid, 0)

        events = self.db.get_session_events(sid)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["violation_type"], "head_forward")

        self.db.mark_event_corrected(eid, corrected_time=5.0)
        events = self.db.get_session_events(sid)
        self.assertEqual(events[0]["corrected"], 1)
        self.assertEqual(events[0]["corrected_time"], 5.0)

    def test_monthly_score_update(self):
        """TC-DB-05: 月度积分自动汇总"""
        sid = self.db.create_session()
        self.db.end_session(sid, 30, 0.9, 0.9, 80, 0.8, 80, "B")

        score = self.db.get_monthly_score()
        self.assertIsNotNone(score)
        self.assertGreater(score["total_score"], 0)
        self.assertEqual(score["session_count"], 1)

    def test_reward_redemption(self):
        """TC-DB-06: 奖励兑换流程"""
        rid = self.db.request_redemption("玩具车", 200)
        self.assertGreater(rid, 0)

        pending = self.db.get_pending_redemptions()
        self.assertEqual(len(pending), 1)

        self.db.approve_redemption(rid, note="表现很好")
        pending = self.db.get_pending_redemptions()
        self.assertEqual(len(pending), 0)

    def test_config_storage(self):
        """TC-DB-07: 家长配置读写"""
        self.db.set_config("test_key", {"a": 1, "b": [2, 3]})
        val = self.db.get_config("test_key")
        self.assertEqual(val, {"a": 1, "b": [2, 3]})

        all_cfg = self.db.get_all_configs()
        self.assertIn("test_key", all_cfg)

    def test_cancel_session(self):
        """TC-DB-08: 取消会话"""
        sid = self.db.create_session()
        self.db.cancel_session(sid)
        session = self.db.get_session(sid)
        self.assertEqual(session["status"], "cancelled")

    def test_reject_redemption(self):
        """TC-DB-09: 拒绝兑换"""
        rid = self.db.request_redemption("糖果", 50)
        self.db.reject_redemption(rid, note="今天不能吃糖")
        pending = self.db.get_pending_redemptions()
        self.assertEqual(len(pending), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
