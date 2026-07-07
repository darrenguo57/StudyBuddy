"""
数据持久化模块 - SQLite 数据库管理
"""
import sqlite3
import json
import logging
from pathlib import Path
from datetime import datetime, date
from typing import Optional, Dict, List, Any
from contextlib import contextmanager

logger = logging.getLogger(__name__)


class Database:
    """SQLite 数据库管理器"""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_date TEXT NOT NULL,
        start_time TEXT NOT NULL,
        end_time TEXT,
        duration_minutes REAL,
        posture_rate REAL DEFAULT 0,
        focus_rate REAL DEFAULT 0,
        efficiency_score REAL DEFAULT 0,
        correction_rate REAL DEFAULT 0,
        total_score REAL DEFAULT 0,
        grade TEXT DEFAULT '',
        video_path TEXT DEFAULT '',
        raw_video_path TEXT DEFAULT '',
        status TEXT DEFAULT 'in_progress'
    );

    CREATE TABLE IF NOT EXISTS posture_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER REFERENCES sessions(id),
        timestamp REAL NOT NULL,
        violation_type TEXT NOT NULL,
        severity TEXT NOT NULL,
        reminded INTEGER DEFAULT 0,
        corrected INTEGER DEFAULT 0,
        corrected_time REAL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS monthly_scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year_month TEXT NOT NULL UNIQUE,
        total_score REAL DEFAULT 0,
        session_count INTEGER DEFAULT 0,
        bonus_score REAL DEFAULT 0,
        used_score REAL DEFAULT 0,
        available_score REAL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS reward_redemptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reward_name TEXT NOT NULL,
        score_cost INTEGER NOT NULL,
        request_date TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        approved_date TEXT,
        parent_note TEXT DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS parent_config (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with self._get_conn() as conn:
            conn.executescript(self.SCHEMA)
            # 初始化默认月度记录
            ym = datetime.now().strftime("%Y-%m")
            conn.execute(
                "INSERT OR IGNORE INTO monthly_scores (year_month) VALUES (?)",
                (ym,),
            )
        logger.info(f"数据库初始化完成: {self.db_path}")

    @contextmanager
    def _get_conn(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── Session 操作 ──

    def create_session(self, raw_video_path: str = "") -> int:
        """创建新会话，返回 session_id"""
        now = datetime.now()
        with self._get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO sessions
                   (session_date, start_time, raw_video_path, status)
                   VALUES (?, ?, ?, 'in_progress')""",
                (now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S"), raw_video_path),
            )
            return cur.lastrowid

    def end_session(
        self,
        session_id: int,
        duration_minutes: float,
        posture_rate: float,
        focus_rate: float,
        efficiency_score: float,
        correction_rate: float,
        total_score: float,
        grade: str,
        video_path: str = "",
        raw_video_path: str = "",
        status: str = "completed",
    ):
        """结束会话并写入评分"""
        now = datetime.now()
        with self._get_conn() as conn:
            conn.execute(
                """UPDATE sessions SET
                   end_time=?, duration_minutes=?, posture_rate=?,
                   focus_rate=?, efficiency_score=?, correction_rate=?,
                   total_score=?, grade=?, video_path=?, raw_video_path=?,
                   status=?
                   WHERE id=?""",
                (
                    now.strftime("%H:%M:%S"),
                    round(duration_minutes, 1),
                    round(posture_rate, 4),
                    round(focus_rate, 4),
                    round(efficiency_score, 2),
                    round(correction_rate, 4),
                    round(total_score, 2),
                    grade,
                    video_path,
                    raw_video_path,
                    status,
                    session_id,
                ),
            )
        # 更新月度积分
        self._update_monthly_score(total_score)

    def _update_monthly_score(self, score: float):
        ym = datetime.now().strftime("%Y-%m")
        with self._get_conn() as conn:
            conn.execute(
                """UPDATE monthly_scores SET
                   total_score = total_score + ?,
                   session_count = session_count + 1,
                   available_score = total_score + bonus_score - used_score + ?
                   WHERE year_month = ?""",
                (score, score, ym),
            )

    def get_session(self, session_id: int) -> Optional[Dict]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_recent_sessions(self, limit: int = 10) -> List[Dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def cancel_session(self, session_id: int):
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE sessions SET status='cancelled' WHERE id=?",
                (session_id,),
            )

    def delete_session(self, session_id: int):
        """删除会话及其关联的所有数据"""
        with self._get_conn() as conn:
            conn.execute("DELETE FROM posture_events WHERE session_id=?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))

    # ── Posture Event 操作 ──

    def add_posture_event(
        self,
        session_id: int,
        timestamp: float,
        violation_type: str,
        severity: str,
        reminded: bool = False,
    ) -> int:
        with self._get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO posture_events
                   (session_id, timestamp, violation_type, severity, reminded)
                   VALUES (?, ?, ?, ?, ?)""",
                (session_id, timestamp, violation_type, severity, int(reminded)),
            )
            return cur.lastrowid

    def mark_event_corrected(self, event_id: int, corrected_time: float):
        with self._get_conn() as conn:
            conn.execute(
                """UPDATE posture_events SET corrected=1, corrected_time=?
                   WHERE id=?""",
                (corrected_time, event_id),
            )

    def get_session_events(self, session_id: int) -> List[Dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM posture_events WHERE session_id=? ORDER BY timestamp",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ── 月度积分操作 ──

    def get_monthly_score(self, year_month: str = None) -> Optional[Dict]:
        if year_month is None:
            year_month = datetime.now().strftime("%Y-%m")
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM monthly_scores WHERE year_month=?",
                (year_month,),
            ).fetchone()
            return dict(row) if row else None

    def add_bonus_score(self, bonus: float, year_month: str = None):
        if year_month is None:
            year_month = datetime.now().strftime("%Y-%m")
        with self._get_conn() as conn:
            conn.execute(
                """UPDATE monthly_scores SET
                   bonus_score = bonus_score + ?,
                   available_score = total_score + bonus_score + ? - used_score
                   WHERE year_month = ?""",
                (bonus, bonus, year_month),
            )

    # ── 奖励兑换 ──

    def request_redemption(self, reward_name: str, score_cost: int) -> int:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO reward_redemptions
                   (reward_name, score_cost, request_date)
                   VALUES (?, ?, ?)""",
                (reward_name, score_cost, now),
            )
            return cur.lastrowid

    def approve_redemption(self, redemption_id: int, note: str = ""):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT score_cost FROM reward_redemptions WHERE id=?",
                (redemption_id,),
            ).fetchone()
            if not row:
                raise ValueError(f"兑换记录不存在: {redemption_id}")

            conn.execute(
                """UPDATE reward_redemptions SET
                   status='approved', approved_date=?, parent_note=?
                   WHERE id=?""",
                (now, note, redemption_id),
            )
            ym = datetime.now().strftime("%Y-%m")
            conn.execute(
                """UPDATE monthly_scores SET
                   used_score = used_score + ?,
                   available_score = total_score + bonus_score - used_score - ?
                   WHERE year_month = ?""",
                (row["score_cost"], row["score_cost"], ym),
            )

    def reject_redemption(self, redemption_id: int, note: str = ""):
        with self._get_conn() as conn:
            conn.execute(
                """UPDATE reward_redemptions SET
                   status='rejected', parent_note=?
                   WHERE id=?""",
                (note, redemption_id),
            )

    def get_pending_redemptions(self) -> List[Dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM reward_redemptions WHERE status='pending'"
            ).fetchall()
            return [dict(r) for r in rows]

    # ── 家长配置 ──

    def get_config(self, key: str, default: Any = None) -> Any:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM parent_config WHERE key=?", (key,)
            ).fetchone()
            if row:
                try:
                    return json.loads(row["value"])
                except (json.JSONDecodeError, TypeError):
                    return row["value"]
            return default

    def set_config(self, key: str, value: Any):
        with self._get_conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO parent_config (key, value, updated_at)
                   VALUES (?, ?, ?)""",
                (key, json.dumps(value), datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )

    def get_all_configs(self) -> Dict[str, Any]:
        with self._get_conn() as conn:
            rows = conn.execute("SELECT key, value FROM parent_config").fetchall()
            result = {}
            for r in rows:
                try:
                    result[r["key"]] = json.loads(r["value"])
                except (json.JSONDecodeError, TypeError):
                    result[r["key"]] = r["value"]
            return result
