"""
坐姿检测模块 - 基于 MediaPipe Pose 33关键点 (重构版)
修复: 头部前倾检测逻辑、距离过近阈值、趴桌检测、引入多帧平滑与置信度评分
"""
import math
import time
import logging
from enum import Enum
from typing import Optional, List, Tuple
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)


class PostureState(Enum):
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class PostureViolation:
    """违规事件"""
    timestamp: float
    violation_type: str
    severity: str
    angle: float = 0.0
    confidence: float = 1.0


@dataclass
class PostureResult:
    """单帧检测结果"""
    timestamp: float = 0.0
    state: PostureState = PostureState.NORMAL
    violations: List[PostureViolation] = field(default_factory=list)
    compliance: bool = True
    head_forward_angle: float = 0.0
    head_tilt_angle: float = 0.0
    body_tilt_angle: float = 0.0
    nose_z: float = 0.0
    landmarks = None


class PostureDetector:
    """坐姿检测器 - 增强版"""

    # MediaPipe 关键点索引
    NOSE = 0
    LEFT_EYE = 2
    RIGHT_EYE = 5
    LEFT_EAR = 7
    RIGHT_EAR = 8
    LEFT_SHOULDER = 11
    RIGHT_SHOULDER = 12
    LEFT_HIP = 23
    RIGHT_HIP = 24
    LEFT_KNEE = 25
    RIGHT_KNEE = 26

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.missing_frame_count = 0
        self.max_missing_frames = 100

        # 延迟导入 MediaPipe
        import mediapipe as mp
        self.mp_pose = mp.solutions.pose
        self.mp_draw = mp.solutions.drawing_utils

        self.pose = self.mp_pose.Pose(
            static_image_mode=False,
            model_complexity=1,
            smooth_landmarks=True,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )

        # 状态机
        self._state_history: List[PostureState] = []
        self._history_size = 10

        # 提醒状态
        self._remind_cooldowns: dict = {}
        self._remind_count = 0

        # 多帧平滑缓冲区
        self._angle_buffer: dict = {
            "head_forward": [],
            "head_tilt": [],
            "body_tilt": [],
            "nose_z": [],
        }
        self._buffer_size = 5

    # ── 配置参数 ──

    @property
    def head_forward_threshold(self) -> float:
        return self.config.get("head_forward_angle", 25)

    @property
    def head_tilt_threshold(self) -> float:
        return self.config.get("head_tilt_angle", 12)

    @property
    def body_tilt_threshold(self) -> float:
        return self.config.get("body_tilt_angle", 10)

    @property
    def too_close_z(self) -> float:
        # z 值在 MediaPipe 中是归一化的，-0.15 约等于 30-40cm
        return self.config.get("too_close_z", -0.12)

    @property
    def confirm_frames(self) -> int:
        return self.config.get("confirm_frames", 3)

    @property
    def critical_seconds(self) -> int:
        return self.config.get("critical_seconds", 5)

    @property
    def remind_cooldown(self) -> int:
        return self.config.get("remind_cooldown", 30)

    @property
    def max_reminds(self) -> int:
        return self.config.get("max_reminds_per_session", 10)

    # ── 核心检测 ──

    def process(self, frame: np.ndarray) -> Optional[PostureResult]:
        """处理一帧图像，返回坐姿检测结果"""
        import cv2
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        rgb.flags.writeable = False
        results = self.pose.process(rgb)
        rgb.flags.writeable = True

        if not results.pose_landmarks:
            self.missing_frame_count += 1
            return None

        self.missing_frame_count = 0
        landmarks = results.pose_landmarks.landmark
        posture = self._analyze_posture(landmarks)
        posture.landmarks = results.pose_landmarks

        # 更新状态机
        self._update_state(posture)

        return posture

    def is_face_missing(self) -> bool:
        return self.missing_frame_count > self.max_missing_frames

    def should_remind(self, violation_type: str) -> bool:
        now = time.time()
        last = self._remind_cooldowns.get(violation_type, 0)
        if now - last < self.remind_cooldown:
            return False
        if self._remind_count >= self.max_reminds:
            return False
        self._remind_cooldowns[violation_type] = now
        self._remind_count += 1
        return True

    def reset_session(self):
        self._state_history.clear()
        self._remind_cooldowns.clear()
        self._remind_count = 0
        self.missing_frame_count = 0
        for key in self._angle_buffer:
            self._angle_buffer[key].clear()

    def draw_landmarks(self, frame: np.ndarray, landmarks) -> np.ndarray:
        self.mp_draw.draw_landmarks(
            frame, landmarks, self.mp_pose.POSE_CONNECTIONS,
            self.mp_draw.DrawingSpec(color=(0, 255, 0), thickness=2, circle_radius=3),
            self.mp_draw.DrawingSpec(color=(255, 255, 255), thickness=2),
        )
        return frame

    # ── 内部算法 ──

    def _smooth_value(self, key: str, value: float) -> float:
        """多帧平滑 - 移动平均"""
        buf = self._angle_buffer[key]
        buf.append(value)
        if len(buf) > self._buffer_size:
            buf.pop(0)
        return sum(buf) / len(buf)

    def _analyze_posture(self, landmarks) -> PostureResult:
        result = PostureResult()
        result.timestamp = time.time()

        # 提取关键点
        nose = landmarks[self.NOSE]
        left_ear = landmarks[self.LEFT_EAR]
        right_ear = landmarks[self.RIGHT_EAR]
        left_shoulder = landmarks[self.LEFT_SHOULDER]
        right_shoulder = landmarks[self.RIGHT_SHOULDER]
        left_hip = landmarks[self.LEFT_HIP]
        right_hip = landmarks[self.RIGHT_HIP]
        left_knee = landmarks[self.LEFT_KNEE]
        right_knee = landmarks[self.RIGHT_KNEE]

        # 计算中点
        shoulder_mid = self._midpoint(left_shoulder, right_shoulder)
        hip_mid = self._midpoint(left_hip, right_hip)
        ear_mid = self._midpoint(left_ear, right_ear)

        # ── 1. 头部前倾（耳中点 → 肩中点连线与垂直线夹角）──
        # 正确的检测方式：耳中点到肩中点的连线与垂直方向的夹角
        # 正常坐姿：耳在肩正上方，夹角接近0
        # 前倾时：耳向前（z变小），夹角变大
        result.head_forward_angle = self._angle_between(
            (shoulder_mid.x, shoulder_mid.y, shoulder_mid.z),
            (ear_mid.x, ear_mid.y, ear_mid.z),
        )
        result.head_forward_angle = self._smooth_value("head_forward", result.head_forward_angle)

        if result.head_forward_angle > self.head_forward_threshold:
            result.violations.append(PostureViolation(
                timestamp=time.time(),
                violation_type="head_forward",
                severity="critical",
                angle=result.head_forward_angle,
                confidence=min(result.head_forward_angle / self.head_forward_threshold, 1.5) / 1.5,
            ))

        # ── 2. 头部歪斜（两耳连线与水平夹角）──
        result.head_tilt_angle = abs(
            math.degrees(math.atan2(
                right_ear.y - left_ear.y,
                right_ear.x - left_ear.x,
            ))
        )
        result.head_tilt_angle = self._smooth_value("head_tilt", result.head_tilt_angle)

        if result.head_tilt_angle > self.head_tilt_threshold:
            result.violations.append(PostureViolation(
                timestamp=time.time(),
                violation_type="head_tilt",
                severity="warning",
                angle=result.head_tilt_angle,
            ))

        # ── 3. 身体倾斜（两肩连线与水平夹角）──
        result.body_tilt_angle = abs(
            math.degrees(math.atan2(
                right_shoulder.y - left_shoulder.y,
                right_shoulder.x - left_shoulder.x,
            ))
        )
        result.body_tilt_angle = self._smooth_value("body_tilt", result.body_tilt_angle)

        if result.body_tilt_angle > self.body_tilt_threshold:
            result.violations.append(PostureViolation(
                timestamp=time.time(),
                violation_type="body_tilt",
                severity="warning",
                angle=result.body_tilt_angle,
            ))

        # ── 4. 距离过近（鼻子 z 轴深度）──
        # MediaPipe z 值：正值表示在摄像头前方（靠近），负值表示远离
        # 实际使用中，z 值范围约 [-0.5, 0.5]，越接近摄像头 z 越大
        result.nose_z = nose.z
        smoothed_z = self._smooth_value("nose_z", nose.z)

        # 修正：z > threshold 表示距离过近（z 越大越靠近摄像头）
        if smoothed_z > abs(self.too_close_z):
            result.violations.append(PostureViolation(
                timestamp=time.time(),
                violation_type="too_close",
                severity="critical",
                confidence=min(smoothed_z / abs(self.too_close_z), 2.0) / 2.0,
            ))

        # ── 5. 趴桌检测（综合指标）──
        # 趴桌特征：
        #   a) 鼻子 y 明显低于肩中点 y（头低得很厉害）
        #   b) 耳中点 y 也低于肩中点 y
        #   c) 肩中点到髋中点的角度异常（身体前倾）
        nose_below_shoulder = nose.y > shoulder_mid.y + 0.05  # 鼻子低于肩中点 + 阈值
        ear_below_shoulder = ear_mid.y > shoulder_mid.y
        torso_angle = self._angle_between(
            (hip_mid.x, hip_mid.y, hip_mid.z),
            (shoulder_mid.x, shoulder_mid.y, shoulder_mid.z),
        )
        # 正常坐姿 torso_angle 接近 0（肩在髋正上方）
        # 趴桌时 torso_angle 变大（肩向前倾）
        torso_leaning = torso_angle > 15

        # 趴桌判定：鼻子低 + (耳朵低 或 身体前倾)
        if nose_below_shoulder and (ear_below_shoulder or torso_leaning):
            result.violations.append(PostureViolation(
                timestamp=time.time(),
                violation_type="lying_down",
                severity="critical",
            ))

        result.compliance = len(result.violations) == 0
        return result

    def _update_state(self, posture: PostureResult):
        """状态机：NORMAL → WARNING → CRITICAL"""
        has_violation = not posture.compliance
        has_critical = any(
            v.severity == "critical" for v in posture.violations
        )

        if has_critical:
            current_state = PostureState.CRITICAL
        elif has_violation:
            current_state = PostureState.WARNING
        else:
            current_state = PostureState.NORMAL

        # 防抖：连续 confirm_frames 才变更状态
        self._state_history.append(current_state)
        if len(self._state_history) > self._history_size:
            self._state_history.pop(0)

        if len(self._state_history) >= self.confirm_frames:
            recent = self._state_history[-self.confirm_frames:]
            if all(s == current_state for s in recent):
                posture.state = current_state
            else:
                posture.state = self._state_history[-2] if len(self._state_history) >= 2 else PostureState.NORMAL
        else:
            posture.state = current_state

    # ── 工具函数 ──

    @staticmethod
    def _midpoint(a, b):
        """计算两个关键点的中点"""
        class MidPoint:
            pass
        m = MidPoint()
        m.x = (a.x + b.x) / 2
        m.y = (a.y + b.y) / 2
        m.z = (a.z + b.z) / 2
        return m

    @staticmethod
    def _angle_between(p1: tuple, p2: tuple) -> float:
        """计算两点连线与垂直线(y轴)的夹角（度）
        垂直方向(dy大, horizontal小) -> 角度接近0
        水平方向(dy=0, horizontal大) -> 角度90
        """
        dx, dy, dz = p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2]
        horizontal_dist = math.sqrt(dx * dx + dz * dz)
        vertical_dist = abs(dy)
        if horizontal_dist < 1e-9 and vertical_dist < 1e-9:
            return 0.0
        if horizontal_dist < 1e-9:
            return 0.0
        return math.degrees(math.atan2(horizontal_dist, vertical_dist))

    def release(self):
        if hasattr(self, 'pose'):
            self.pose.close()


# 类型检查
import cv2
