"""
摄像头预览组件 - 修复版
修复: 首次获取帧后未清除占位样式导致 pixmap 被背景色遮挡
"""
import logging

import cv2
import numpy as np

from pathlib import Path
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QFrame
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QImage, QPixmap, QPainter

from .styles import (
    BG_ELEVATED, SEPARATOR, TEXT_TERTIARY,
    SUCCESS, ERROR, RADIUS, FONT_SIZE, FONT_WEIGHT,
)

MASCOT_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "images" / "mascot"

logger = logging.getLogger("StudyBuddy.UI")


class CameraWidget(QFrame):
    """摄像头预览组件"""

    def __init__(self, camera_manager, parent=None):
        super().__init__(parent)
        self.camera = camera_manager
        self._first_frame_received = False

        self.setMinimumSize(480, 360)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {BG_ELEVATED};
                border: 1px solid {SEPARATOR};
                border-radius: {RADIUS['lg']};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        # 视频画面 - 使用独立容器
        self.video_container = QWidget()
        self.video_container.setStyleSheet(f"""
            background-color: {BG_ELEVATED};
            border-radius: {RADIUS['lg']};
        """)
        container_layout = QVBoxLayout(self.video_container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        self.video_label = QLabel()
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setMinimumSize(480, 360)
        self.video_label.setStyleSheet("background-color: transparent; border: none;")
        container_layout.addWidget(self.video_label)

        layout.addWidget(self.video_container, 1)

        # 状态叠加层
        self.status_overlay = QLabel()
        self.status_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_overlay.setStyleSheet(f"""
            color: {TEXT_TERTIARY};
            font-size: {FONT_SIZE['md']};
            font-weight: {FONT_WEIGHT['medium']};
            background-color: transparent;
        """)
        self.status_overlay.setText("摄像头未连接")
        layout.addWidget(self.status_overlay)

        # 帧更新定时器
        self._frame_timer = QTimer(self)
        self._frame_timer.timeout.connect(self._update_frame)
        self._frame_timer.start(50)  # ~20fps，降低CPU占用

        # 吉祥物叠加层（右下角）
        self.mascot_overlay = QLabel(self.video_container)
        self.mascot_overlay.setFixedSize(56, 56)
        self.mascot_overlay.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        self.mascot_overlay.setStyleSheet("background-color: transparent; border: none;")
        self.mascot_overlay.hide()
        self._mascot_pixmaps = {}
        for name in ["mascot_default", "mascot_happy"]:
            p = MASCOT_DIR / f"{name}.jpg"
            if p.exists():
                self._mascot_pixmaps[name] = QPixmap(str(p)).scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)

        self._show_placeholder()

    def _show_placeholder(self):
        """显示占位提示"""
        self._first_frame_received = False
        self.video_label.clear()
        self.video_label.setText("摄像头未连接")
        self.video_label.setStyleSheet(f"""
            background-color: transparent;
            color: {TEXT_TERTIARY};
            font-size: {FONT_SIZE['md']};
            border: none;
        """)
        self.video_container.setStyleSheet(f"""
            background-color: {BG_ELEVATED};
            border: 2px dashed {SEPARATOR};
            border-radius: {RADIUS['lg']};
        """)
        self.status_overlay.setText("摄像头未连接")
        self.status_overlay.setStyleSheet(f"color: {TEXT_TERTIARY}; font-size: {FONT_SIZE['md']}; font-weight: {FONT_WEIGHT['medium']}; background-color: transparent;")
        self.status_overlay.show()

    def _update_frame(self):
        """定时更新视频帧 - 直接使用 QImage BGR888 零拷贝渲染"""
        if not self.camera.is_connected:
            return

        try:
            frame = self.camera.get_preview_frame()
            if frame is None:
                return

            if not self._first_frame_received:
                self._first_frame_received = True
                self.video_label.clear()
                self.video_label.setStyleSheet("background-color: transparent; border: none;")
                self.video_container.setStyleSheet(f"""
                    background-color: {BG_ELEVATED};
                    border: 1px solid {SEPARATOR};
                    border-radius: {RADIUS['lg']};
                """)
                logger.info("摄像头预览帧开始渲染")

            # 调试：记录帧属性（仅首次）
            if not hasattr(self, "_logged_frame_info"):
                self._logged_frame_info = True
                logger.info(f"预览帧属性: shape={frame.shape}, dtype={frame.dtype}, "
                           f"min={frame.min()}, max={frame.max()}, mean={frame.mean():.1f}")

            # 统一转换为 BGR（3 通道），CameraManager 已保证 BGR 输出
            # 仅处理四通道 BGRA 回退和灰度情况
            if len(frame.shape) == 2:
                frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
            elif frame.shape[2] == 4:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
            elif frame.shape[2] != 3:
                logger.warning(f"不支持的帧通道数: {frame.shape}")
                return

            # 确保内存连续（关键：QImage 要求字节对齐）
            h, w, ch = frame.shape
            if not frame.flags["C_CONTIGUOUS"]:
                frame = np.ascontiguousarray(frame)
            bytes_per_line = ch * w

            # Qt6 原生支持 BGR888，无需 RGB 转换，零拷贝映射内存
            qt_image = QImage(frame.data, w, h, bytes_per_line, QImage.Format.Format_BGR888)
            if qt_image.isNull():
                logger.warning("QImage BGR888 构造失败，回退到 RGB 转换")
                rgb_frame = np.ascontiguousarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                qt_image = QImage(rgb_frame.data, w, h, w * 3, QImage.Format.Format_RGB888)
                if qt_image.isNull():
                    return

            pixmap = QPixmap.fromImage(qt_image)

            container_size = self.video_container.size()
            if container_size.width() > 1 and container_size.height() > 1:
                scaled = pixmap.scaled(
                    container_size,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self.video_label.setPixmap(scaled)
            else:
                # 边缘情况：容器尚未布局完毕时使用帧原始尺寸
                self.video_label.setPixmap(pixmap)

            # 更新录制状态
            if self.camera.is_recording:
                self.status_overlay.setText("● 录制中")
                self.status_overlay.setStyleSheet(f"color: {ERROR}; font-size: {FONT_SIZE['sm']}; font-weight: {FONT_WEIGHT['semibold']}; background-color: transparent;")
                if "mascot_happy" in self._mascot_pixmaps:
                    self.mascot_overlay.setPixmap(self._mascot_pixmaps["mascot_happy"])
                    self.mascot_overlay.show()
            else:
                self.status_overlay.setText("预览中")
                self.status_overlay.setStyleSheet(f"color: {SUCCESS}; font-size: {FONT_SIZE['sm']}; font-weight: {FONT_WEIGHT['medium']}; background-color: transparent;")
                if "mascot_default" in self._mascot_pixmaps:
                    self.mascot_overlay.setPixmap(self._mascot_pixmaps["mascot_default"])
                    self.mascot_overlay.show()

            # 更新吉祥物位置（右下角）
            if self.mascot_overlay.isVisible():
                container_w = self.video_container.width()
                container_h = self.video_container.height()
                self.mascot_overlay.move(container_w - 56, container_h - 56)

        except Exception as e:
            logger.debug(f"Frame update error: {e}")

    def set_overlay_text(self, text: str, color: str = TEXT_TERTIARY):
        """设置叠加文字"""
        self.status_overlay.setText(text)
        self.status_overlay.setStyleSheet(f"color: {color}; font-size: {FONT_SIZE['sm']}; background-color: transparent;")
