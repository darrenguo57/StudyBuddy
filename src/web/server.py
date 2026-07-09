"""
FastAPI Web 服务器 - StudyBuddy 2.0
提供给手机端的内嵌服务：REST API + WebSocket 视频推流
"""
import asyncio
import json
import logging
import time
import threading
from pathlib import Path
from typing import Optional, Dict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# AI 辅导请求模型
class TutorRequest(BaseModel):
    question: str
    subject: str = ""

class HomeworkToggleRequest(BaseModel):
    day_number: int
    task_index: int

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 静态文件目录
STATIC_DIR = Path(__file__).resolve().parent / "static"


class WebServer:
    """内嵌 Web 服务器管理器"""

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8910,
        schedule_html_path: str = "",
    ):
        self.host = host
        self.port = port
        self.schedule_html_path = schedule_html_path
        self.app = FastAPI(title="StudyBuddy API", version="2.0.0")

        # 数据库引用（由 main.py 注入）
        self.db = None

        # 运行时数据
        self.current_session: Optional[Dict] = None
        self.schedule_data: list = []
        self.mobile_ws_clients: set = set()
        self._schedule_loaded = False

        # 视频帧接收回调（由 CameraManager 处理）
        self.on_mobile_frame = None
        self._setup_routes()

    def _setup_routes(self):
        """配置路由和中间件"""
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # 静态文件
        if STATIC_DIR.exists():
            self.app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

        app = self.app

        @app.get("/")
        async def index():
            """手机端主页面"""
            index_path = STATIC_DIR / "index.html"
            if index_path.exists():
                return HTMLResponse(index_path.read_text(encoding="utf-8"))
            return HTMLResponse("<h1>StudyBuddy Mobile</h1><p>页面加载中...</p>")

        @app.get("/api/health")
        async def health():
            return {"status": "ok", "timestamp": time.time()}

        @app.get("/api/schedule")
        async def get_schedule():
            """获取暑假作业排程"""
            self._ensure_schedule_loaded()
            return JSONResponse({
                "total_days": len(self.schedule_data),
                "total_tasks": sum(len(d.get("tasks", [])) for d in self.schedule_data),
                "plans": self.schedule_data,
            })

        @app.get("/api/schedule/today")
        async def get_today_schedule(day: int = 0):
            """获取指定天数的作业（day=0 取今天）"""
            self._ensure_schedule_loaded()
            # 简单实现：如果 day=0，根据日期计算
            if day < 1 or day > len(self.schedule_data):
                day = 1
            plan = self.schedule_data[day - 1] if day <= len(self.schedule_data) else {}
            return JSONResponse(plan)

        @app.get("/api/session/status")
        async def get_session_status():
            """获取当前会话状态"""
            return JSONResponse(self.current_session or {"status": "idle"})

        # ── AI 辅导 ──

        @app.post("/api/tutor")
        async def ai_tutor(req: TutorRequest):
            """AI 辅导：解答题目（本地 llama.cpp + Ollama 回退）"""
            from ..core.ai_tutor_local import tutor, get_tutor_status

            status = get_tutor_status()
            if not status["local_model_available"] and not status.get("ollama_available", False):
                return JSONResponse({
                    "success": False,
                    "error": "AI 服务未就绪，请检查模型文件或 Ollama 服务",
                    "answer": "",
                })

            answer = tutor(req.question, req.subject)
            if answer:
                return JSONResponse({"success": True, "answer": answer})
            return JSONResponse({
                "success": False,
                "error": "模型响应异常，请稍后重试",
                "answer": "",
            })

        @app.get("/api/tutor/status")
        async def tutor_status():
            """检查 AI 辅导服务状态"""
            from ..core.ai_tutor_local import get_tutor_status
            status = get_tutor_status()
            return JSONResponse({
                "available": (
                    status["local_model_ready"]
                    or status.get("ollama_available", False)
                ),
                "local_model_ready": status["local_model_ready"],
                "ollama_available": status.get("ollama_available", False),
            })

        # ── 作业进度 ──

        @app.post("/api/homework/toggle")
        async def toggle_homework(req: HomeworkToggleRequest):
            """切换任务完成状态"""
            if not self.db:
                return JSONResponse({"success": False, "error": "数据库未就绪"})
            new_state = self.db.toggle_homework_task(req.day_number, req.task_index)
            return JSONResponse({"success": True, "is_done": new_state})

        @app.get("/api/homework/day/{day_number}")
        async def get_homework_day(day_number: int):
            """获取某天作业详情及进度"""
            if not self.db:
                return JSONResponse({"success": False, "error": "数据库未就绪"})
            data = self.db.get_homework_day(day_number)
            if not data:
                return JSONResponse({"success": False, "error": "未找到该天数据"})
            return JSONResponse({"success": True, "data": data})

        @app.get("/api/homework/summary")
        async def get_homework_summary():
            """获取整体作业进度摘要"""
            if not self.db:
                return JSONResponse({"success": False, "error": "数据库未就绪"})
            data = self.db.get_homework_summary()
            return JSONResponse({"success": True, "data": data})

        # ── WebSocket ──

        @app.websocket("/ws/mobile")
        async def mobile_websocket(ws: WebSocket):
            """手机端 WebSocket：视频推流 + 双向通信"""
            await ws.accept()
            self.mobile_ws_clients.add(ws)
            logger.info(f"手机端已连接 (当前 {len(self.mobile_ws_clients)} 个客户端)")

            try:
                while True:
                    data = await ws.receive()
                    # 支持文本（JSON 消息）和字节（视频帧）
                    if "text" in data:
                        try:
                            msg = json.loads(data["text"])
                            msg_type = msg.get("type", "")

                            if msg_type == "ping":
                                await ws.send_json({"type": "pong"})

                            elif msg_type == "start_recording":
                                logger.info("手机端请求开始录制")
                                await ws.send_json({"type": "recording_started"})

                            elif msg_type == "stop_recording":
                                logger.info("手机端请求停止录制")
                                await ws.send_json({"type": "recording_stopped"})

                            elif msg_type == "task_update":
                                # 任务状态更新
                                logger.info(f"任务更新: {msg}")

                            elif msg_type == "camera_frame":
                                # 视频帧数据（base64）
                                if self.on_mobile_frame:
                                    frame_data = msg.get("data", "")
                                    self.on_mobile_frame(frame_data)

                        except json.JSONDecodeError:
                            pass

                    elif "bytes" in data:
                        # 二进制视频帧
                        if self.on_mobile_frame:
                            self.on_mobile_frame(data["bytes"])

            except WebSocketDisconnect:
                logger.info("手机端断开连接")
            except Exception as e:
                logger.error(f"WebSocket 异常: {e}")
            finally:
                self.mobile_ws_clients.discard(ws)

    def _ensure_schedule_loaded(self):
        """确保排程已加载"""
        if self._schedule_loaded:
            return
        if self.schedule_html_path:
            try:
                from .schedule_parser import parse_schedule
                import json as _json
                from dataclasses import asdict

                plans = parse_schedule(self.schedule_html_path)
                self.schedule_data = [
                    {
                        "day": p.day,
                        "weekday": p.weekday,
                        "phase": p.phase,
                        "day_type": p.day_type,
                        "tasks": [asdict(t) for t in p.tasks],
                    }
                    for p in plans
                ]
                self._schedule_loaded = True
                logger.info(f"排程已加载: {len(self.schedule_data)} 天")
            except Exception as e:
                logger.error(f"加载排程失败: {e}")

    def start_async(self):
        """在后台线程启动 uvicorn 服务器"""
        import uvicorn

        def run_server():
            uvicorn.run(
                self.app,
                host=self.host,
                port=self.port,
                log_level="warning",
                access_log=False,
            )

        thread = threading.Thread(target=run_server, daemon=True, name="web-server")
        thread.start()
        logger.info(f"Web 服务器已启动: http://{self.host}:{self.port}")

    async def broadcast(self, message: dict):
        """向所有手机端推送消息"""
        dead = set()
        for ws in self.mobile_ws_clients:
            try:
                await ws.send_json(message)
            except Exception:
                dead.add(ws)
        self.mobile_ws_clients -= dead
