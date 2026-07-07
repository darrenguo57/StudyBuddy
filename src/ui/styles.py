"""
Apple 浅色风格设计系统 (macOS/iOS Light Mode)
- 背景层级: 系统灰 -> 纯白 -> 浅灰
- 大圆角卡片 + 大量留白 + 极简字体层级
- Pill 形状标签和按钮
"""

# ═══════════════════════════════════════
# 颜色系统 - Apple Light Mode
# ═══════════════════════════════════════

# 背景层级
BG_SYSTEM = "#F2F2F7"           # 最底层系统背景 (iOS systemGray6)
BG_PRIMARY = "#FFFFFF"          # 主背景（纯白）
BG_ELEVATED = "#FFFFFF"         # 一级卡片背景（纯白卡片在灰底上）
BG_SECONDARY = "#F2F2F7"        # 二级卡片/内嵌背景/输入框
BG_TERTIARY = "#E5E5EA"         # 三级背景/悬停高亮
BG_OVERLAY = "rgba(120,120,128,0.16)"  # 玻璃拟态遮罩

# 文字层级
TEXT_PRIMARY = "#1C1C1E"        # 主标题（深黑）
TEXT_SECONDARY = "#48484A"      # 次级文字
TEXT_TERTIARY = "#8E8E93"       # 三级文字（描述）
TEXT_DISABLED = "#C7C7CC"       # 禁用/占位文字

# 强调色（保持不变）
PRIMARY = "#E8753A"             # 品牌橙红
PRIMARY_LIGHT = "#F5A623"       # 浅橙（高亮）
SUCCESS = "#34C759"             # Apple Green
WARNING = "#FF9500"             # Apple Orange
ERROR = "#FF3B30"               # Apple Red
INFO = "#0A84FF"                # Apple Blue
GOLD = "#FFD60A"                # Apple Yellow/Gold

# 分隔线/边框
SEPARATOR = "#E5E5EA"           # 微弱分隔线
BORDER = "rgba(0,0,0,0.08)"     # 边框
BORDER_ACTIVE = "rgba(0,0,0,0.15)"

# ═══════════════════════════════════════
# 字体系统
# ═══════════════════════════════════════
FONT_FAMILY = "-apple-system, 'SF Pro Display', 'SF Pro', 'PingFang SC', 'Microsoft YaHei', sans-serif"
FONT_FAMILY_MONO = "'SF Mono', 'SFMono-Regular', 'Consolas', monospace"

FONT_SIZE = {
    "xs": "11px",      # 辅助/标签
    "sm": "13px",      # 小字/说明
    "base": "15px",    # 正文
    "md": "17px",      # 中等标题
    "lg": "22px",      # 大标题
    "xl": "28px",      # 超大标题
    "2xl": "34px",     # 数字/得分
    "3xl": "48px",     # 巨大数字
}

FONT_WEIGHT = {
    "regular": "400",
    "medium": "500",
    "semibold": "600",
    "bold": "700",
}

# ═══════════════════════════════════════
# 圆角系统
# ═══════════════════════════════════════
RADIUS = {
    "sm": "6px",
    "md": "10px",
    "lg": "16px",
    "xl": "20px",
    "pill": "9999px",  # 完全圆角
}

# ═══════════════════════════════════════
# 间距系统
# ═══════════════════════════════════════
SPACING = {
    "xs": "4px",
    "sm": "8px",
    "md": "12px",
    "lg": "16px",
    "xl": "20px",
    "2xl": "24px",
    "3xl": "32px",
}

# ═══════════════════════════════════════
# 全局样式表
# ═══════════════════════════════════════

def global_stylesheet() -> str:
    return f"""
    /* 主窗口 */
    QMainWindow {{
        background-color: {BG_SYSTEM};
        font-family: {FONT_FAMILY};
    }}

    /* 滚动区域 */
    QScrollArea {{
        border: none;
        background-color: transparent;
    }}
    QScrollArea > QWidget > QWidget {{
        background-color: transparent;
    }}

    /* 滚动条 - Apple 风格 */
    QScrollBar:vertical {{
        background: transparent;
        width: 8px;
        margin: 0px;
        border-radius: 4px;
    }}
    QScrollBar::handle:vertical {{
        background: rgba(0,0,0,0.20);
        min-height: 40px;
        border-radius: 4px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: rgba(0,0,0,0.35);
    }}
    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical {{
        height: 0px;
    }}
    QScrollBar::add-page:vertical,
    QScrollBar::sub-page:vertical {{
        background: none;
    }}

    QScrollBar:horizontal {{
        background: transparent;
        height: 8px;
        margin: 0px;
        border-radius: 4px;
    }}
    QScrollBar::handle:horizontal {{
        background: rgba(0,0,0,0.20);
        min-width: 40px;
        border-radius: 4px;
    }}
    QScrollBar::handle:horizontal:hover {{
        background: rgba(0,0,0,0.35);
    }}
    QScrollBar::add-line:horizontal,
    QScrollBar::sub-line:horizontal {{
        width: 0px;
    }}

    /* 工具提示 */
    QToolTip {{
        background-color: {BG_SECONDARY};
        color: {TEXT_PRIMARY};
        border: 1px solid {SEPARATOR};
        border-radius: {RADIUS['md']};
        padding: 8px 12px;
        font-size: {FONT_SIZE['sm']};
    }}
    """

# ═══════════════════════════════════════
# 组件样式生成器
# ═══════════════════════════════════════

def card_style(bg=BG_ELEVATED, radius=RADIUS["lg"], padding="20px") -> str:
    """大圆角卡片样式"""
    return f"""
        background-color: {bg};
        border-radius: {radius};
        padding: {padding};
        border: none;
    """


def glass_card_style(bg="rgba(255,255,255,0.85)", radius=RADIUS["lg"]) -> str:
    """玻璃拟态卡片"""
    return f"""
        background-color: {bg};
        border-radius: {radius};
        border: 1px solid rgba(0,0,0,0.06);
    """


def button_style(
    variant="primary",
    size="md",
) -> str:
    """
    按钮样式
    variant: primary, secondary, success, danger, ghost, glass
    size: sm, md, lg
    """
    sizes = {
        "sm": {"padding": "6px 14px", "font": FONT_SIZE["sm"]},
        "md": {"padding": "10px 20px", "font": FONT_SIZE["base"]},
        "lg": {"padding": "14px 28px", "font": FONT_SIZE["md"]},
    }
    s = sizes.get(size, sizes["md"])

    variants = {
        "primary": {
            "bg": PRIMARY,
            "color": "#FFFFFF",
            "hover_bg": "#F0943A",
            "pressed_bg": "#C45F2E",
        },
        "secondary": {
            "bg": BG_SECONDARY,
            "color": TEXT_PRIMARY,
            "hover_bg": BG_TERTIARY,
            "pressed_bg": "#D1D1D6",
        },
        "success": {
            "bg": SUCCESS,
            "color": "#FFFFFF",
            "hover_bg": "#30D158",
            "pressed_bg": "#248A3D",
        },
        "danger": {
            "bg": ERROR,
            "color": "#FFFFFF",
            "hover_bg": "#FF5449",
            "pressed_bg": "#D93025",
        },
        "ghost": {
            "bg": "transparent",
            "color": TEXT_SECONDARY,
            "hover_bg": "rgba(0,0,0,0.05)",
            "pressed_bg": "rgba(0,0,0,0.08)",
        },
        "glass": {
            "bg": "rgba(0,0,0,0.05)",
            "color": TEXT_PRIMARY,
            "hover_bg": "rgba(0,0,0,0.08)",
            "pressed_bg": "rgba(0,0,0,0.12)",
        },
    }
    v = variants.get(variant, variants["primary"])

    return f"""
        QPushButton {{
            background-color: {v['bg']};
            color: {v['color']};
            border: none;
            border-radius: {RADIUS['pill']};
            padding: {s['padding']};
            font-family: {FONT_FAMILY};
            font-size: {s['font']};
            font-weight: {FONT_WEIGHT['semibold']};
        }}
        QPushButton:hover {{
            background-color: {v['hover_bg']};
        }}
        QPushButton:pressed {{
            background-color: {v['pressed_bg']};
        }}
        QPushButton:disabled {{
            background-color: {BG_TERTIARY};
            color: {TEXT_DISABLED};
        }}
    """


def input_style() -> str:
    """输入框样式"""
    return f"""
        QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
            background-color: {BG_SECONDARY};
            color: {TEXT_PRIMARY};
            border: 1px solid {SEPARATOR};
            border-radius: {RADIUS['md']};
            padding: 10px 14px;
            font-family: {FONT_FAMILY};
            font-size: {FONT_SIZE['base']};
        }}
        QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
            border: 1.5px solid {PRIMARY};
        }}
        QLineEdit::placeholder {{
            color: {TEXT_DISABLED};
        }}
    """


def label_style(size="base", weight="regular", color=TEXT_PRIMARY) -> str:
    """标签文字样式"""
    return f"""
        color: {color};
        font-family: {FONT_FAMILY};
        font-size: {FONT_SIZE[size]};
        font-weight: {FONT_WEIGHT[weight]};
    """


def pill_tag_style(color=PRIMARY, bg_alpha="15") -> str:
    """Pill 形状标签（状态标签）"""
    return f"""
        background-color: {color}{bg_alpha};
        color: {color};
        border-radius: {RADIUS['pill']};
        padding: 4px 12px;
        font-family: {FONT_FAMILY};
        font-size: {FONT_SIZE['xs']};
        font-weight: {FONT_WEIGHT['semibold']};
    """


def list_style() -> str:
    """列表样式"""
    return f"""
        QListWidget {{
            background-color: transparent;
            border: none;
            outline: none;
            font-family: {FONT_FAMILY};
        }}
        QListWidget::item {{
            background-color: transparent;
            border: none;
            border-radius: {RADIUS['lg']};
            padding: 4px;
            margin-bottom: 8px;
        }}
        QListWidget::item:selected {{
            background-color: {BG_TERTIARY};
        }}
        QListWidget::item:hover:!selected {{
            background-color: {BG_SECONDARY};
        }}
    """


def menu_style() -> str:
    """菜单样式"""
    return f"""
        QMenu {{
            background-color: {BG_PRIMARY};
            border: 1px solid {SEPARATOR};
            border-radius: {RADIUS['md']};
            padding: 8px;
            font-family: {FONT_FAMILY};
            font-size: {FONT_SIZE['base']};
        }}
        QMenu::item {{
            color: {TEXT_PRIMARY};
            padding: 8px 16px;
            border-radius: {RADIUS['sm']};
        }}
        QMenu::item:selected {{
            background-color: {PRIMARY};
            color: #FFFFFF;
        }}
    """


def group_box_style() -> str:
    """分组框样式"""
    return f"""
        QGroupBox {{
            background-color: {BG_ELEVATED};
            border: none;
            border-radius: {RADIUS['lg']};
            margin-top: 16px;
            padding-top: 24px;
            padding: 20px;
            font-family: {FONT_FAMILY};
            font-size: {FONT_SIZE['md']};
            font-weight: {FONT_WEIGHT['semibold']};
            color: {TEXT_PRIMARY};
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 16px;
            top: 8px;
            padding: 0 8px;
        }}
    """


def dialog_style() -> str:
    """对话框样式"""
    return f"""
        QDialog {{
            background-color: {BG_PRIMARY};
            font-family: {FONT_FAMILY};
        }}
    """


def progress_bar_style() -> str:
    """进度条样式"""
    return f"""
        QProgressBar {{
            background-color: {BG_SECONDARY};
            border: none;
            border-radius: {RADIUS['pill']};
            height: 8px;
            text-align: center;
            color: transparent;
        }}
        QProgressBar::chunk {{
            background-color: {PRIMARY};
            border-radius: {RADIUS['pill']};
        }}
    """


def slider_style() -> str:
    """滑块样式"""
    return f"""
        QSlider::groove:horizontal {{
            background: {BG_SECONDARY};
            height: 4px;
            border-radius: 2px;
        }}
        QSlider::sub-page:horizontal {{
            background: {PRIMARY};
            border-radius: 2px;
        }}
        QSlider::handle:horizontal {{
            background: {TEXT_SECONDARY};
            width: 18px;
            height: 18px;
            margin: -7px 0;
            border-radius: 9px;
        }}
        QSlider::handle:horizontal:hover {{
            background: {PRIMARY};
        }}
    """


def check_box_style() -> str:
    """复选框样式"""
    return f"""
        QCheckBox {{
            color: {TEXT_PRIMARY};
            font-family: {FONT_FAMILY};
            font-size: {FONT_SIZE['base']};
            spacing: 10px;
        }}
        QCheckBox::indicator {{
            width: 20px;
            height: 20px;
            border-radius: 6px;
            border: 2px solid {TEXT_TERTIARY};
            background: transparent;
        }}
        QCheckBox::indicator:checked {{
            background-color: {PRIMARY};
            border-color: {PRIMARY};
            image: url();
        }}
    """


def separator_style() -> str:
    """水平分隔线"""
    return f"""
        background-color: {SEPARATOR};
        max-height: 1px;
        min-height: 1px;
        border: none;
    """
