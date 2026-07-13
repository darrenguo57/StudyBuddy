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


def _ensure_audio_files():
    """确保音频文件存在，不存在则自动生成"""
    audio_dir = Path(__file__).resolve().parent.parent / "assets" / "audio"

    # 检查关键文件
    required = ["intro.wav", "start.wav", "camera_lost.wav"]
    missing = [f for f in required if not (audio_dir / f).exists()]

    if missing:
        logger.info(f"音频文件缺失 {len(missing)} 个，正在自动生成...")
        try:
            import subprocess
            script = Path(__file__).resolve().parent.parent / "scripts" / "generate_audio.py"
            result = subprocess.run(
                [sys.executable, str(script)],
                capture_output=True, text=True, timeout=120,
                cwd=str(audio_dir.parent.parent)
            )
            if result.returncode != 0:
                logger.warning(f"音频生成脚本返回非零: {result.stderr[:500]}")
            else:
                logger.info("音频文件自动生成完成")
        except Exception as e:
            logger.warning(f"音频自动生成失败: {e}")


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

    # ── 确保音频文件存在 ──
    _ensure_audio_files()

    # ── 启动 Web 服务器 ──
    from web.server import WebServer

    # 排程文件路径：多路径搜索
    schedule_candidates = [
        PROJECT_ROOT / "data" / "summer_homework_plan.html",                # 优先项目内 data/
        PROJECT_ROOT / "docs" / "summer_homework_plan.html",                # 项目内 docs/
        PROJECT_ROOT.parent / "思思学习资料" / "summer_homework_plan.html",  # 原路径
        Path(r"G:\思思学习资料\summer_homework_plan.html"),                 # G盘硬编码
    ]
    schedule_path = ""
    for candidate in schedule_candidates:
        if candidate.exists():
            schedule_path = str(candidate)
            logger.info(f"找到排程文件: {schedule_path}")
            break

    if not schedule_path:
        logger.warning("未在任何位置找到排程文件，将跳过排程加载")

    web_server = WebServer(
        host="0.0.0.0",
        port=8910,
        schedule_html_path=schedule_path,
    )
    web_server.start_async()
    lan_ip = get_lan_ip()
    protocol = "https" if (PROJECT_ROOT / "certs" / "cert.pem").exists() else "http"
    logger.info(f"手机端访问: {protocol}://{lan_ip}:8910")

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
