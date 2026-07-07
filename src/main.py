"""
StudyBuddy - 智能作业陪伴与回顾系统
主入口程序
"""
import sys
import os
import warnings
import logging
from pathlib import Path

# 抑制第三方库的警告弹窗（protobuf、mediapipe 等）
warnings.filterwarnings("ignore")
os.environ["QT_LOGGING_RULES"] = "*.debug=false;qt.*.debug=false;qt.multimedia.*=false"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 创建日志目录
(PROJECT_ROOT / "logs").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(PROJECT_ROOT / "logs" / "app.log", encoding="utf-8"),
    ],
)
# 彻底静默 FFmpeg 日志（qt.multimedia.ffmpeg 模块）
for ffmpeg_logger in ["FFmpeg", "avcodec", "avformat", "swscale", "qt.multimedia.ffmpeg"]:
    logging.getLogger(ffmpeg_logger).setLevel(logging.CRITICAL)
    logging.getLogger(ffmpeg_logger).propagate = False

logger = logging.getLogger("studybuddy")


def main():
    logger.info("StudyBuddy 启动中…")

    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 9))

    from ui.main_window import MainWindow

    db_path = PROJECT_ROOT / "data" / "studybuddy.db"
    window = MainWindow(db_path=db_path)
    window.show()

    logger.info("StudyBuddy UI 已启动")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
