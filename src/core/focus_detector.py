"""
专注度检测模块 - 基于 MediaPipe Face Mesh
检测维度: 视线方向、眨眼频率、头部姿态
"""
import time
import math
import logging
from typing import Optional, Tuple
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class FocusResult:
    """专注度检测结果"""
    focus_score: float = 1.0  # 0.0~1.0
    gaze_direction: str = "center"  # center/left/right/up/down/closed
    blink_rate: float = 0.0  # 眨眼次数/分钟
    head_pose: Tuple[float, float, float] = (0.0, 0.0, 0.0)  # pitch, yaw, roll
    is_drowsy: bool = False
    is_distracted: bool = False
    face_visible: bool = True


class FocusDetector:
    """专注度检测器"""

    # MediaPipe Face Mesh 关键点索引
    LEFT_EYE_INDICES = [33, 160, 158, 133, 153, 144]   # 左眼轮廓
    RIGHT_EYE_INDICES = [362, 385, 387, 263, 373, 380]  # 右眼轮廓
    LEFT_IRIS = [468, 469, 470, 471]    # 左眼虹膜
    RIGHT_IRIS = [473, 474, 475, 476]   # 右眼虹膜
    FACE_OVAL = [10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288,
                  397, 365, 379, 378, 400, 377, 152, 148, 176, 149, 150, 136,
                  172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109]
    NOSE_TIP = 1
    CHIN = 152

    def __init__(self, config: dict = None):
        self.config = config or {}
        self._face_mesh = None
        self._blink_count = 0
        self._last_blink_time = 0.0
        self._blink_window_start = time.time()
        self._ear_history = []
        self._ear_window = 10
        self._ear_threshold = 0.2
        self._gaze_history = []
        self._gaze_window = 15

        # 延迟初始化 MediaPipe
        try:
            import mediapipe as mp
            self.mp_face_mesh = mp.solutions.face_mesh
            self.face_mesh = self.mp_face_mesh.FaceMesh(
                static_image_mode=False,
                max_num_faces=1,
                refine_landmarks=True,  # 启用虹膜关键点
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5,
            )
            logger.info("Face Mesh 初始化完成")
        except Exception as e:
            logger.error(f"Face Mesh 初始化失败: {e}")
            self.face_mesh = None

    def process(self, frame: np.ndarray) -> Optional[FocusResult]:
        """处理一帧图像，返回专注度检测结果"""
        if self.face_mesh is None:
            return None

        import cv2
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        rgb.flags.writeable = False
        results = self.face_mesh.process(rgb)
        rgb.flags.writeable = True

        if not results.multi_face_landmarks:
            return FocusResult(focus_score=0.0, face_visible=False)

        landmarks = results.multi_face_landmarks[0].landmark
        h, w = frame.shape[:2]

        # 提取关键点坐标
        points = []
        for lm in landmarks:
            points.append((lm.x * w, lm.y * h, lm.z))

        # 1. 眨眼检测 (EAR)
        left_ear = self._eye_aspect_ratio(points, self.LEFT_EYE_INDICES)
        right_ear = self._eye_aspect_ratio(points, self.RIGHT_EYE_INDICES)
        avg_ear = (left_ear + right_ear) / 2.0

        # EAR 历史平滑
        self._ear_history.append(avg_ear)
        if len(self._ear_history) > self._ear_window:
            self._ear_history.pop(0)
        smoothed_ear = sum(self._ear_history) / len(self._ear_history)

        # 眨眼计数
        is_blinking = smoothed_ear < self._ear_threshold
        if is_blinking and (time.time() - self._last_blink_time) > 0.3:
            self._blink_count += 1
            self._last_blink_time = time.time()

        # 计算眨眼频率 (次/分钟)
        now = time.time()
        if now - self._blink_window_start >= 60:
            self._blink_count = max(0, self._blink_count - 1)
            self._blink_window_start = now
        blink_rate = self._blink_count

        # 2. 视线方向
        gaze = self._estimate_gaze(points, w, h)
        self._gaze_history.append(gaze)
        if len(self._gaze_history) > self._gaze_window:
            self._gaze_history.pop(0)

        # 投票决定视线方向
        gaze_votes = {"center": 0, "left": 0, "right": 0, "up": 0, "down": 0, "closed": 0}
        for g in self._gaze_history:
            gaze_votes[g] += 1
        gaze_direction = max(gaze_votes, key=gaze_votes.get)

        # 3. 头部姿态 (简化版)
        head_pose = self._estimate_head_pose(points, w, h)

        # 4. 专注度评分
        focus_score = self._calculate_focus_score(
            gaze_direction, blink_rate, head_pose, smoothed_ear
        )

        # 5. 状态判断
        is_drowsy = blink_rate > 30 or (smoothed_ear < 0.15 and blink_rate > 15)
        is_distracted = gaze_direction in ("left", "right", "up", "down")

        return FocusResult(
            focus_score=focus_score,
            gaze_direction=gaze_direction,
            blink_rate=blink_rate,
            head_pose=head_pose,
            is_drowsy=is_drowsy,
            is_distracted=is_distracted,
            face_visible=True,
        )

    def _eye_aspect_ratio(self, points, eye_indices):
        """计算眼睛纵横比 EAR"""
        # 6个点: [p1, p2, p3, p4, p5, p6]
        # EAR = (|p2-p6| + |p3-p5|) / (2 * |p1-p4|)
        p = [points[i] for i in eye_indices]
        v1 = math.dist((p[1][0], p[1][1]), (p[5][0], p[5][1]))
        v2 = math.dist((p[2][0], p[2][1]), (p[4][0], p[4][1]))
        h = math.dist((p[0][0], p[0][1]), (p[3][0], p[3][1]))
        if h < 1e-6:
            return 1.0
        return (v1 + v2) / (2.0 * h)

    def _estimate_gaze(self, points, w, h) -> str:
        """估计视线方向"""
        # 获取左右眼虹膜中心
        try:
            left_iris_center = self._get_center(points, self.LEFT_IRIS)
            right_iris_center = self._get_center(points, self.RIGHT_IRIS)

            # 获取左右眼眼角
            left_eye_corner = points[33]   # 左眼外角
            left_eye_inner = points[133]   # 左眼内角
            right_eye_inner = points[362]  # 右眼内角
            right_eye_corner = points[263] # 右眼外角

            # 计算虹膜相对于眼框的位置比例
            left_ratio = (left_iris_center[0] - left_eye_inner[0]) / max(left_eye_corner[0] - left_eye_inner[0], 1)
            right_ratio = (right_iris_center[0] - right_eye_inner[0]) / max(right_eye_corner[0] - right_eye_inner[0], 1)

            # 垂直方向（基于眼睑位置粗略估计）
            left_eye_top = points[159]
            left_eye_bottom = points[145]
            vertical_ratio = (left_iris_center[1] - left_eye_top[1]) / max(left_eye_bottom[1] - left_eye_top[1], 1)

            # 判断方向
            avg_h_ratio = (left_ratio + right_ratio) / 2.0

            # 眼睛闭合检测
            left_ear = self._eye_aspect_ratio(points, self.LEFT_EYE_INDICES)
            right_ear = self._eye_aspect_ratio(points, self.RIGHT_EYE_INDICES)
            if left_ear < 0.18 and right_ear < 0.18:
                return "closed"

            # 水平方向
            if avg_h_ratio < 0.35:
                return "right"  # 虹膜偏右 = 看右边
            elif avg_h_ratio > 0.65:
                return "left"   # 虹膜偏左 = 看左边

            # 垂直方向
            if vertical_ratio < 0.35:
                return "up"
            elif vertical_ratio > 0.65:
                return "down"

            return "center"
        except Exception:
            return "center"

    def _get_center(self, points, indices):
        """计算多个点的中心"""
        x = sum(points[i][0] for i in indices) / len(indices)
        y = sum(points[i][1] for i in indices) / len(indices)
        z = sum(points[i][2] for i in indices) / len(indices)
        return (x, y, z)

    def _estimate_head_pose(self, points, w, h) -> Tuple[float, float, float]:
        """简化版头部姿态估计（俯仰/偏航/翻滚角）"""
        nose = points[self.NOSE_TIP]
        chin = points[self.CHIN]
        left_eye = points[33]
        right_eye = points[263]

        # 俯仰角 (pitch): 鼻尖-下巴连线与垂直方向夹角
        pitch = math.degrees(math.atan2(
            chin[1] - nose[1],
            max(chin[2] - nose[2], 1)
        ))

        # 偏航角 (yaw): 两眼水平位置差
        eye_mid_x = (left_eye[0] + right_eye[0]) / 2
        nose_offset = nose[0] - eye_mid_x
        yaw = nose_offset / (w * 0.1) * 30  # 粗略估计

        # 翻滚角 (roll): 两眼连线与水平夹角
        roll = math.degrees(math.atan2(
            right_eye[1] - left_eye[1],
            right_eye[0] - left_eye[0]
        ))

        return (pitch, yaw, roll)

    def _calculate_focus_score(self, gaze, blink_rate, head_pose, ear) -> float:
        """计算专注度评分 0.0~1.0"""
        score = 1.0

        # 视线方向扣分
        if gaze == "center":
            pass
        elif gaze == "closed":
            score -= 0.3  # 闭眼
        else:
            score -= 0.4  # 看别处

        # 眨眼频率扣分 (正常 15-20 次/分钟)
        if blink_rate > 35:
            score -= 0.2  # 眨眼过多（疲劳）
        elif blink_rate < 5:
            score -= 0.1  # 眨眼过少（不自然）

        # 头部姿态扣分
        pitch, yaw, roll = head_pose
        if abs(yaw) > 25:
            score -= 0.3  # 头部左右转
        if abs(pitch) > 20:
            score -= 0.2  # 头部上下仰
        if abs(roll) > 15:
            score -= 0.1  # 头部歪斜

        return max(0.0, min(1.0, score))

    def reset(self):
        """重置统计"""
        self._blink_count = 0
        self._last_blink_time = 0.0
        self._blink_window_start = time.time()
        self._ear_history.clear()
        self._gaze_history.clear()

    def release(self):
        if self.face_mesh is not None:
            self.face_mesh.close()
