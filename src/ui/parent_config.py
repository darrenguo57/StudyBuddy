"""
家长设置对话框 - 重构版
简洁现代的配置界面
"""
from pathlib import Path
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QSpinBox, QDoubleSpinBox, QCheckBox,
    QGroupBox, QMessageBox, QFrame, QScrollArea, QWidget,
    QFileDialog, QComboBox, QLineEdit,
)
from PyQt6.QtCore import Qt

from .styles import (
    global_stylesheet, button_style, input_style, slider_style,
    BG_PRIMARY, BG_ELEVATED, BG_TERTIARY,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_TERTIARY,
    PRIMARY, SUCCESS, SEPARATOR, RADIUS, FONT_SIZE, FONT_WEIGHT,
)


class ConfigSection(QFrame):
    """配置区块 - 卡片式分组"""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {BG_ELEVATED};
                border: 1px solid {SEPARATOR};
                border-radius: {RADIUS['lg']};
                padding: 16px;
            }}
        """)

        self.layout = QVBoxLayout(self)
        self.layout.setSpacing(12)
        self.layout.setContentsMargins(16, 16, 16, 16)

        title_label = QLabel(title)
        title_label.setStyleSheet(f"""
            color: {TEXT_PRIMARY};
            font-size: {FONT_SIZE['md']};
            font-weight: {FONT_WEIGHT['semibold']};
            padding-bottom: 8px;
            border-bottom: 1px solid {SEPARATOR};
        """)
        self.layout.addWidget(title_label)

    def add_row(self, label_text: str, widget: QWidget):
        """添加一行配置项"""
        row = QHBoxLayout()
        row.setSpacing(12)
        row.setAlignment(Qt.AlignmentFlag.AlignLeft)

        label = QLabel(label_text)
        label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: {FONT_SIZE['base']};")
        label.setMinimumWidth(120)
        row.addWidget(label)

        row.addWidget(widget)
        row.addStretch()

        self.layout.addLayout(row)


class ParentConfigDialog(QDialog):
    """家长设置对话框 - 重构版"""

    def __init__(self, database, parent=None):
        super().__init__(parent)
        self.db = database

        self.setWindowTitle("家长设置")
        self.setMinimumSize(520, 600)
        self.setStyleSheet(global_stylesheet() + input_style() + slider_style())

        self._init_ui()
        self._load_config()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(16)
        main_layout.setContentsMargins(20, 20, 20, 20)

        # 标题
        header = QLabel("家长设置")
        header.setStyleSheet(f"""
            color: {TEXT_PRIMARY};
            font-size: {FONT_SIZE['xl']};
            font-weight: {FONT_WEIGHT['bold']};
            padding-bottom: 8px;
        """)
        main_layout.addWidget(header)

        # 滚动区域
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"""
            QScrollArea {{
                border: none;
                background: transparent;
            }}
        """)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(16)
        layout.setContentsMargins(0, 0, 8, 0)

        # ── 作业时长 ──
        duration_section = ConfigSection("作业时长")

        self.spin_expected = QSpinBox()
        self.spin_expected.setRange(10, 120)
        self.spin_expected.setSuffix(" 分钟")
        self.spin_expected.setStyleSheet(input_style())
        duration_section.add_row("预期时长", self.spin_expected)

        self.spin_min = QSpinBox()
        self.spin_min.setRange(5, 60)
        self.spin_min.setSuffix(" 分钟")
        duration_section.add_row("最短时长", self.spin_min)

        layout.addWidget(duration_section)

        # ── 坐姿检测 ──
        posture_section = ConfigSection("坐姿检测")

        self.spin_angle = QSpinBox()
        self.spin_angle.setRange(10, 45)
        self.spin_angle.setSuffix("°")
        posture_section.add_row("前倾角度阈值", self.spin_angle)

        self.spin_distance = QDoubleSpinBox()
        self.spin_distance.setRange(0.3, 1.0)
        self.spin_distance.setSingleStep(0.05)
        posture_section.add_row("距离阈值", self.spin_distance)

        self.spin_remind = QSpinBox()
        self.spin_remind.setRange(1, 10)
        self.spin_remind.setSuffix(" 次")
        posture_section.add_row("每节最大提醒", self.spin_remind)

        layout.addWidget(posture_section)

        # ── 语音设置 ──
        voice_section = ConfigSection("语音设置")

        self.spin_cooldown = QSpinBox()
        self.spin_cooldown.setRange(10, 120)
        self.spin_cooldown.setSuffix(" 秒")
        voice_section.add_row("提醒冷却", self.spin_cooldown)

        self.spin_encourage = QSpinBox()
        self.spin_encourage.setRange(5, 30)
        self.spin_encourage.setSuffix(" 分钟")
        voice_section.add_row("鼓励间隔", self.spin_encourage)

        layout.addWidget(voice_section)

        # ── 积分规则 ──
        score_section = ConfigSection("积分规则")

        self.spin_base = QSpinBox()
        self.spin_base.setRange(50, 200)
        self.spin_base.setSuffix(" 分")
        score_section.add_row("基础分", self.spin_base)

        self.spin_bonus = QSpinBox()
        self.spin_bonus.setRange(0, 50)
        self.spin_bonus.setSuffix(" 分")
        score_section.add_row("满分奖励", self.spin_bonus)

        layout.addWidget(score_section)

        # ── 视频设置 ──
        video_section = ConfigSection("视频设置")

        self.spin_effective = QSpinBox()
        self.spin_effective.setRange(2, 8)
        self.spin_effective.setSuffix("x")
        video_section.add_row("有效段倍速", self.spin_effective)

        self.spin_violation = QSpinBox()
        self.spin_violation.setRange(1, 4)
        self.spin_violation.setSuffix("x")
        video_section.add_row("违规段倍速", self.spin_violation)

        layout.addWidget(video_section)

        # ── 封面帧设置 ──
        cover_section = ConfigSection("封面帧")
        self.cb_cover = QCheckBox("启用封面帧")
        self.cb_cover.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: {FONT_SIZE['base']};")
        cover_section.add_row("封面开关", self.cb_cover)

        self.spin_cover_text = QSpinBox()  # 用 QLineEdit 替代更好，但为保持风格一致先用 SpinBox 占位
        # 实际上封面文案不需要 SpinBox，换一种方式：直接作为输入框
        from PyQt6.QtWidgets import QLineEdit
        # 移除占位的 spinbox，改用 QLineEdit
        cover_section.layout.removeWidget(self.spin_cover_text)
        self.spin_cover_text.deleteLater()
        self.edit_cover_text = QLineEdit()
        self.edit_cover_text.setPlaceholderText("Mila作业Vlog-第一期")
        self.edit_cover_text.setStyleSheet(input_style())
        cover_section.add_row("封面文案", self.edit_cover_text)

        # 自定义封面图
        cover_img_row = QHBoxLayout()
        cover_img_row.setSpacing(8)
        self.edit_cover_image = QLineEdit()
        self.edit_cover_image.setPlaceholderText("自动捕获最佳帧")
        self.edit_cover_image.setReadOnly(True)
        self.edit_cover_image.setStyleSheet(input_style())
        cover_img_row.addWidget(self.edit_cover_image, 1)

        self.btn_cover_browse = QPushButton("浏览...")
        self.btn_cover_browse.setStyleSheet(button_style(variant="secondary", size="sm"))
        self.btn_cover_browse.clicked.connect(self._on_cover_browse)
        cover_img_row.addWidget(self.btn_cover_browse)

        self.btn_cover_clear = QPushButton("清除")
        self.btn_cover_clear.setStyleSheet(button_style(variant="ghost", size="sm"))
        self.btn_cover_clear.clicked.connect(self._on_cover_clear)
        cover_img_row.addWidget(self.btn_cover_clear)

        cover_section.layout.addLayout(cover_img_row)

        layout.addWidget(cover_section)

        # ── 背景音乐设置 ──
        bgm_section = ConfigSection("背景音乐")
        self.cb_bgm = QCheckBox("启用背景音乐")
        self.cb_bgm.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: {FONT_SIZE['base']};")
        bgm_section.add_row("音乐开关", self.cb_bgm)

        # 默认曲库下拉框
        self.combo_bgm_preset = QComboBox()
        self.combo_bgm_preset.addItems(["自动匹配", "欢快活泼", "温暖治愈", "动感激励"])
        self.combo_bgm_preset.setStyleSheet(input_style())
        bgm_section.add_row("默认曲库", self.combo_bgm_preset)

        # BGM 文件路径 + 浏览按钮
        bgm_path_row = QHBoxLayout()
        bgm_path_row.setSpacing(8)
        self.edit_bgm_path = QLineEdit()
        self.edit_bgm_path.setPlaceholderText("使用默认曲库自动匹配")
        self.edit_bgm_path.setStyleSheet(input_style())
        self.edit_bgm_path.setReadOnly(True)
        bgm_path_row.addWidget(self.edit_bgm_path, 1)

        self.btn_bgm_browse = QPushButton("浏览...")
        self.btn_bgm_browse.setStyleSheet(button_style(variant="secondary", size="sm"))
        self.btn_bgm_browse.clicked.connect(self._on_bgm_browse)
        bgm_path_row.addWidget(self.btn_bgm_browse)

        self.btn_bgm_clear = QPushButton("清除")
        self.btn_bgm_clear.setStyleSheet(button_style(variant="ghost", size="sm"))
        self.btn_bgm_clear.clicked.connect(self._on_bgm_clear)
        bgm_path_row.addWidget(self.btn_bgm_clear)

        bgm_section.layout.addLayout(bgm_path_row)

        self.spin_bgm_volume = QSpinBox()
        self.spin_bgm_volume.setRange(5, 50)
        self.spin_bgm_volume.setSuffix("%")
        self.spin_bgm_volume.setValue(12)
        bgm_section.add_row("音量", self.spin_bgm_volume)

        layout.addWidget(bgm_section)

        layout.addStretch()
        scroll.setWidget(content)
        main_layout.addWidget(scroll, 1)

        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)
        btn_layout.addStretch()

        reset_btn = QPushButton("恢复默认")
        reset_btn.setStyleSheet(button_style(variant="ghost", size="md"))
        reset_btn.clicked.connect(self._reset_defaults)
        btn_layout.addWidget(reset_btn)

        cancel_btn = QPushButton("取消")
        cancel_btn.setStyleSheet(button_style(variant="secondary", size="md"))
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        save_btn = QPushButton("保存设置")
        save_btn.setStyleSheet(button_style(variant="primary", size="md"))
        save_btn.clicked.connect(self._save_config)
        btn_layout.addWidget(save_btn)

        main_layout.addLayout(btn_layout)

    def _load_config(self):
        """加载现有配置"""
        try:
            cfg = self.db.get_all_configs()
            self.spin_expected.setValue(cfg.get("expected_duration", 40))
            self.spin_min.setValue(cfg.get("min_duration", 10))
            self.spin_angle.setValue(cfg.get("head_angle_threshold", 25))
            self.spin_distance.setValue(cfg.get("distance_threshold", 0.5))
            self.spin_remind.setValue(cfg.get("max_reminds_per_session", 5))
            self.spin_cooldown.setValue(cfg.get("remind_cooldown", 30))
            self.spin_encourage.setValue(cfg.get("encourage_interval", 15))
            self.spin_base.setValue(cfg.get("base_score", 100))
            self.spin_bonus.setValue(cfg.get("perfect_bonus", 20))
            self.spin_effective.setValue(cfg.get("effective_speed", 4))
            self.spin_violation.setValue(cfg.get("violation_speed", 2))
            self.cb_cover.setChecked(cfg.get("cover_enabled", True))
            self.edit_cover_text.setText(cfg.get("cover_text", "Mila作业Vlog-第一期"))
            self.edit_cover_image.setText(cfg.get("cover_image_path", ""))
            self.cb_bgm.setChecked(cfg.get("bgm_enabled", True))
            self.spin_bgm_volume.setValue(int(cfg.get("bgm_volume", 12)))
            self.combo_bgm_preset.setCurrentIndex(int(cfg.get("bgm_preset", 0)))
            saved_bgm_path = cfg.get("bgm_path", "")
            self.edit_bgm_path.setText(saved_bgm_path if saved_bgm_path else "")
        except Exception as e:
            print(f"加载配置失败: {e}")

    def _save_config(self):
        """保存配置"""
        try:
            self.db.set_config("expected_duration", self.spin_expected.value())
            self.db.set_config("min_duration", self.spin_min.value())
            self.db.set_config("head_angle_threshold", self.spin_angle.value())
            self.db.set_config("distance_threshold", self.spin_distance.value())
            self.db.set_config("max_reminds_per_session", self.spin_remind.value())
            self.db.set_config("remind_cooldown", self.spin_cooldown.value())
            self.db.set_config("encourage_interval", self.spin_encourage.value())
            self.db.set_config("base_score", self.spin_base.value())
            self.db.set_config("perfect_bonus", self.spin_bonus.value())
            self.db.set_config("effective_speed", self.spin_effective.value())
            self.db.set_config("violation_speed", self.spin_violation.value())
            self.db.set_config("cover_enabled", self.cb_cover.isChecked())
            self.db.set_config("cover_text", self.edit_cover_text.text() or "Mila作业Vlog-第一期")
            self.db.set_config("cover_image_path", self.edit_cover_image.text())
            self.db.set_config("bgm_enabled", self.cb_bgm.isChecked())
            self.db.set_config("bgm_volume", self.spin_bgm_volume.value())
            self.db.set_config("bgm_preset", self.combo_bgm_preset.currentIndex())

            # BGM 路径：优先使用自定义路径，否则根据预设选择默认曲库
            custom_path = self.edit_bgm_path.text().strip()
            if custom_path:
                self.db.set_config("bgm_path", custom_path)
            else:
                preset = self.combo_bgm_preset.currentIndex()
                asset_dir = Path(__file__).resolve().parent.parent.parent / "assets" / "audio"
                preset_map = {
                    1: asset_dir / "bgm_01_happy.wav",
                    2: asset_dir / "bgm_02_warm.wav",
                    3: asset_dir / "bgm_03_energy.wav",
                }
                default_path = preset_map.get(preset, "")
                if default_path and default_path.exists():
                    self.db.set_config("bgm_path", str(default_path))
                else:
                    self.db.set_config("bgm_path", "")  # 自动匹配

            QMessageBox.information(self, "保存成功", "设置已保存。")
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"保存设置时出错: {e}")

    def _on_cover_browse(self):
        """浏览选择封面图片"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择封面图", "",
            "图片文件 (*.jpg *.jpeg *.png *.bmp);;所有文件 (*.*)"
        )
        if path:
            self.edit_cover_image.setText(path)

    def _on_cover_clear(self):
        """清除自定义封面图"""
        self.edit_cover_image.clear()

    def _on_bgm_browse(self):
        """浏览选择 BGM 文件"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择背景音乐", "", "音频文件 (*.mp3 *.wav *.ogg);;所有文件 (*.*)"
        )
        if path:
            self.edit_bgm_path.setText(path)
            self.combo_bgm_preset.setCurrentIndex(0)  # 切换为自动匹配

    def _on_bgm_clear(self):
        """清除自定义 BGM"""
        self.edit_bgm_path.clear()

    def _reset_defaults(self):
        """恢复默认"""
        reply = QMessageBox.question(
            self, "确认恢复",
            "确定要恢复所有设置为默认值吗？",
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.spin_expected.setValue(40)
            self.spin_min.setValue(10)
            self.spin_angle.setValue(25)
            self.spin_distance.setValue(0.5)
            self.spin_remind.setValue(5)
            self.spin_cooldown.setValue(30)
            self.spin_encourage.setValue(15)
            self.spin_base.setValue(100)
            self.spin_bonus.setValue(20)
            self.spin_effective.setValue(4)
            self.spin_violation.setValue(2)
            self.cb_cover.setChecked(True)
            self.edit_cover_text.setText("Mila作业Vlog-第一期")
            self.cb_bgm.setChecked(True)
            self.combo_bgm_preset.setCurrentIndex(0)
            self.edit_bgm_path.clear()
            self.spin_bgm_volume.setValue(12)
