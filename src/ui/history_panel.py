"""
历史记录面板 - 重构版
简洁的会话历史列表，支持右键删除
"""
from pathlib import Path
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QFrame, QMenu, QMessageBox,
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QAction, QPixmap, QIcon

from .styles import (
    BG_ELEVATED, BG_TERTIARY, SEPARATOR,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_TERTIARY, TEXT_DISABLED,
    SUCCESS, WARNING, ERROR, PRIMARY, PRIMARY_LIGHT, RADIUS, FONT_SIZE, FONT_WEIGHT,
    list_style,
)

ASSET_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "images"


class HistoryItemWidget(QFrame):
    """历史记录项 - Apple 暗色风格卡片 + Pill 状态标签"""

    GRADE_COLORS = {
        "S": "#FFD700",
        "A": SUCCESS,
        "B": PRIMARY,
        "C": WARNING,
        "D": ERROR,
    }

    STATUS_CONFIG = {
        "completed":  ("已完成", SUCCESS,       "rgba(52,199,89,0.10)"),
        "cancelled":  ("已取消", WARNING,       "rgba(255,149,0,0.10)"),
        "in_progress":("进行中", PRIMARY,        "rgba(232,117,58,0.10)"),
        "clipping":   ("生成中", PRIMARY_LIGHT, "rgba(245,166,35,0.10)"),
    }

    def __init__(self, session: dict, parent=None):
        super().__init__(parent)
        self.session = session
        self.setFixedHeight(90)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {BG_ELEVATED};
                border: none;
                border-radius: {RADIUS['lg']};
            }}
            QFrame:hover {{
                background-color: {BG_TERTIARY};
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(16, 12, 16, 12)

        # 左侧：圆形等级徽章
        grade = session.get("grade", "-")
        self._build_grade_badge(layout, grade)

        # 中间：信息列
        info = QVBoxLayout()
        info.setSpacing(6)
        info.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        # 日期与时长行
        header = QHBoxLayout()
        header.setSpacing(10)

        date_str = session.get("end_time") or "未知时间"
        if date_str and date_str != "未知时间":
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(date_str)
                date_str = dt.strftime("%m月%d日 %H:%M")
            except Exception:
                pass

        date_label = QLabel(date_str)
        date_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: {FONT_SIZE['sm']};")
        header.addWidget(date_label)

        duration = session.get("duration_minutes") or 0
        duration_label = QLabel(f"{duration:.0f} 分钟")
        duration_label.setStyleSheet(f"color: {TEXT_TERTIARY}; font-size: {FONT_SIZE['xs']};")
        header.addWidget(duration_label)
        header.addStretch()
        info.addLayout(header)

        # 分数
        score = session.get("total_score") or 0
        score_label = QLabel(f"{score:.1f} 分")
        score_label.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: {FONT_SIZE['lg']}; font-weight: {FONT_WEIGHT['semibold']};")
        info.addWidget(score_label)

        layout.addLayout(info, 1)

        # 右侧：Pill 状态标签
        self._build_status_pill(layout)

    def _build_grade_badge(self, layout, grade):
        """构建圆形等级徽章（图片优先，回退纯色）"""
        badge_path = ASSET_DIR / "badges" / f"badge_{grade}.jpg"
        if badge_path.exists():
            badge_label = QLabel()
            badge_label.setFixedSize(48, 48)
            badge_pixmap = QPixmap(str(badge_path)).scaled(
                48, 48,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            badge_label.setPixmap(badge_pixmap)
            badge_label.setStyleSheet("border-radius: 24px;")
            layout.addWidget(badge_label)
        else:
            grade_color = self.GRADE_COLORS.get(grade, TEXT_TERTIARY)
            grade_label = QLabel(grade)
            grade_label.setFixedSize(44, 44)
            grade_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grade_label.setStyleSheet(f"""
                color: {grade_color};
                font-size: {FONT_SIZE['md']};
                font-weight: {FONT_WEIGHT['bold']};
                background-color: rgba(0,0,0,0.05);
                border-radius: 22px;
                border: 2px solid {grade_color};
            """)
            layout.addWidget(grade_label)

    def _build_status_pill(self, layout):
        """构建 Pill 形状状态标签"""
        status = self.session.get("status") or ""
        config = self.STATUS_CONFIG.get(status, (status, TEXT_TERTIARY, "rgba(0,0,0,0.05)"))
        text, color, bg = config

        status_label = QLabel(text)
        status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_label.setFixedHeight(28)
        status_label.setStyleSheet(f"""
            color: {color};
            font-size: {FONT_SIZE['xs']};
            font-weight: {FONT_WEIGHT['semibold']};
            background-color: {bg};
            padding: 4px 14px;
            border-radius: 14px;
        """)
        layout.addWidget(status_label)


class HistoryPanel(QWidget):
    """历史记录面板"""

    def __init__(self, database, parent=None):
        super().__init__(parent)
        self.db = database
        self._init_ui()
        self.refresh()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(0, 0, 0, 0)

        # 列表
        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet(f"""
            QListWidget {{
                background-color: transparent;
                border: none;
                outline: none;
            }}
            QListWidget::item {{
                padding: 4px 0px;
                border: none;
                background: transparent;
            }}
            QListWidget::item:selected {{
                background: transparent;
            }}
        """)
        self.list_widget.setSpacing(6)
        self.list_widget.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._on_context_menu)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.list_widget, 1)

        # 空状态提示
        empty_widget = QWidget()
        empty_layout = QVBoxLayout(empty_widget)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.setSpacing(12)

        empty_img_path = ASSET_DIR / "backgrounds" / "empty_state.jpg"
        if empty_img_path.exists():
            empty_img = QLabel()
            empty_pixmap = QPixmap(str(empty_img_path)).scaled(300, 225, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            empty_img.setPixmap(empty_pixmap)
            empty_img.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(empty_img)

        empty_text = QLabel("还没有作业记录哦~\n完成一次作业后将在这里显示")
        empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_text.setStyleSheet(f"color: {TEXT_TERTIARY}; font-size: {FONT_SIZE['sm']}; padding: 8px;")
        empty_text.setWordWrap(True)
        empty_layout.addWidget(empty_text)

        self.empty_label = empty_widget
        layout.addWidget(self.empty_label)

    def _on_item_clicked(self, item: QListWidgetItem):
        """点击条目高亮"""
        # 清除其他选中，仅选中当前
        for i in range(self.list_widget.count()):
            w = self.list_widget.itemWidget(self.list_widget.item(i))
            if w:
                w.setStyleSheet(w.styleSheet().replace(
                    f"border: 1px solid {PRIMARY};",
                    f"border: 1px solid {SEPARATOR};"
                ))
        widget = self.list_widget.itemWidget(item)
        if widget:
            widget.setStyleSheet(widget.styleSheet().replace(
                f"border: 1px solid {SEPARATOR};",
                f"border: 1px solid {PRIMARY};"
            ))

    def _on_context_menu(self, pos):
        """右键菜单 — 删除"""
        item = self.list_widget.itemAt(pos)
        if item is None:
            return
        widget = self.list_widget.itemWidget(item)
        if not hasattr(widget, "session"):
            return

        session = widget.session
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {BG_ELEVATED};
                border: 1px solid {SEPARATOR};
                border-radius: {RADIUS['sm']};
                padding: 4px;
            }}
            QMenu::item {{
                padding: 6px 24px;
                color: {TEXT_PRIMARY};
            }}
            QMenu::item:selected {{
                background-color: {ERROR};
                color: white;
                border-radius: 3px;
            }}
        """)

        delete_action = QAction("删除此记录", menu)
        delete_action.triggered.connect(lambda: self._delete_session(session, item))
        menu.addAction(delete_action)
        menu.exec(self.list_widget.mapToGlobal(pos))

    def _delete_session(self, session: dict, item: QListWidgetItem):
        """删除一条历史记录"""
        sid = session.get("id")
        grade = session.get("grade", "-")
        score = session.get("total_score", 0)

        reply = QMessageBox.question(
            self, "确认删除",
            f"确定要删除这条记录吗？\n等级: {grade}  分数: {score:.1f}\n\n此操作不可撤销。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            self.db.delete_session(sid)
        except Exception as e:
            QMessageBox.critical(self, "删除失败", f"删除记录时出错: {e}")
            return

        # 从列表中移除该项
        row = self.list_widget.row(item)
        self.list_widget.takeItem(row)

        # 如果列表为空，显示空状态
        if self.list_widget.count() == 0:
            self.list_widget.hide()
            self.empty_label.show()

    def refresh(self):
        """刷新历史列表"""
        self.list_widget.clear()

        try:
            sessions = self.db.get_recent_sessions(limit=20)
        except Exception as e:
            sessions = []

        if not sessions:
            self.list_widget.hide()
            self.empty_label.show()
            return

        self.empty_label.hide()
        self.list_widget.show()

        for session in sessions:
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 90))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)

            widget = HistoryItemWidget(session)
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, widget)
