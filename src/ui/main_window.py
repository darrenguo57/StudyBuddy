"""
StudyBuddy 主窗口 - 重构版
简洁、现代、高交互性的桌面应用界面
"""
import logging
import ssl
import threading
import time
import subprocess
import json
import urllib.request
from pathlib import Path

from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QStackedWidget, QFrame,
    QMessageBox, QApplication, QScrollArea,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QSize, QThread, QObject
from PyQt6.QtGui import QFont, QIcon, QPixmap, QColor, QPainter, QLinearGradient

from .styles import (
    global_stylesheet, button_style, card_style,
    PRIMARY, PRIMARY_LIGHT, SUCCESS, WARNING, ERROR,
    BG_PRIMARY, BG_ELEVATED, TEXT_PRIMARY, TEXT_SECONDARY,
    TEXT_TERTIARY, SEPARATOR, RADIUS, FONT_SIZE, FONT_WEIGHT,
)

from .camera_widget import CameraWidget
from .status_panel import StatusPanel
from .history_panel import HistoryPanel
from .parent_config import ParentConfigDialog
from .reward_dialog import RewardDialog

from core.database import Database
from core.camera_manager import CameraManager, CameraConfig
from core.posture_detector import PostureDetector, PostureState
from core.focus_detector import FocusDetector
from core.audio_player import AudioPlayer
from core.scoring_engine import ScoringEngine
from core.video_clipper import VideoClipper, ClipConfig

logger = logging.getLogger("StudyBuddy.UI")


class AnimatedButton(QPushButton):
    """带动画效果的按钮"""

    def __init__(self, text: str, variant: str = "primary", icon_text: str = "", parent=None):
        super().__init__(text, parent)
        self.variant = variant
        self.icon_text = icon_text
        self.setStyleSheet(button_style(variant=variant, size="md"))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(40)


class ScoreBadge(QFrame):
    """评分徽章 - 圆形等级展示"""

    def __init__(self, grade: str = "-", score: float = 0.0, badge_size: int = 80, parent=None):
        super().__init__(parent)
        self.grade = grade
        self.score = score
        self._badge_size = badge_size
        self.setFixedSize(badge_size, badge_size)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {BG_ELEVATED};
                border: 2px solid {self._grade_color()};
                border-radius: 40px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.grade_label = QLabel(self.grade)
        self.grade_label.setStyleSheet(f"""
            color: {self._grade_color()};
            font-size: 28px;
            font-weight: {FONT_WEIGHT['bold']};
        """)
        self.grade_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.grade_label)

    def _grade_color(self) -> str:
        colors = {
            "A+": SUCCESS, "A-": "#66BB6A", "A": SUCCESS,
            "B": PRIMARY_LIGHT,
        }
        return colors.get(self.grade, TEXT_TERTIARY)

    def update_grade(self, grade: str, score: float):
        self.grade = grade
        self.score = score
        self.grade_label.setText(grade)
        color = self._grade_color()
        self.grade_label.setStyleSheet(f"""
            color: {color};
            font-size: 28px;
            font-weight: {FONT_WEIGHT['bold']};
        """)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {BG_ELEVATED};
                border: 2px solid {color};
                border-radius: 40px;
            }}
        """)


class StatCard(QFrame):
    """统计卡片 - 简洁的数据展示"""

    value_changed = pyqtSignal(str)

    def __init__(self, label: str, value: str = "0", unit: str = "", color: str = TEXT_PRIMARY, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {BG_ELEVATED};
                border: 1px solid {SEPARATOR};
                border-radius: {RADIUS['lg']};
                padding: 16px;
            }}
            QFrame:hover {{
                border-color: {SEPARATOR};
                background-color: {BG_ELEVATED};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(16, 16, 16, 16)

        self.label = QLabel(label)
        self.label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: {FONT_SIZE['sm']};")
        layout.addWidget(self.label)

        value_layout = QHBoxLayout()
        value_layout.setSpacing(4)
        value_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self.value_label = QLabel(value)
        self.value_label.setStyleSheet(f"""
            color: {color};
            font-size: {FONT_SIZE['2xl']};
            font-weight: {FONT_WEIGHT['bold']};
        """)
        value_layout.addWidget(self.value_label)

        if unit:
            self.unit_label = QLabel(unit)
            self.unit_label.setStyleSheet(f"color: {TEXT_TERTIARY}; font-size: {FONT_SIZE['sm']}; padding-top: 8px;")
            value_layout.addWidget(self.unit_label)

        value_layout.addStretch()
        layout.addLayout(value_layout)

    def set_value(self, value: str):
        self.value_label.setText(value)


class SessionTimer(QFrame):
    """会话计时器 - 大字体时间展示"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"""
            QFrame {{
                background-color: {BG_ELEVATED};
                border: 1px solid {SEPARATOR};
                border-radius: {RADIUS['lg']};
                padding: 20px;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.label = QLabel("本次作业时长")
        self.label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: {FONT_SIZE['sm']};")
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.label)

        self.time_label = QLabel("00:00")
        self.time_label.setStyleSheet(f"""
            color: {TEXT_PRIMARY};
            font-size: {FONT_SIZE['3xl']};
            font-weight: {FONT_WEIGHT['bold']};
            font-family: 'JetBrains Mono', monospace;
        """)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.time_label)

        self.status_label = QLabel("准备就绪")
        self.status_label.setStyleSheet(f"color: {TEXT_TERTIARY}; font-size: {FONT_SIZE['sm']};")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.status_label)

    def set_time(self, minutes: int, seconds: int):
        self.time_label.setText(f"{minutes:02d}:{seconds:02d}")

    def set_status(self, text: str, color: str = TEXT_TERTIARY):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color}; font-size: {FONT_SIZE['sm']};")


class ClipWorker(QObject):
    """视频剪辑工作线程"""
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, clipper, raw_path: Path, output_path: Path, posture_events: list, score_report: dict, best_frame_path: str = None, mobile_video_path: str = None):
        super().__init__()
        self.clipper = clipper
        self.raw_path = raw_path
        self.output_path = output_path
        self.posture_events = posture_events
        self.score_report = score_report
        self.best_frame_path = best_frame_path
        self.mobile_video_path = mobile_video_path

    def run(self):
        try:
            result = self.clipper.clip(
                raw_video_path=self.raw_path,
                output_path=self.output_path,
                posture_events=self.posture_events,
                score_report=self.score_report,
                best_frame_path=self.best_frame_path,
                progress_callback=self._on_progress,
                mobile_video_path=self.mobile_video_path,
            )
            self.finished.emit(str(result))
        except Exception as e:
            logger.error(f"视频剪辑失败: {e}")
            self.error.emit(str(e))

    def _on_progress(self, percent: int, message: str):
        self.progress.emit(percent, message)


class MainWindow(QMainWindow):
    """StudyBuddy 主窗口"""

    _camera_connected_signal = pyqtSignal(bool)

    def __init__(self, db_path: Path = None):
        super().__init__()
        self.setWindowTitle("StudyBuddy - 智能作业陪伴")

        # 屏幕自适应窗口尺寸
        screen = QApplication.primaryScreen().availableGeometry()
        screen_w, screen_h = screen.width(), screen.height()
        target_w = max(1024, min(int(screen_w * 0.85), screen_w))
        target_h = max(680, min(int(screen_h * 0.88), screen_h))
        self.setMinimumSize(900, 600)
        self.resize(target_w, target_h)
        self._target_w = target_w

        # 设置应用图标
        icon_path = Path(__file__).resolve().parent.parent.parent / "assets" / "images" / "icons" / "app_icon.jpg"
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        # 全局样式
        self.setStyleSheet(global_stylesheet())

        # ===== Phase 1: 摄像头优先（快速创建） =====
        self._init_camera()

        # ===== Phase 2: 数据库（快速） =====
        self._init_db(db_path)
        self._core_ready = False  # 重模块初始化的就绪标记

        # ===== Phase 3: 构建 UI（仅依赖 camera 和 db） =====
        self._init_ui()

        # ===== Phase 4: 立即启动摄像头连接（后台线程，不阻塞 UI） =====
        self._camera_connected_signal.connect(self._on_camera_connected)
        self._do_start_camera_preview()

        # ===== Phase 5: 耗时模块后台加载 =====
        self._init_heavy_async(db_path)

        # ===== Phase 6: 定时器 =====
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._on_timer_tick)
        self._timer.start(1000)

        self._session_start_time = None
        self._session_seconds = 0

        # BGM 路径
        self._bgm_path = Path(__file__).resolve().parent.parent.parent / "assets" / "audio" / "NIGHT DANCER - imase.mp3"

        # 基础运行状态标志（定时器回调依赖，需提前初始化）
        self._is_running = False
        self._is_paused = False

    def _init_camera(self):
        """仅创建摄像头管理器（不连接，不探测）"""
        import json
        config_path = Path(__file__).resolve().parent.parent.parent / "config" / "settings.json"
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                self._config = json.load(f)
        else:
            self._config = {}

        camera_cfg = self._config.get("camera", {})
        self.camera = CameraManager(
            CameraConfig(
                index=camera_cfg.get("index", 0),
                record_fps=camera_cfg.get("record_fps", 15),
            ),
            Path(__file__).resolve().parent.parent.parent / "recordings",
        )

    def _init_db(self, db_path: Path):
        """数据库初始化（快速）"""
        self.db = Database(db_path)
        self._db_path = db_path

    def _init_heavy_async(self, db_path: Path):
        """耗时核心模块在后台线程初始化（不阻塞 UI 和摄像头预览）"""
        config = self._config

        def _worker():
            logger.info("后台加载核心模块…")
            # 姿势检测器
            self.detector = PostureDetector(config=config.get("posture", {}))
            # 专注度检测器（Face Mesh 初始化 ~1.5s，是主要耗时项）
            self.focus_detector = FocusDetector(config=config.get("focus", {}))
            # 语音播报
            self.voice = AudioPlayer()
            # 评分引擎
            self.scoring = ScoringEngine(config=config.get("scoring", {}))
            # 视频剪辑器
            video_cfg = config.get("video", {})
            db_cfg = self.db.get_all_configs()
            bgm_path = db_cfg.get("bgm_path", "")
            if bgm_path and not Path(bgm_path).exists():
                bgm_path = ""
            self.clipper = VideoClipper(ClipConfig(
                effective_speed=video_cfg.get("speed_factor", 4.0),
                output_fps=video_cfg.get("output_fps", 30),
                bitrate=video_cfg.get("output_bitrate", "4000k"),
                cover_enabled=db_cfg.get("cover_enabled", True),
                cover_text=db_cfg.get("cover_text", "Mila作业Vlog-第一期"),
                cover_image_path=db_cfg.get("cover_image_path", ""),
                bgm_path=bgm_path,
                bgm_volume=float(db_cfg.get("bgm_volume", 12)) / 100.0,
            ))

            self._session_id = None
            self._is_running = False
            self._is_paused = False
            self._posture_events = []
            self._focus_events = []
            self._violation_last_ts: Dict[str, float] = {}

            self._posture_lock = threading.Lock()
            self._focus_lock = threading.Lock()
            self._latest_posture = None
            self._latest_focus = None
            self._posture_frame_skip = 0

            from core.audio_player import ensure_audio_exists
            if not ensure_audio_exists():
                logger.warning("预录制音频文件缺失！请运行 scripts/generate_audio.py 生成语音文件。")

            self._core_ready = True
            logger.info("核心模块全部就绪")

        threading.Thread(target=_worker, daemon=True, name="core-init").start()

    # ──────────────────────────────────────────
    # UI 构建
    # ──────────────────────────────────────────

    def _init_ui(self):
        """构建主界面 - 三栏布局"""
        central = QWidget()
        self.setCentralWidget(central)

        main_layout = QHBoxLayout()
        main_layout.setSpacing(16)
        main_layout.setContentsMargins(16, 16, 16, 16)

        # ── 左侧：摄像头与计时 ──
        left_panel = self._build_left_panel()
        main_layout.addWidget(left_panel, 3)

        # ── 中间：状态面板 ──
        center_panel = self._build_center_panel()
        main_layout.addWidget(center_panel, 2)

        # ── 右侧：历史记录 ──
        right_panel = self._build_right_panel()
        main_layout.addWidget(right_panel, 2)

        # 动态调整三栏比例
        self._adjust_stretch_ratio(main_layout)

        # 用 QScrollArea 包裹主内容，确保低分辨率下可滚动
        content_widget = QWidget()
        content_widget.setLayout(main_layout)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setWidget(content_widget)

        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.addWidget(scroll_area)

    def _adjust_stretch_ratio(self, layout):
        """根据窗口宽度动态调整三栏比例"""
        w = self._target_w
        if w >= 1300:
            layout.setStretch(0, 3)
            layout.setStretch(1, 2)
            layout.setStretch(2, 2)
        else:
            layout.setStretch(0, 4)
            layout.setStretch(1, 2)
            layout.setStretch(2, 2)

    def _build_left_panel(self) -> QWidget:
        """构建左侧面板 - 摄像头 + 控制按钮"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(16)
        layout.setContentsMargins(0, 0, 0, 0)

        # 摄像头区域
        self.camera_widget = CameraWidget(self.camera)
        self.camera_widget.setStyleSheet(f"""
            QWidget {{
                background-color: {BG_ELEVATED};
                border: 1px solid {SEPARATOR};
                border-radius: {RADIUS['lg']};
            }}
        """)
        layout.addWidget(self.camera_widget, 1)

        # 计时器
        self.timer_widget = SessionTimer()
        layout.addWidget(self.timer_widget)

        # 控制按钮区
        controls = QHBoxLayout()
        controls.setSpacing(12)

        asset_dir = Path(__file__).resolve().parent.parent.parent / "assets" / "images" / "icons"

        self.btn_start = AnimatedButton("开始作业", variant="success", icon_text="▶")
        self.btn_start.clicked.connect(self._on_start)
        start_icon = asset_dir / "icon_play.png"
        if start_icon.exists():
            self.btn_start.setIcon(QIcon(str(start_icon)))
            self.btn_start.setIconSize(QSize(20, 20))
        controls.addWidget(self.btn_start)

        self.btn_pause = AnimatedButton("暂停", variant="secondary")
        self.btn_pause.clicked.connect(self._on_pause)
        self.btn_pause.setEnabled(False)
        pause_icon = asset_dir / "icon_pause.png"
        if pause_icon.exists():
            self.btn_pause.setIcon(QIcon(str(pause_icon)))
            self.btn_pause.setIconSize(QSize(20, 20))
        controls.addWidget(self.btn_pause)

        self.btn_stop = AnimatedButton("结束", variant="danger")
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_stop.setEnabled(False)
        stop_icon = asset_dir / "icon_stop.png"
        if stop_icon.exists():
            self.btn_stop.setIcon(QIcon(str(stop_icon)))
            self.btn_stop.setIconSize(QSize(20, 20))
        controls.addWidget(self.btn_stop)

        controls.addStretch()

        self.btn_reward = AnimatedButton("兑换奖励", variant="primary")
        self.btn_reward.clicked.connect(self._on_reward)
        reward_icon = asset_dir / "icon_reward.png"
        if reward_icon.exists():
            self.btn_reward.setIcon(QIcon(str(reward_icon)))
            self.btn_reward.setIconSize(QSize(20, 20))
        controls.addWidget(self.btn_reward)

        layout.addLayout(controls)

        return panel

    def _build_center_panel(self) -> QWidget:
        """构建中间面板 - 实时状态 + 评分"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(16)
        layout.setContentsMargins(0, 0, 0, 0)

        # 评分徽章
        score_header = QHBoxLayout()
        score_header.setSpacing(16)
        score_header.setAlignment(Qt.AlignmentFlag.AlignLeft)

        badge_size = max(60, min(80, self._target_w // 16))
        self.score_badge = ScoreBadge("-", 0, badge_size=badge_size)
        score_header.addWidget(self.score_badge)

        score_info = QVBoxLayout()
        score_info.setSpacing(4)

        self.score_title = QLabel("综合评分")
        self.score_title.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: {FONT_SIZE['sm']};")
        score_info.addWidget(self.score_title)

        self.score_detail = QLabel("开始作业后将显示评分")
        self.score_detail.setStyleSheet(f"color: {TEXT_TERTIARY}; font-size: {FONT_SIZE['sm']};")
        score_info.addWidget(self.score_detail)

        score_header.addLayout(score_info)
        score_header.addStretch()
        layout.addLayout(score_header)

        # 统计卡片网格
        stats_grid = QHBoxLayout()
        stats_grid.setSpacing(12)

        self.stat_duration = StatCard("作业时长", "0", "分钟", PRIMARY)
        stats_grid.addWidget(self.stat_duration)

        self.stat_posture = StatCard("坐姿合规", "100%", "", SUCCESS)
        stats_grid.addWidget(self.stat_posture)

        self.stat_focus = StatCard("专注时长", "100%", "", PRIMARY_LIGHT)
        stats_grid.addWidget(self.stat_focus)

        layout.addLayout(stats_grid)

        # 状态面板
        self.status_panel = StatusPanel()
        self.status_panel.setStyleSheet(f"""
            QWidget {{
                background-color: {BG_ELEVATED};
                border: 1px solid {SEPARATOR};
                border-radius: {RADIUS['lg']};
                padding: 16px;
            }}
        """)
        layout.addWidget(self.status_panel, 1)

        # 底部操作
        bottom = QHBoxLayout()
        bottom.setSpacing(12)

        self.btn_config = AnimatedButton("家长设置", variant="ghost")
        self.btn_config.clicked.connect(self._on_config)
        asset_dir = Path(__file__).resolve().parent.parent.parent / "assets" / "images" / "icons"
        settings_icon = asset_dir / "icon_settings.png"
        if settings_icon.exists():
            self.btn_config.setIcon(QIcon(str(settings_icon)))
            self.btn_config.setIconSize(QSize(18, 18))
        bottom.addWidget(self.btn_config)

        self.btn_history = AnimatedButton("查看历史", variant="ghost")
        self.btn_history.clicked.connect(self._on_history)
        history_icon = asset_dir / "icon_history.png"
        if history_icon.exists():
            self.btn_history.setIcon(QIcon(str(history_icon)))
            self.btn_history.setIconSize(QSize(18, 18))
        bottom.addWidget(self.btn_history)

        bottom.addStretch()

        self.btn_review = AnimatedButton("查看回顾", variant="secondary")
        self.btn_review.clicked.connect(self._on_review)
        self.btn_review.setEnabled(False)
        bottom.addWidget(self.btn_review)

        layout.addLayout(bottom)

        return panel

    def _build_right_panel(self) -> QWidget:
        """构建右侧面板 - 历史记录"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(12)
        layout.setContentsMargins(0, 0, 0, 0)

        # 标题
        header = QHBoxLayout()
        title = QLabel("历史记录")
        title.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: {FONT_SIZE['lg']}; font-weight: {FONT_WEIGHT['semibold']};")
        header.addWidget(title)
        header.addStretch()

        refresh_btn = AnimatedButton("刷新", variant="ghost")
        refresh_btn.setFixedWidth(80)
        refresh_btn.clicked.connect(self._refresh_history)
        header.addWidget(refresh_btn)

        layout.addLayout(header)

        # 历史列表面板
        self.history_panel = HistoryPanel(self.db)
        self.history_panel.setStyleSheet(f"""
            QWidget {{
                background-color: {BG_ELEVATED};
                border: 1px solid {SEPARATOR};
                border-radius: {RADIUS['lg']};
            }}
        """)
        layout.addWidget(self.history_panel, 1)

        return panel

    # ──────────────────────────────────────────
    # 事件处理
    # ──────────────────────────────────────────

    def _start_camera_preview(self):
        """启动时自动连接摄像头并开始预览（延迟执行避免阻塞UI启动）"""
        import logging
        logger = logging.getLogger("StudyBuddy.UI")
        # 使用 QTimer 延迟连接，避免启动时阻塞主线程
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(500, self._do_start_camera_preview)

    def _do_start_camera_preview(self):
        """实际连接摄像头的逻辑 - 在后台线程执行，避免阻塞UI"""
        import logging
        logger = logging.getLogger("StudyBuddy.UI")
        logger.info("开始连接摄像头...")

        def _connect_worker():
            try:
                ok = self.camera.connect()
                if ok:
                    self.camera.start_preview()
                    logger.info("摄像头连接成功，预览线程已启动")
                    # 使用信号在主线程更新UI
                    self._camera_connected_signal.emit(True)
                else:
                    logger.warning("摄像头连接失败")
                    self._camera_connected_signal.emit(False)
            except Exception as e:
                logger.error(f"摄像头连接异常: {e}")
                self._camera_connected_signal.emit(False)

        threading.Thread(target=_connect_worker, daemon=True).start()

    def _on_camera_connected(self, success: bool):
        """摄像头连接结果回调（在主线程执行）"""
        if success:
            self.camera_widget.status_overlay.setText("预览中")
            self.camera_widget.status_overlay.setStyleSheet(
                f"color: {SUCCESS}; font-size: {FONT_SIZE['sm']}; background-color: transparent;"
            )
            self.status_panel.set_message("摄像头已连接，请坐好准备开始")
        else:
            self.camera_widget.status_overlay.setText("未连接")
            self.camera_widget.status_overlay.setStyleSheet(
                f"color: {WARNING}; font-size: {FONT_SIZE['sm']}; background-color: transparent;"
            )
            self.status_panel.set_message("摄像头未连接，请检查设备")

    def _start_bgm(self):
        """开始循环播放背景音乐"""
        if not self._bgm_path.exists():
            logger.warning(f"BGM文件不存在: {self._bgm_path}")
            return
        try:
            import pygame
            pygame.mixer.init()
            pygame.mixer.music.load(str(self._bgm_path))
            pygame.mixer.music.play(-1)  # -1 = 无限循环
            logger.info("BGM 开始播放")
        except Exception as e:
            logger.warning(f"BGM播放失败: {e}")

    def _stop_bgm(self):
        """停止背景音乐"""
        try:
            import pygame
            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
                logger.info("BGM 已停止")
        except Exception as e:
            logger.warning(f"BGM停止失败: {e}")

    def _on_start(self):
        """开始作业会话"""
        if not getattr(self, '_core_ready', False):
            QMessageBox.information(self, "请稍候", "核心模块正在加载中，请稍后再试…")
            return
        if not self.camera.is_connected:
            if not self.camera.connect():
                QMessageBox.warning(self, "摄像头未就绪", "无法打开摄像头，请检查设备连接。")
                return
            self.camera.start_preview()
            self.camera_widget.status_overlay.setText("预览中")
            self.camera_widget.status_overlay.setStyleSheet(
                f"color: {SUCCESS}; font-size: {FONT_SIZE['sm']}; background-color: transparent;"
            )

        self._session_id = self.db.create_session()
        self.camera.start_recording()
        self.detector.reset_session()
        self.focus_detector.reset()
        self._posture_frame_skip = 0
        self.camera.on_frame = self._on_posture_frame  # 坐姿+专注度检测回调
        self.voice.reset()
        self.voice.intro()  # 非阻塞，通过工作线程播报

        self._is_running = True
        self._is_paused = False
        self._session_start_time = time.time()
        self._session_seconds = 0
        self._posture_events = []
        self._focus_events = []
        self._violation_last_ts = {}    # 重置去重计时器

        # 分级提醒追踪: {violation_type: {"start": timestamp, "stage": "remind"|"alarm", "last_voice": timestamp}}
        self._violation_tracking: dict = {}
        self._voice_cooldown = 8        # 温和提醒最小间隔(秒)
        self._alarm_threshold = 12      # 持续违规多久升级为警报(秒)
        self._alarm_interval = 4        # 警报最小间隔(秒)
        self._reminder_count = 0        # 本次会话累计提醒次数
        self._correction_count = 0      # 本次会话累计纠正次数
        self._best_frame = None         # 最佳封面帧（numpy array）
        self._best_frame_score = -1.0   # 最佳封面帧评分

        self.btn_start.setEnabled(False)
        self.btn_pause.setEnabled(True)
        self.btn_stop.setEnabled(True)
        self.btn_review.setEnabled(False)

        self.timer_widget.set_status("作业进行中...", SUCCESS)
        self.status_panel.set_posture_state("normal")
        self.status_panel.set_message("坐姿良好，继续保持！")

        self._refresh_history()
        logger.info(f"Session {self._session_id} started")

        # 通知手机端开始录制
        self._notify_mobile_start()

        # 开始循环播放 BGM
        self._start_bgm()

    def _on_pause(self):
        """暂停/恢复"""
        if not self._is_paused:
            self.camera.pause_recording()
            self._is_paused = True
            self.btn_pause.setText("继续")
            self.timer_widget.set_status("已暂停", WARNING)
            self.status_panel.set_message("作业已暂停")
        else:
            self.camera.resume_recording()
            self._is_paused = False
            self.btn_pause.setText("暂停")
            self.timer_widget.set_status("作业进行中...", SUCCESS)
            self.status_panel.set_message("继续加油！")

    def _on_stop(self):
        """结束会话"""
        if not self._is_running:
            return

        reply = QMessageBox.question(
            self, "确认结束",
            "确定要结束本次作业吗？\n系统将生成评分和回顾视频。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._is_running = False
        self._is_paused = False

        # 停止 BGM
        self._stop_bgm()

        self.camera.on_frame = None  # 停止检测回调
        self._violation_tracking.clear()  # 清空分级提醒追踪

        raw_path = self.camera.stop_recording()

        # 合并音频（AVI + WAV → MP4）
        wav_path = self.camera.get_audio_wav_path()
        if raw_path and raw_path.exists() and wav_path and wav_path.exists():
            mp4_path = raw_path.parent / f"{raw_path.stem}_audio.mp4"
            merged = self._merge_av_to_mp4(raw_path, wav_path, mp4_path)
            if merged:
                raw_path = mp4_path  # 后续剪辑用带音频的 MP4

        duration = self._session_seconds / 60.0
        with self._posture_lock:
            events_copy = self._posture_events.copy()
        with self._focus_lock:
            focus_events_copy = self._focus_events.copy()
        report = self.scoring.calculate(
            session_id=self._session_id,
            duration_minutes=duration,
            posture_events=events_copy,
            focus_events=focus_events_copy,
            session_start=self._session_start_time,
            session_end=time.time(),
        )

        # 先保存基础结果（raw视频路径，状态为 clipping）
        self.db.end_session(
            session_id=self._session_id,
            duration_minutes=duration,
            posture_rate=report.posture_rate,
            focus_rate=report.focus_rate,
            efficiency_score=report.efficiency_score,
            correction_rate=report.correction_rate,
            total_score=report.total_score,
            grade=report.grade,
            video_path="",
            raw_video_path=str(raw_path) if raw_path else "",
            status="clipping",
        )

        # 先保存最佳封面帧
        best_frame_path = None
        if self._best_frame is not None and raw_path and raw_path.exists():
            import cv2
            best_frame_path = str(raw_path.parent / f"cover_{raw_path.stem}.png")
            cv2.imwrite(best_frame_path, self._best_frame)

        # 启动后台剪辑线程
        self._last_raw_path = str(raw_path) if raw_path else ""
        self._last_score_report = report

        # 停止手机端录制并获取路径
        mobile_video_path = self._notify_mobile_stop()

        if raw_path and raw_path.exists():
            output_path = raw_path.parent / f"review_{raw_path.stem}.mp4"
            # 归一化时间戳为会话相对偏移
            start_ts = self._session_start_time
            clip_events = [
                {**e, "timestamp": max(0, e.get("timestamp", 0) - start_ts)}
                for e in events_copy
            ]
            self._start_clip_worker(raw_path, output_path, clip_events, report, best_frame_path, mobile_video_path)
        else:
            self._on_clip_finished(None, report)

        # 更新评分展示
        self.score_badge.update_grade(report.grade, report.total_score)
        self.score_detail.setText(f"{report.total_score:.1f} 分 | {report.duration_minutes:.0f} 分钟")
        self.stat_duration.set_value(f"{duration:.0f}")
        self.stat_posture.set_value(f"{report.posture_rate*100:.0f}%")
        self.stat_focus.set_value(f"{report.focus_rate*100:.0f}%")

        self.status_panel.set_posture_state("completed")
        self.status_panel.set_message(f"本次作业完成！等级: {report.grade} | 正在生成回顾视频...")
        self.voice.complete(duration, report.grade)

        self.btn_start.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.btn_review.setEnabled(False)  # 剪辑完成前禁用
        self.btn_pause.setText("暂停")

        self.timer_widget.set_status("作业完成", PRIMARY)
        self._refresh_history()

        # 启用进度条指示剪辑状态
        self.status_panel.show_clip_progress(0)

        self.camera_widget.set_overlay_text("预览中", SUCCESS)

    def _on_timer_tick(self):
        """每秒定时更新"""
        t_start = time.time()
        if self._is_running and not self._is_paused:
            self._session_seconds += 1
            minutes = self._session_seconds // 60
            seconds = self._session_seconds % 60
            self.timer_widget.set_time(minutes, seconds)
            self.stat_duration.set_value(str(minutes))

            # 真实坐姿检测更新
            t1 = time.time()
            self._update_posture_status()
            dt1 = time.time() - t1
            if dt1 > 0.2:
                logger.warning(f"[主线程] _update_posture_status 耗时 {dt1*1000:.0f}ms")

            # 专注度 UI 更新
            self._update_focus_status()

            # 手机端姿态提醒检查（每5秒轮询一次）
            if self._session_seconds % 5 == 0:
                self._check_mobile_posture_alert()

            # 鼓励语音
            if minutes > 0 and minutes % 15 == 0 and seconds == 0:
                self.voice.encourage(minutes)

        total = time.time() - t_start
        if total > 0.5:
            logger.warning(f"[主线程] _on_timer_tick 总耗时 {total*1000:.0f}ms")

    def _update_focus_status(self):
        """更新专注度 UI 显示"""
        with self._focus_lock:
            focus = self._latest_focus
        if focus is None:
            return

        # 更新专注度统计卡片（平滑显示）
        score_pct = int(focus.focus_score * 100)
        self.stat_focus.set_value(f"{score_pct}%")

        # 更新状态面板专注度信息
        status_msg = f"专注度 {score_pct}%"
        if focus.is_drowsy:
            status_msg += " | 似乎有点困"
        elif focus.is_distracted:
            status_msg += f" | 视线{focus.gaze_direction}"
        elif focus.gaze_direction == "closed":
            status_msg += " | 眼睛闭着"
        self.status_panel.set_focus_info(status_msg)

    def _on_posture_frame(self, frame):
        """摄像头帧回调：执行坐姿检测+专注度检测（在采集线程中运行，每5帧采样1次）"""
        if not self._is_running or self._is_paused:
            return
        self._posture_frame_skip += 1
        if self._posture_frame_skip % 5 != 0:
            return  # 1/5 采样率避免拖慢采集线程

        # 1. 坐姿检测
        t0 = time.time()
        try:
            posture_result = self.detector.process(frame)
        except Exception:
            posture_result = None
        dt_posture = time.time() - t0
        if dt_posture > 0.15:
            logger.warning(f"[检测] posture.process 耗时 {dt_posture*1000:.0f}ms")

        if posture_result is not None:
            with self._posture_lock:
                self._latest_posture = posture_result
                now_ts = getattr(posture_result, 'timestamp', time.time())
                for v in posture_result.violations:
                    vtype = v.violation_type
                    last_ts = self._violation_last_ts.get(vtype, 0)
                    if now_ts - last_ts < 5.0:
                        continue  # 5 秒内同类型只记录一次
                    self._violation_last_ts[vtype] = now_ts
                    self._posture_events.append({
                        "timestamp": v.timestamp,
                        "violation_type": v.violation_type,
                        "severity": v.severity,
                        "angle": v.angle,
                        "reminded": 1,
                        "corrected": 0,
                        "corrected_time": 0,
                    })

        # 2. 专注度检测（复用同一帧，减少重复转换开销）
        t0 = time.time()
        try:
            focus_result = self.focus_detector.process(frame)
        except Exception:
            focus_result = None
        dt_focus = time.time() - t0
        if dt_focus > 0.15:
            logger.warning(f"[检测] focus.process 耗时 {dt_focus*1000:.0f}ms")
        if focus_result is not None:
            with self._focus_lock:
                self._latest_focus = focus_result
                self._focus_events.append({
                    "timestamp": time.time(),
                    "focus_score": focus_result.focus_score,
                    "gaze_direction": focus_result.gaze_direction,
                    "blink_rate": focus_result.blink_rate,
                    "is_drowsy": focus_result.is_drowsy,
                    "is_distracted": focus_result.is_distracted,
                    "face_visible": focus_result.face_visible,
                })

        # 3. 最佳封面帧追踪（坐姿正常 + 专注度最高）
        if posture_result is not None and posture_result.state.value == "normal" and focus_result is not None:
            focus_bonus = focus_result.focus_score if focus_result.face_visible else 0.3
            combined = focus_bonus
            if combined > self._best_frame_score:
                self._best_frame_score = combined
                self._best_frame = frame.copy()

    def _update_posture_status(self):
        """从检测器读取最新坐姿结果并更新UI（主线程），带分级提醒与升级警报"""
        with self._posture_lock:
            posture = self._latest_posture
        if posture is None:
            return

        # UI 更新
        if posture.state.value == "normal":
            self.status_panel.set_posture_state("normal")
            self.status_panel.set_message("坐姿良好，继续保持！")
        elif posture.state.value == "warning":
            self.status_panel.set_posture_state("warning")
            self.status_panel.set_message("注意坐姿，稍微调整一下")
        elif posture.state.value == "critical":
            self.status_panel.set_posture_state("critical")
            self.status_panel.set_message("坐姿需要纠正！")

        # ========== 分级提醒与升级警报（纯语音，不弹窗） ==========
        now = time.time()
        current_violations = {v.violation_type for v in posture.violations}

        # 1. 清除已纠正的违规追踪
        for vtype in list(self._violation_tracking.keys()):
            if vtype not in current_violations:
                self._correction_count += 1
                del self._violation_tracking[vtype]

        # 2. 处理当前违规
        for vtype in current_violations:
            if vtype not in self._violation_tracking:
                # 新违规：首次温和提醒
                self._violation_tracking[vtype] = {
                    "start": now, "stage": "remind", "last_voice": now
                }
                self.voice.remind_violation(vtype)
                self._reminder_count += 1
            else:
                track = self._violation_tracking[vtype]
                duration = now - track["start"]

                if track["stage"] == "remind" and duration >= self._alarm_threshold:
                    # 升级到警报阶段
                    track["stage"] = "alarm"
                    track["last_voice"] = 0  # 立即触发首次警报
                    self.voice.alarm_violation(vtype)

                elif track["stage"] == "alarm":
                    # 警报阶段：间隔 alarm_interval 重复骚扰
                    if now - track["last_voice"] >= self._alarm_interval:
                        self.voice.alarm_violation(vtype)
                        track["last_voice"] = now

                elif track["stage"] == "remind":
                    # 温和提醒阶段：间隔 voice_cooldown 重复提醒
                    if now - track["last_voice"] >= self._voice_cooldown:
                        self.voice.remind_violation(vtype)
                        track["last_voice"] = now
                        self._reminder_count += 1

        # 更新合规率统计
        with self._posture_lock:
            violation_count = len(self._posture_events)
        if self._session_seconds > 0:
            violation_secs = min(violation_count * 2, self._session_seconds)
            rate = int((1 - violation_secs / self._session_seconds) * 100)
            self.stat_posture.set_value(f"{rate}%")
        else:
            self.stat_posture.set_value("100%")

        # 更新底部实时统计标签
        self.status_panel.update_stats(
            violations=len(self._posture_events),
            reminds=self._reminder_count,
            corrected=self._correction_count,
        )

        # 实时相似度显示
        if hasattr(self, 'detector') and self.detector is not None:
            avg_sim = self.detector.get_average_similarity()
            self.status_panel.set_compliance_info(f"相似度 {avg_sim*100:.0f}%")

    def _simulate_posture_update(self):
        """（已废弃：真实检测已接入 _update_posture_status）"""

    def _on_reward(self):
        """打开奖励兑换"""
        monthly = self.db.get_monthly_score()
        dialog = RewardDialog(self.db, monthly or {}, self)
        dialog.exec()

    def _on_config(self):
        """打开家长设置"""
        dialog = ParentConfigDialog(self.db, self)
        if dialog.exec() == 1:
            self._refresh_history()

    def _on_history(self):
        """查看历史 — 刷新并滚动到顶部"""
        self._refresh_history()
        # 滚动历史列表到顶部
        self.history_panel.list_widget.verticalScrollBar().setValue(0)
        self.history_panel.list_widget.setFocus()

        # 闪烁高亮边框
        original = self.history_panel.styleSheet()
        highlight = f"""
            QWidget {{
                background-color: {BG_ELEVATED};
                border: 2px solid {PRIMARY};
                border-radius: {RADIUS['lg']};
            }}
        """
        self.history_panel.setStyleSheet(highlight)
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(600, lambda: self.history_panel.setStyleSheet(original))

    # ── 视频剪辑后台线程 ──

    def _merge_av_to_mp4(self, video_path: Path, audio_path: Path, output_path: Path) -> bool:
        """使用 imageio-ffmpeg 合并视频和音频为 MP4"""
        try:
            # 优先使用系统路径下的 ffmpeg，否则使用 imageio-ffmpeg 内置
            import imageio_ffmpeg
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
            cmd = [
                ffmpeg_exe, "-y",
                "-i", str(video_path),
                "-i", str(audio_path),
                "-c:v", "libx264", "-c:a", "aac",
                "-map", "0:v:0", "-map", "1:a:0",
                "-shortest",
                str(output_path),
            ]
            subprocess.run(cmd, capture_output=True, timeout=120, check=True)
            logger.info(f"音视频合并成功: {output_path}")
            return True
        except FileNotFoundError:
            logger.warning("ffmpeg 未找到，跳过音视频合并，保留纯视频 AVI")
            return False
        except subprocess.TimeoutExpired:
            logger.error("音视频合并超时 (120s)")
            return False
        except Exception as e:
            logger.error(f"音视频合并失败: {e}")
            return False

    def _start_clip_worker(self, raw_path: Path, output_path: Path, posture_events: list, score_report: dict, best_frame_path: str = None, mobile_video_path: str = ""):
        """启动后台视频剪辑线程"""
        self._clip_thread = QThread()
        self._clip_worker = ClipWorker(
            self.clipper, raw_path, output_path, posture_events, score_report, best_frame_path, mobile_video_path
        )
        self._clip_worker.moveToThread(self._clip_thread)
        self._clip_worker.progress.connect(self._on_clip_progress)
        self._clip_worker.finished.connect(lambda path: self._on_clip_finished(path, score_report))
        self._clip_worker.error.connect(self._on_clip_error)
        self._clip_thread.started.connect(self._clip_worker.run)
        self._clip_thread.start()

    def _on_clip_progress(self, percent: int, message: str):
        """剪辑进度更新"""
        self.status_panel.update_clip_progress(percent)
        self.status_panel.set_message(f"正在生成回顾视频... {percent}% {message}")
        logger.info(f"剪辑进度: {percent}% - {message}")

    def _on_clip_finished(self, video_path: str, score_report):
        """剪辑完成回调（score_report 为 ScoreReport dataclass 或 dict）"""
        # 统一取值方法
        def _get(key, default=0):
            if hasattr(score_report, key):
                return getattr(score_report, key)
            if isinstance(score_report, dict):
                return score_report.get(key, default)
            return default

        # 清理线程
        if hasattr(self, "_clip_thread") and self._clip_thread:
            self._clip_thread.quit()
            self._clip_thread.wait()
            self._clip_thread = None
            self._clip_worker = None

        # 更新数据库
        raw_path_str = getattr(self, "_last_raw_path", "")
        if video_path and Path(video_path).exists():
            self.db.end_session(
                session_id=self._session_id,
                duration_minutes=_get("duration_minutes", 0),
                posture_rate=_get("posture_rate", 0),
                focus_rate=_get("focus_rate", 0),
                efficiency_score=_get("efficiency_score", 0),
                correction_rate=_get("correction_rate", 0),
                total_score=_get("total_score", 0),
                grade=_get("grade", "B"),
                video_path=video_path,
                raw_video_path=raw_path_str,
                status="completed",
            )
            self.status_panel.set_clip_result("剪辑完成")
            self.status_panel.set_message("回顾视频已生成！")
            self.btn_review.setEnabled(True)
            logger.info(f"回顾视频生成完成: {video_path}")

            # 清理原始文件：只保留剪辑好的回顾视频
            self._cleanup_raw_files(raw_path_str, video_path)
        else:
            # 剪辑失败或跳过，标记为完成但无回顾视频
            self.db.end_session(
                session_id=self._session_id,
                duration_minutes=_get("duration_minutes", 0),
                posture_rate=_get("posture_rate", 0),
                focus_rate=_get("focus_rate", 0),
                efficiency_score=_get("efficiency_score", 0),
                correction_rate=_get("correction_rate", 0),
                total_score=_get("total_score", 0),
                grade=_get("grade", "B"),
                raw_video_path=raw_path_str,
                status="completed",
            )
            self.status_panel.set_message("回顾视频生成失败，原始视频已保存。")
            self.btn_review.setEnabled(True)
            logger.warning("回顾视频生成失败")

        self._refresh_history()

    def _cleanup_raw_files(self, raw_path_str: str, review_path_str: str):
        """剪辑成功后清理原始录像、音频、过程文件，仅保留回顾视频"""
        review_path = Path(review_path_str)
        raw_path = Path(raw_path_str) if raw_path_str else None
        deleted = []
        skipped = []

        def _safe_remove(p: Path, label: str):
            if p.exists() and p.resolve() != review_path.resolve():
                try:
                    p.unlink()
                    deleted.append(label)
                    logger.info(f"已清理: {p}")
                except OSError as e:
                    skipped.append(f"{label} ({e})")
                    logger.warning(f"清理失败: {p} - {e}")

        if raw_path and raw_path.exists():
            raw_stem = raw_path.stem
            raw_dir = raw_path.parent

            # 原始录像
            _safe_remove(raw_path, "原始录像")

            # 合并过程的音频文件
            audio_wav = raw_dir / f"audio_{raw_stem.split('_')[0] if '_' in raw_stem else raw_stem}.wav"
            _safe_remove(audio_wav, "音频WAV")

            # 合并后的过程 MP4（原始 AVI 被 merge 替换后，合并版以 _audio.mp4 结尾）
            merged_mp4 = raw_dir / f"{raw_stem}_audio.mp4"
            _safe_remove(merged_mp4, "合并MP4")

            # 封面帧图片
            cover_png = raw_dir / f"cover_{raw_stem}.png"
            _safe_remove(cover_png, "封面PNG")

            # 同目录下同时间戳的其他临时文件 (audio_xxx.wav)
            raw_dir_path = raw_dir
            try:
                timestamp_prefix = raw_stem.split('_')[0] if '_' in raw_stem else ""
                if timestamp_prefix and len(timestamp_prefix) >= 14:  # YYYYMMDD_HHMMSS
                    for f in raw_dir_path.glob(f"audio_{timestamp_prefix}*.wav"):
                        _safe_remove(f, f"音频WAV({f.name})")
            except Exception:
                pass

        if deleted:
            logger.info(f"清理完成: 删除了 {len(deleted)} 个文件 ({', '.join(deleted)})")
        if skipped:
            logger.warning(f"清理部分失败: {', '.join(skipped)}")

    def _on_clip_error(self, error_msg: str):
        """剪辑错误回调"""
        logger.error(f"视频剪辑错误: {error_msg}")
        self.status_panel.set_message(f"回顾视频生成出错: {error_msg}")
        if hasattr(self, "_clip_thread") and self._clip_thread:
            self._clip_thread.quit()
            self._clip_thread.wait()
            self._clip_thread = None
            self._clip_worker = None

        # 更新数据库为完成状态（保留原始视频）
        report = getattr(self, "_last_score_report", None)
        if report is not None:
            self.db.end_session(
                session_id=self._session_id,
                duration_minutes=getattr(report, "duration_minutes", 0),
                posture_rate=getattr(report, "posture_rate", 0),
                focus_rate=getattr(report, "focus_rate", 0),
                efficiency_score=getattr(report, "efficiency_score", 0),
                correction_rate=getattr(report, "correction_rate", 0),
                total_score=getattr(report, "total_score", 0),
                grade=getattr(report, "grade", "B"),
                raw_video_path=getattr(self, "_last_raw_path", ""),
                status="completed",
            )
            self._refresh_history()
        self.btn_review.setEnabled(True)

    def _on_review(self):
        """查看回顾"""
        if self._session_id:
            session = self.db.get_session(self._session_id)
            video_path = session.get("video_path") if session else None
            if video_path and Path(video_path).exists():
                from PyQt6.QtCore import QUrl
                from PyQt6.QtGui import QDesktopServices
                QDesktopServices.openUrl(QUrl.fromLocalFile(video_path))
            else:
                QMessageBox.information(self, "回顾视频", "回顾视频尚未生成或不可用。")

    def _refresh_history(self):
        """刷新历史记录"""
        self.history_panel.refresh()

    # ── 手机端远程录制控制 ──

    def _get_server_base_url(self):
        """获取服务端基础URL，根据certs目录是否存在自动判断HTTP/HTTPS"""
        from pathlib import Path
        cert_path = Path(__file__).resolve().parent.parent.parent / "certs" / "cert.pem"
        if cert_path.exists():
            return "https://localhost:8910"
        return "http://localhost:8910"

    def _notify_mobile_start(self):
        """通知手机端开始录制（WebServer → WebSocket Broadcast）"""
        try:
            base_url = self._get_server_base_url()
            is_https = base_url.startswith("https")

            ctx = None
            if is_https:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE

            body = json.dumps({"session_id": self._session_id}).encode("utf-8")
            req = urllib.request.Request(
                f"{base_url}/api/mobile/start",
                data=body,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            resp = urllib.request.urlopen(req, timeout=5, context=ctx) if ctx else urllib.request.urlopen(req, timeout=5)
            result = json.loads(resp.read().decode())
            if result.get("success"):
                logger.info(f"手机端录制已通知启动: {result.get('path')}")
            else:
                logger.warning(f"手机端录制启动失败: {result.get('error')}")
        except Exception as e:
            logger.warning(f"通知手机端开始录制失败: {e}")

    def _notify_mobile_stop(self) -> str:
        """通知手机端停止录制，返回手机端视频路径（可能为空）"""
        mobile_path = ""
        try:
            base_url = self._get_server_base_url()
            is_https = base_url.startswith("https")

            ctx = None
            if is_https:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE

            req = urllib.request.Request(
                f"{base_url}/api/mobile/stop",
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            resp = urllib.request.urlopen(req, timeout=30, context=ctx) if ctx else urllib.request.urlopen(req, timeout=30)
            result = json.loads(resp.read().decode())
            if result.get("success"):
                mobile_path = result.get("path", "")
                logger.info(f"手机端录制已停止: {mobile_path}, 帧数={result.get('frame_count')}")
            else:
                logger.warning(f"手机端录制停止失败: {result.get('error')}")
        except Exception as e:
            logger.warning(f"通知手机端停止录制失败: {e}")

        # 如果路径存在但文件为空/无效，回退
        if mobile_path and Path(mobile_path).exists():
            file_size = Path(mobile_path).stat().st_size
            if file_size < 1024:  # 小于 1KB 视为无效
                logger.warning(f"手机端视频文件过小({file_size}B)，忽略")
                mobile_path = ""
        return mobile_path

    def _check_mobile_posture_alert(self):
        """轮询服务器获取手机端姿态提醒"""
        try:
            base_url = self._get_server_base_url()
            is_https = base_url.startswith("https")

            ctx = None
            if is_https:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE

            req = urllib.request.Request(
                f"{base_url}/api/mobile/posture_alert",
                method="GET",
            )
            resp = urllib.request.urlopen(req, timeout=3, context=ctx) if ctx else urllib.request.urlopen(req, timeout=3)
            result = json.loads(resp.read().decode())
            if result.get("type") == "posture_alert" and result.get("alert") == "head_too_low":
                self.voice.remind_violation("head_too_low")
                # 记录到违规事件
                with self._posture_lock:
                    self._posture_events.append({
                        "timestamp": time.time(),
                        "violation_type": "head_too_low_mobile",
                        "severity": "warning",
                        "angle": 0,
                        "reminded": 1,
                        "corrected": 0,
                        "corrected_time": 0,
                    })
                logger.info("收到手机端姿态提醒：头太低了")
        except Exception:
            pass

    def resizeEvent(self, event):
        """窗口尺寸变化时动态调整内部元素"""
        super().resizeEvent(event)
        w = self.width()
        badge_size = max(50, min(80, w // 16))
        if hasattr(self, 'score_badge'):
            self.score_badge.setFixedSize(badge_size, badge_size)
            self.score_badge._badge_size = badge_size

    def closeEvent(self, event):
        """关闭窗口"""
        if self._is_running:
            reply = QMessageBox.question(
                self, "确认退出",
                "作业正在进行中，确定要退出吗？\n当前进度将不会保存。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.camera.stop_recording()
            self.db.cancel_session(self._session_id)

        self.camera.release()
        self.focus_detector.release()
        self.voice.release()
        event.accept()
        logger.info("MainWindow closed")
