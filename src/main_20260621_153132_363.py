#!/usr/bin/env python3
"""
StudyBuddy — 儿童作业智能监督系统
主程序入口
"""
import sys
import os
import json
import logging
from pathlib import Path
from datetime import datetime

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

# 配置日志
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
               logging.FileHandler(LOG_DIR / f"studybuddy_{datetime.now():%Y%m%d}.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("studybuddy")


def load_config() -> dict:
    """加载配置文件"""
    config_path = PROJECT_ROOT / "config" / "settings.json"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"加载配置失败: {e}，使用默认配置")
        return {}


def init_dirs(config: dict) -> dict:
    """初始化目录结构并返回路径"""
    paths = {
        "recordings": PROJECT_ROOT / config.get("recording_dir", "recordings"),
        "reviews": PROJECT_ROOT / config.get("review_dir", "reviews"),
        "snapshots": PROJECT_ROOT / config.get("snapshot_dir", "snapshots"),
        "data": PROJECT_ROOT / config.get("data_dir", "data"),
    }
    for name, p in paths.items():
        p.mkdir(parents=True, exist_ok=True)
    return paths


def main():
    """主函数"""
    print("=" * 60)
    print("  StudyBuddy — 儿童作业智能监督系统 v1.0")
    print("=" * 60)

    config = load_config()
    paths = init_dirs(config)

    # 初始化数据库
    from core.database import Database
    db = Database(paths["data"] / "studybuddy.db")
    logger.info("数据库就绪")

    # 初始化摄像头
    from core.camera_manager import CameraManager, CameraConfig
    cam_config = CameraConfig(
        index=config.get("camera_index", 0),
        resolution=tuple(config.get("resolution", [1280, 720])),
        record_fps=config.get("record_fps", 15.0),
    )
    camera = CameraManager(cam_config, paths["recordings"])

    if not camera.connect():
        logger.error("无法连接摄像头，请检查设备")
        print("[错误] 无法连接摄像头，请检查设备后重新启动")
        sys.exit(1)

    logger.info("摄像头就绪")

    # 初始化坐姿检测器
    from core.posture_detector import PostureDetector
    posture_detector = PostureDetector(config.get("posture", {}))
    logger.info("坐姿检测器就绪")

    # 初始化语音引擎
    from core.voice_interaction import VoiceInteraction
    voice = VoiceInteraction(config.get("voice", {}))
    logger.info("语音引擎就绪")

    # 初始化评分引擎
    from core.scoring_engine import ScoringEngine
    scoring = ScoringEngine(config.get("scoring", {}))
    logger.info("评分引擎就绪")

    # 初始化视频剪辑器
    from core.video_clipper import VideoClipper, ClipConfig
    clip_config = ClipConfig(
        effective_speed=config.get("clip", {}).get("effective_speed", 4.0),
        violation_speed=config.get("clip", {}).get("violation_speed", 2.0),
    )
    clipper = VideoClipper(clip_config)
    logger.info("视频剪辑器就绪")

    # 启动 GUI
    try:
        from PyQt6.QtWidgets import QApplication
        from ui.main_window import MainWindow

        app = QApplication(sys.argv)
        app.setApplicationName("StudyBuddy")

        window = MainWindow(
            camera=camera,
            posture_detector=posture_detector,
            voice=voice,
            database=db,
            scoring=scoring,
            clipper=clipper,
            config=config,
            paths=paths,
        )

        # 初始引导
        voice.intro()
        window.show()

        # 清理
        exit_code = app.exec()

        camera.release()
        posture_detector.release()
        voice.release()

        sys.exit(exit_code)

    except ImportError as e:
        logger.error(f"PyQt6 导入失败: {e}")
        print("[错误] PyQt6 未安装，请运行: pip install PyQt6")
        print("当前已就绪模块：摄像头、坐姿检测、语音、评分、剪辑")
        print("GUI 模块将在安装 PyQt6 后可用")
        sys.exit(1)
    except Exception as e:
        logger.exception(f"程序异常退出: {e}")
        camera.release()
        posture_detector.release()
        voice.release()
        sys.exit(1)


if __name__ == "__main__":
    main()
