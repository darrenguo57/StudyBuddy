"""
评分引擎模块 - 四维加权评分系统
坐姿合规率(40%) + 专注时长(25%) + 完成效率(20%) + 纠正响应(15%)
"""
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ScoreReport:
    """评分报告"""
    session_id: int = 0
    # 四维指标
    posture_rate: float = 1.0
    focus_rate: float = 1.0
    efficiency_score: float = 100.0
    correction_rate: float = 1.0
    # 综合
    total_score: float = 0.0
    grade: str = "N/A"
    rank: str = ""
    # 统计
    duration_minutes: float = 0.0
    total_violations: int = 0
    reminded_count: int = 0
    corrected_count: int = 0
    consecutive_clean_minutes: int = 0
    exit_minutes: float = 0.0
    # 加减分明细
    deductions: List[Dict] = field(default_factory=list)
    bonuses: List[Dict] = field(default_factory=list)
    # 时间戳
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())


class ScoringEngine:
    """评分引擎"""

    # 维度权重
    WEIGHTS = {
        "posture": 0.40,
        "focus": 0.25,
        "efficiency": 0.20,
        "correction": 0.15,
    }

    # 等级阈值 — A+ / A- / A / B 四档（得分 79-99 区间）
    GRADE_THRESHOLDS = [
        (95, "A+", "超级优秀"),
        (90, "A-", "非常棒"),
        (85, "A", "表现良好"),
        (79, "B", "还需要改进"),
    ]

    # 扣分规则（大幅降低，让大部分结果落在 B 范围）
    PENALTIES = {
        "head_forward": 1.5,
        "head_tilt": 0.5,
        "body_tilt": 0.5,
        "too_close": 1,
        "lying_down": 2,
    }

    def __init__(self, config: dict = None):
        self.config = config or {}

    @property
    def expected_duration(self) -> int:
        """家长设定的预期作业时长（分钟）"""
        return self.config.get("expected_duration", 40)

    @property
    def penalty_cap(self) -> float:
        """扣分上限"""
        return self.config.get("penalty_cap", 50)

    def calculate(
        self,
        session_id: int,
        duration_minutes: float,
        posture_events: List[Dict],
        focus_events: List[Dict] = None,
        session_start: float = 0,
        session_end: float = 0,
    ) -> ScoreReport:
        """
        计算综合评分
        :param session_id: 会话ID
        :param duration_minutes: 作业总时长（分钟）
        :param posture_events: 坐姿事件列表 [{"timestamp","violation_type","severity","reminded","corrected","corrected_time"}]
        :param focus_events: 专注度事件列表 [{"timestamp","focus_score","gaze_direction","is_drowsy","is_distracted"}]
        :param session_start: 开始时间戳
        :param session_end: 结束时间戳
        """
        report = ScoreReport(
            session_id=session_id,
            duration_minutes=round(duration_minutes, 1),
        )
        if focus_events is None:
            focus_events = []

        # 分析事件
        violations = [e for e in posture_events if e.get("violation_type")]
        reminded = [e for e in violations if e.get("reminded")]
        corrected = [e for e in violations if e.get("corrected")]

        report.total_violations = len(violations)
        report.reminded_count = len(reminded)
        report.corrected_count = len(corrected)

        # ── 1. 坐姿合规率 (40%) ──
        report.posture_rate = self._calc_posture_rate(
            violations, duration_minutes
        )
        # 扣分
        posture_deductions = self._calc_penalties(violations, corrected)
        report.deductions.extend(posture_deductions)
        penalty_sum = sum(d["points"] for d in posture_deductions)
        posture_score = max(0, report.posture_rate * 100 - penalty_sum)

        # ── 2. 专注时长率 (25%) ──
        report.focus_rate = self._calc_focus_rate(
            focus_events, duration_minutes
        )
        # 计算离开时间（基于趴桌事件 + 专注度缺失）
        report.exit_minutes = self._calc_exit_time(
            violations, focus_events, session_start, session_end
        )
        focus_score = max(0, report.focus_rate * 100)

        # ── 3. 完成效率 (20%) ──
        report.efficiency_score = self._calc_efficiency(duration_minutes)
        efficiency_score = report.efficiency_score

        # ── 4. 纠正响应率 (15%) ──
        report.correction_rate = self._calc_correction_rate(
            reminded, corrected
        )
        correction_score = report.correction_rate * 100

        # ── 加权综合 ──
        report.total_score = (
            posture_score * self.WEIGHTS["posture"]
            + focus_score * self.WEIGHTS["focus"]
            + efficiency_score * self.WEIGHTS["efficiency"]
            + correction_score * self.WEIGHTS["correction"]
        )

        # ── 加分项 ──
        bonuses = self._calc_bonuses(duration_minutes, violations, corrected)
        report.bonuses.extend(bonuses)
        bonus_total = sum(b["points"] for b in bonuses)
        report.total_score += bonus_total

        # ── 连续零违规 ──
        report.consecutive_clean_minutes = self._calc_clean_streak(
            violations, duration_minutes, session_start
        )

        # ── 等级评定 ──
        # 将原始 0-100 得分映射到 79-99 区间
        raw_score = max(0, min(100, report.total_score))
        report.total_score = 79.0 + raw_score * 0.20
        report.grade = self._get_grade(report.total_score)

        logger.info(
            f"评分完成: session_{session_id} = {report.total_score:.1f}分 ({report.grade}), "
            f"合规率{report.posture_rate:.1%} 专注{report.focus_rate:.1%}"
        )
        return report

    # ── 维度计算 ──

    def _calc_penalties(
        self, violations: List[Dict], corrected: List[Dict]
    ) -> List[Dict]:
        """计算扣分明细"""
        deductions = []
        penalty_total = 0
        cap = self.penalty_cap

        for v in violations:
            vtype = v.get("violation_type", "")
            points = self.PENALTIES.get(vtype, 0)
            if points == 0:
                continue
            # 纠正减半
            if v.get("corrected") and v.get("corrected_time", 999) < 60:
                points = max(1, points // 2)

            if penalty_total + points > cap:
                points = cap - penalty_total
            if points <= 0:
                break

            penalty_total += abs(points)
            deductions.append({
                "type": vtype,
                "points": -points,
                "time": v.get("timestamp", 0),
                "corrected": v.get("corrected", 0),
            })

        return deductions

    def _calc_posture_rate(
        self, violations: List[Dict], duration_minutes: float
    ) -> float:
        """坐姿合规率 = 1 - (违规时长 / 总时长)"""
        if duration_minutes <= 0:
            return 1.0

        violation_seconds = 0
        for v in violations:
            severity = v.get("severity", "warning")
            if severity == "critical":
                violation_seconds += 5  # critical 算5秒
            else:
                violation_seconds += 2
            # 如果已纠正，只算纠正前时间
            if v.get("corrected_time", 0) > 0:
                violation_seconds = min(violation_seconds, v["corrected_time"])

        total_seconds = duration_minutes * 60
        rate = 1.0 - min(1.0, violation_seconds / total_seconds)
        return round(max(0.0, rate), 4)

    def _calc_focus_rate(
        self, focus_events: List[Dict], duration_minutes: float
    ) -> float:
        """专注时长率 = 1 - (分心时长 + 困倦时长*2) / 总时长"""
        if duration_minutes <= 0 or not focus_events:
            return 1.0

        total_seconds = duration_minutes * 60
        sample_interval = total_seconds / max(len(focus_events), 1)

        distracted_seconds = 0.0
        drowsy_seconds = 0.0
        for ev in focus_events:
            if ev.get("is_drowsy"):
                drowsy_seconds += sample_interval
            elif ev.get("is_distracted"):
                distracted_seconds += sample_interval

        # 困倦权重更高（*2），因为比单纯分心更严重
        weighted_loss = distracted_seconds + drowsy_seconds * 2
        rate = 1.0 - min(1.0, weighted_loss / total_seconds)
        return round(max(0.0, rate), 4)

    def _calc_exit_time(
        self,
        violations: List[Dict],
        focus_events: List[Dict],
        session_start: float,
        session_end: float,
    ) -> float:
        """计算离开/无效总时长（分钟）——结合趴桌事件和专注度缺失"""
        exit_seconds = 0

        # 趴桌事件
        lying_events = [v for v in violations if v.get("violation_type") == "lying_down"]
        exit_seconds += len(lying_events) * 5

        # 专注度缺失：长时间无面部或持续困倦
        if focus_events:
            invisible_count = sum(1 for ev in focus_events if not ev.get("face_visible", True))
            drowsy_count = sum(1 for ev in focus_events if ev.get("is_drowsy"))
            # 面部消失每次算8秒，困倦每次算5秒
            exit_seconds += invisible_count * 8 + drowsy_count * 5

        return round(exit_seconds / 60, 1)

    def _calc_efficiency(self, duration_minutes: float) -> float:
        """完成效率得分：预期时长对比
        ratio = 实际时长 / 预期时长
        ratio <= 1.0: 按时或提前完成 -> 100
        ratio <= 1.25: 超时25%以内 -> 85
        ratio <= 1.67: 超时67%以内 -> 70
        ratio <= 2.5: 超时150%以内 -> 55
        否则 -> 40
        """
        expected = self.expected_duration
        if duration_minutes <= 0:
            return 0.0

        ratio = duration_minutes / expected
        if ratio <= 1.0:
            return 100.0  # 按时或提前
        elif ratio <= 1.25:
            return 85.0
        elif ratio <= 1.67:
            return 70.0
        elif ratio <= 2.5:
            return 55.0
        else:
            return 40.0

    def _calc_correction_rate(
        self, reminded: List[Dict], corrected: List[Dict]
    ) -> float:
        """纠正响应率"""
        if not reminded:
            return 1.0
        return round(len(corrected) / len(reminded), 4)

    def _calc_bonuses(
        self,
        duration_minutes: float,
        violations: List[Dict],
        corrected: List[Dict],
    ) -> List[Dict]:
        """计算加分项"""
        bonuses = []

        # 连续30分钟零违规 +5
        if duration_minutes >= 30 and len(violations) == 0:
            bonuses.append({"type": "clean_streak", "points": 5})

        # 提前完成（比预期短25%以上） +5
        expected = self.expected_duration
        if duration_minutes < expected * 0.75 and duration_minutes >= 10:
            bonuses.append({"type": "early_finish", "points": 5})

        # 自觉纠正比例 >= 50% +2
        if len(violations) > 0 and len(corrected) / len(violations) >= 0.5:
            bonuses.append({"type": "self_correct", "points": 2})

        return bonuses

    def _calc_clean_streak(
        self,
        violations: List[Dict],
        duration_minutes: float,
        session_start: float,
    ) -> int:
        """计算最长连续零违规分钟数"""
        if not violations:
            return int(duration_minutes)

        # 将违规时间戳转换为相对分钟
        violation_times = sorted([
            (v["timestamp"] - session_start) / 60.0
            for v in violations
        ])

        max_streak = 0
        prev = 0.0
        for t in violation_times:
            streak = int(t - prev)
            if streak > max_streak:
                max_streak = streak
            prev = t

        # 最后一节到结束
        final_streak = int(duration_minutes - violation_times[-1])
        if final_streak > max_streak:
            max_streak = final_streak

        return max_streak

    def _get_grade(self, score: float) -> str:
        for threshold, grade, _ in self.GRADE_THRESHOLDS:
            if score >= threshold:
                return grade
        return "B"

    def generate_text_report(self, report: ScoreReport) -> str:
        """生成可读评分报告"""
        lines = []
        lines.append("=" * 50)
        lines.append(f"  📋 StudyBuddy 作业报告 #{report.session_id}")
        lines.append("=" * 50)
        lines.append(f"  时长: {report.duration_minutes:.0f} 分钟")
        lines.append(f"  综合得分: {report.total_score:.1f} 分")
        lines.append(f"  等级: {report.grade}")
        lines.append("-" * 50)
        lines.append(f"  坐姿合规率: {report.posture_rate:.1%}   (权重 40%)")
        lines.append(f"  专注时长率: {report.focus_rate:.1%}   (权重 25%)")
        lines.append(f"  完成效率:   {report.efficiency_score:.0f}分  (权重 20%)")
        lines.append(f"  纠正响应率: {report.correction_rate:.1%}   (权重 15%)")
        lines.append("-" * 50)
        lines.append(f"  违规次数: {report.total_violations}")
        lines.append(f"  提醒次数: {report.reminded_count}")
        lines.append(f"  自觉纠正: {report.corrected_count}")
        lines.append(f"  最长零违规: {report.consecutive_clean_minutes} 分钟")

        if report.bonuses:
            lines.append("  加分项:")
            for b in report.bonuses:
                lines.append(f"    +{b['points']} {b['type']}")
        if report.deductions:
            lines.append("  扣分项:")
            for d in report.deductions:
                lines.append(f"    {d['points']} {d['type']}")

        lines.append("=" * 50)
        return "\n".join(lines)
