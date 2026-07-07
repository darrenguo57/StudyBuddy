"""
实时状态面板 - 重构版
简洁的状态指示与消息展示
"""
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QProgressBar
from PyQt6.QtCore import Qt

from .styles import (
    BG_ELEVATED, BG_TERTIARY, SEPARATOR,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_TERTIARY,
    SUCCESS, WARNING, ERROR, PRIMARY, RADIUS, FONT_SIZE, FONT_WEIGHT,
)


class StatusIndicator(QFrame):
    """状态指示灯 - 带脉冲动画效果的圆点"""

    COLORS = {
        "normal": SUCCESS,
        "warning": WARNING,
        "critical": ERROR,
        "completed": PRIMARY,
        "idle": TEXT_TERTIARY,
    }

    LABELS = {
        "normal": "坐姿良好",
        "warning": "需要注意",
        "critical": "立即纠正",
        "completed": "已完成",
        "idle": "未开始",
    }

    def __init__(self, state: str = "idle", parent=None):
        super().__init__(parent)
        self._state = state
        self.setFixedSize(14, 14)
        self._update_style()

    def _update_style(self):
        color = self.COLORS.get(self._state, TEXT_TERTIARY)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {color};
                border-radius: 7px;
            }}
        """)

    def set_state(self, state: str):
        self._state = state
        self._update_style()


class StatusPanel(QWidget):
    """实时状态面板"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(0, 0, 0, 0)

        # 状态头部
        header = QHBoxLayout()
        header.setSpacing(10)

        self.indicator = StatusIndicator("idle")
        header.addWidget(self.indicator)

        self.state_label = QLabel("未开始")
        self.state_label.setStyleSheet(f"""
            color: {TEXT_SECONDARY};
            font-size: {FONT_SIZE['md']};
            font-weight: {FONT_WEIGHT['semibold']};
        """)
        header.addWidget(self.state_label)
        header.addStretch()

        layout.addLayout(header)

        # 分隔线
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(f"color: {SEPARATOR};")
        layout.addWidget(divider)

        # 消息区域
        self.message_label = QLabel("点击「开始作业」开始记录")
        self.message_label.setStyleSheet(f"""
            color: {TEXT_TERTIARY};
            font-size: {FONT_SIZE['base']};
            line-height: 1.6;
        """)
        self.message_label.setWordWrap(True)
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.message_label)

        layout.addStretch()

        # 剪辑进度条（默认隐藏）
        self.clip_progress = QProgressBar()
        self.clip_progress.setRange(0, 100)
        self.clip_progress.setValue(0)
        self.clip_progress.setTextVisible(True)
        self.clip_progress.setFormat("剪辑中 %p%")
        self.clip_progress.setStyleSheet(f"""
            QProgressBar {{
                background-color: {BG_TERTIARY};
                border: 1px solid {SEPARATOR};
                border-radius: {RADIUS['sm']};
                height: 20px;
                text-align: center;
                color: {TEXT_SECONDARY};
                font-size: {FONT_SIZE['sm']};
            }}
            QProgressBar::chunk {{
                background-color: {PRIMARY};
                border-radius: {RADIUS['sm']};
            }}
        """)
        self.clip_progress.hide()
        layout.addWidget(self.clip_progress)

        # 统计小标签
        stats = QHBoxLayout()
        stats.setSpacing(12)

        self.stat_violations = self._create_mini_stat("违规: 0")
        stats.addWidget(self.stat_violations)

        self.stat_reminds = self._create_mini_stat("提醒: 0")
        stats.addWidget(self.stat_reminds)

        self.stat_corrected = self._create_mini_stat("纠正: 0")
        stats.addWidget(self.stat_corrected)

        stats.addStretch()
        layout.addLayout(stats)

    def _create_mini_stat(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(f"""
            color: {TEXT_TERTIARY};
            font-size: {FONT_SIZE['sm']};
            background-color: {BG_ELEVATED};
            padding: 4px 10px;
            border-radius: {RADIUS['sm']};
        """)
        return label

    def set_posture_state(self, state: str):
        self.indicator.set_state(state)
        label = StatusIndicator.LABELS.get(state, "未知")
        self.state_label.setText(label)

        colors = {
            "normal": SUCCESS,
            "warning": WARNING,
            "critical": ERROR,
            "completed": PRIMARY,
            "idle": TEXT_TERTIARY,
        }
        color = colors.get(state, TEXT_TERTIARY)
        self.state_label.setStyleSheet(f"""
            color: {color};
            font-size: {FONT_SIZE['md']};
            font-weight: {FONT_WEIGHT['semibold']};
        """)

    def set_message(self, message: str):
        self.message_label.setText(message)

    def update_stats(self, violations: int = 0, reminds: int = 0, corrected: int = 0):
        self.stat_violations.setText(f"违规: {violations}")
        self.stat_reminds.setText(f"提醒: {reminds}")
        self.stat_corrected.setText(f"纠正: {corrected}")

    def set_focus_info(self, info: str):
        """设置专注度信息（追加到消息下方）"""
        current = self.message_label.text()
        # 如果当前消息包含专注度信息，替换它；否则追加
        if "\n" in current and "专注度" in current.split("\n")[-1]:
            current = current.rsplit("\n", 1)[0]
        if current.strip():
            self.message_label.setText(current + "\n" + info)
        else:
            self.message_label.setText(info)

    def show_clip_progress(self, progress: int = 0):
        """显示剪辑进度条"""
        self.clip_progress.setValue(progress)
        self.clip_progress.show()

    def update_clip_progress(self, progress: int):
        """更新剪辑进度"""
        self.clip_progress.setValue(progress)

    def hide_clip_progress(self):
        """隐藏剪辑进度条"""
        self.clip_progress.hide()

    def set_clip_result(self, text: str):
        """显示剪辑结果文本"""
        self.clip_progress.setFormat(text)
        self.clip_progress.setValue(100)
