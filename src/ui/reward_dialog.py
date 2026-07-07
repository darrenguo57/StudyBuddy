"""
奖励积分兑换对话框 - 重构版
简洁现代的兑换界面
"""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QListWidget, QListWidgetItem,
    QMessageBox, QInputDialog, QFrame,
)
from PyQt6.QtCore import Qt

from .styles import (
    global_stylesheet, button_style, list_style,
    BG_PRIMARY, BG_ELEVATED, BG_TERTIARY,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_TERTIARY, TEXT_DISABLED,
    PRIMARY_LIGHT, SUCCESS, WARNING, ERROR,
    SEPARATOR, RADIUS, FONT_SIZE, FONT_WEIGHT,
)


class RewardItemWidget(QFrame):
    """奖励项 - 卡片式展示"""

    def __init__(self, name: str, cost: int, available: int, parent=None):
        super().__init__(parent)
        self.name = name
        self.cost = cost
        self.available = available
        self.enabled = cost <= available

        self.setStyleSheet(self._get_style())

        layout = QHBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(14, 12, 14, 12)

        # 左侧：图标/名称
        info = QVBoxLayout()
        info.setSpacing(4)

        name_label = QLabel(name)
        name_label.setStyleSheet(f"""
            color: {TEXT_PRIMARY if self.enabled else TEXT_DISABLED};
            font-size: {FONT_SIZE['md']};
            font-weight: {FONT_WEIGHT['semibold']};
        """)
        info.addWidget(name_label)

        cost_label = QLabel(f"需要 {cost} 积分")
        cost_label.setStyleSheet(f"""
            color: {PRIMARY_LIGHT if self.enabled else TEXT_DISABLED};
            font-size: {FONT_SIZE['sm']};
        """)
        info.addWidget(cost_label)

        layout.addLayout(info, 1)

        # 右侧：状态
        if not self.enabled:
            status = QLabel("积分不足")
            status.setStyleSheet(f"""
                color: {TEXT_DISABLED};
                font-size: {FONT_SIZE['sm']};
                background-color: {BG_ELEVATED};
                padding: 2px 10px;
                border-radius: {RADIUS['sm']};
            """)
            layout.addWidget(status)


    def _get_style(self) -> str:
        if self.enabled:
            return f"""
                QFrame {{
                    background-color: {BG_ELEVATED};
                    border: 1px solid {SEPARATOR};
                    border-radius: {RADIUS['md']};
                }}
                QFrame:hover {{
                    background-color: {BG_TERTIARY};
                    border-color: {PRIMARY_LIGHT};
                }}
            """
        else:
            return f"""
                QFrame {{
                    background-color: {BG_SECONDARY};
                    border: 1px solid {SEPARATOR};
                    border-radius: {RADIUS['md']};
                }}
            """


class RewardDialog(QDialog):
    """奖励兑换对话框 - 重构版"""

    def __init__(self, database, monthly_score: dict, parent=None):
        super().__init__(parent)
        self.db = database
        self.monthly = monthly_score

        self.setWindowTitle("积分奖励兑换")
        self.setMinimumSize(420, 520)
        self.setStyleSheet(global_stylesheet())

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(20, 20, 20, 20)

        # 积分头部
        available = self.monthly.get("available_score", 0) if self.monthly else 0
        total = self.monthly.get("total_score", 0) if self.monthly else 0

        score_card = QFrame()
        score_card.setStyleSheet(f"""
            QFrame {{
                background-color: {BG_ELEVATED};
                border: 1px solid {SEPARATOR};
                border-radius: {RADIUS['lg']};
                padding: 16px;
            }}
        """)
        score_layout = QHBoxLayout(score_card)
        score_layout.setSpacing(16)
        score_layout.setContentsMargins(0, 0, 0, 0)

        # 可用积分
        available_layout = QVBoxLayout()
        available_label = QLabel("可用积分")
        available_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: {FONT_SIZE['sm']};")
        available_layout.addWidget(available_label)

        available_value = QLabel(f"{available:.0f}")
        available_value.setStyleSheet(f"""
            color: {PRIMARY_LIGHT};
            font-size: {FONT_SIZE['2xl']};
            font-weight: {FONT_WEIGHT['bold']};
        """)
        available_layout.addWidget(available_value)
        score_layout.addLayout(available_layout)

        score_layout.addStretch()

        # 累计积分
        total_layout = QVBoxLayout()
        total_layout.setAlignment(Qt.AlignmentFlag.AlignRight)
        total_label = QLabel("累计积分")
        total_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: {FONT_SIZE['sm']};")
        total_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        total_layout.addWidget(total_label)

        total_value = QLabel(f"{total:.0f}")
        total_value.setStyleSheet(f"""
            color: {TEXT_TERTIARY};
            font-size: {FONT_SIZE['lg']};
            font-weight: {FONT_WEIGHT['semibold']};
        """)
        total_value.setAlignment(Qt.AlignmentFlag.AlignRight)
        total_layout.addWidget(total_value)
        score_layout.addLayout(total_layout)

        layout.addWidget(score_card)

        # 奖励列表标题
        list_header = QLabel("选择奖励")
        list_header.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: {FONT_SIZE['md']}; font-weight: {FONT_WEIGHT['semibold']};")
        layout.addWidget(list_header)

        # 奖励列表
        self.list_widget = QListWidget()
        self.list_widget.setStyleSheet(list_style())
        self.list_widget.setSpacing(8)

        rewards = [
            ("多玩15分钟", 100),
            ("选晚餐菜单", 200),
            ("周末去公园", 350),
            ("买一本喜欢的书", 500),
            ("去游乐场", 800),
        ]

        for name, cost in rewards:
            item = QListWidgetItem()
            item.setSizeHint(__import__('PyQt6.QtCore', fromlist=['QSize']).QSize(0, 70))
            item.setData(Qt.ItemDataRole.UserRole, {"name": name, "cost": cost})

            widget = RewardItemWidget(name, cost, available)
            if not widget.enabled:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)

            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, widget)

        layout.addWidget(self.list_widget, 1)

        # 自定义奖励
        custom_btn = QPushButton("+ 自定义奖励")
        custom_btn.setStyleSheet(button_style(variant="ghost", size="md"))
        custom_btn.clicked.connect(self._add_custom)
        layout.addWidget(custom_btn)

        # 待审批提示
        pending = self.db.get_pending_redemptions()
        if pending:
            pending_label = QLabel(f"待审批兑换: {len(pending)} 项")
            pending_label.setStyleSheet(f"color: {WARNING}; font-size: {FONT_SIZE['sm']};")
            layout.addWidget(pending_label)

        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)
        btn_layout.addStretch()

        cancel_btn = QPushButton("关闭")
        cancel_btn.setStyleSheet(button_style(variant="secondary", size="md"))
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        exchange_btn = QPushButton("申请兑换")
        exchange_btn.setStyleSheet(button_style(variant="primary", size="md"))
        exchange_btn.clicked.connect(self._request_exchange)
        btn_layout.addWidget(exchange_btn)

        layout.addLayout(btn_layout)

    def _request_exchange(self):
        item = self.list_widget.currentItem()
        if not item:
            QMessageBox.warning(self, "提示", "请先选择要兑换的奖励。")
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        name = data["name"]
        cost = data["cost"]

        available = self.monthly.get("available_score", 0) if self.monthly else 0
        if cost > available:
            QMessageBox.warning(self, "积分不足", f"当前可用积分 {available:.0f}，该奖励需要 {cost} 分。")
            return

        reply = QMessageBox.question(
            self, "确认兑换",
            f"确定要使用 {cost} 积分兑换「{name}」吗？\n兑换后需要家长确认。",
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.db.request_redemption(name, cost)
                QMessageBox.information(self, "申请成功", f"已提交兑换申请「{name}」\n消耗 {cost} 积分\n等待家长确认。")
                self.accept()
            except Exception as e:
                QMessageBox.critical(self, "错误", f"兑换失败: {e}")

    def _add_custom(self):
        name, ok = QInputDialog.getText(self, "自定义奖励", "奖励名称:")
        if not ok or not name.strip():
            return
        cost, ok = QInputDialog.getInt(self, "积分设置", f"「{name}」需要多少积分？", 200, 50, 5000, 10)
        if not ok:
            return
        self.db.request_redemption(name.strip(), cost)
        QMessageBox.information(self, "申请成功", f"已提交自定义奖励「{name}」\n消耗 {cost} 积分\n等待家长确认。")
