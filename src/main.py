"""
StudyBuddy - 智能作业陪伴与回顾系统
主入口程序（2.0：PyQt6 + FastAPI Web 服务器）
"""
import sys
import os
import warnings
import logging
import socket
from datetime import datetime
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

# 每次启动生成独立的日志文件
log_filename = f"app_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log"
log_filepath = PROJECT_ROOT / "logs" / log_filename

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(log_filepath, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),  # 同时输出到控制台，便于调试
    ],
)
# 彻底静默 FFmpeg 日志（qt.multimedia.ffmpeg 模块）
for ffmpeg_logger in ["FFmpeg", "avcodec", "avformat", "swscale", "qt.multimedia.ffmpeg"]:
    logging.getLogger(ffmpeg_logger).setLevel(logging.CRITICAL)
    logging.getLogger(ffmpeg_logger).propagate = False

logger = logging.getLogger("studybuddy")


def get_lan_ip() -> str:
    """获取本机局域网 IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    logger.info("StudyBuddy 2.0 启动中…")

    # ── 预加载本地 AI 模型（后台启动 llama-server） ──
    import threading as _th

    def _preload_ai():
        try:
            from core.ai_tutor_local import start_local_server, is_local_available
            if is_local_available():
                logger.info("正在预加载本地 AI 模型…")
                ok = start_local_server()
                logger.info(f"本地 AI 模型加载{'成功' if ok else '失败，将回退到 Ollama'}")
        except Exception as e:
            logger.warning(f"AI 预加载跳过: {e}")

    _th.Thread(target=_preload_ai, daemon=True, name="ai-preload").start()

    # ── 启动 Web 服务器 ──
    from web.server import WebServer

    schedule_path = str(PROJECT_ROOT / "docs" / "summer_homework_plan.html")
    if not Path(schedule_path).exists():
        # 回退：尝试外部目录
        alt = Path(r"G:\思思学习资料\summer_homework_plan.html")
        if alt.exists():
            schedule_path = str(alt)

    web_server = WebServer(
        host="0.0.0.0",
        port=8910,
        schedule_html_path=schedule_path,
    )
    web_server.start_async()
    lan_ip = get_lan_ip()
    logger.info(f"手机端访问: http://{lan_ip}:8910")

    # ── 初始化数据库：加载作业排程到 SQLite ──
    from core.database import Database
    from web.schedule_parser import parse_schedule
    from dataclasses import asdict

    db_path = PROJECT_ROOT / "data" / "studybuddy.db"
    db = Database(db_path)
    web_server.db = db  # 注入数据库引用

    if Path(schedule_path).exists():
        plans = parse_schedule(schedule_path)
        day_data = [
            {
                "day": p.day,
                "tasks": [{"subject": t.subject, "description": t.description, "duration_minutes": t.duration_minutes}
                          for t in p.tasks],
            }
            for p in plans
        ]
        try:
            db.init_homework_tasks(day_data)
            logger.info(f"作业任务已初始化：{len(plans)} 天，共 {sum(len(d.get('tasks', [])) for d in day_data)} 个任务")
        except Exception as e:
            logger.warning(f"作业初始化失败（可能已存在）: {e}")

    # ── 启动 PyQt6 主窗口 ──
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 9))

    from ui.main_window import MainWindow

    window = MainWindow(db_path=db_path)
    window.setWindowTitle(f"StudyBuddy 2.0 - 手机访问 http://{lan_ip}:8910")
    window.show()

    logger.info("StudyBuddy UI 已启动")
    exit_code = app.exec()

    # ── 清理 ──
    try:
        from core.ai_tutor_local import stop_local_server
        stop_local_server()
    except Exception:
        pass
    logger.info("StudyBuddy 已退出")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
