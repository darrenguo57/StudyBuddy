"""
FastAPI Web 服务器 - StudyBuddy 2.0
提供给手机端的内嵌服务：REST API + WebSocket 视频推流
"""
import asyncio
import json
import logging
import time
import threading
import queue
from pathlib import Path
from typing import Optional, Dict

import cv2
import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, File, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# 手机端帧共享缓冲区（供 camera_manager 实时合成画中画）
_mobile_frame_lock = threading.Lock()
_mobile_frame = None  # 最新的手机帧 (numpy array, BGR 格式)
_mobile_ws_connected = False  # 手机端 WebSocket 是否已连接

# AI 辅导请求模型
class TutorRequest(BaseModel):
    question: str
    subject: str = ""

class HomeworkToggleRequest(BaseModel):
    day_number: int
    task_index: int

# 手机端基准坐姿（待用户提供手机端基准照片后校准）
# 当前为临时值，后续根据手机端照片精确设定
MOBILE_BASELINE = {
    'head_y_ratio': 0.45,        # 鼻尖在画面中的y比例（临时值）
    'head_shoulder_gap': 0.15,   # 鼻子与肩膀的y距离比例（临时值）
    'tolerance': 0.30,
}

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

        # 手机端录制器（帧通过共享缓冲区传给 camera_manager 合成画中画，不再单独写文件）
        self._mobile_recording = False
        self._mobile_recording_path: Optional[Path] = None
        self._mobile_frame_width: int = 640
        self._mobile_frame_height: int = 480
        self._mobile_frame_count: int = 0
        self._mobile_lock = threading.Lock()
        self._first_frame_received = False
        self._mobile_ws_connected = False  # WebSocket 连接状态

        # WebSocket IP 去重映射（Fix 1）
        self._ws_ip_map: dict = {}

        # 视频帧接收回调（由 CameraManager 处理）
        self.on_mobile_frame = None

        # 手机端坐姿检测（头部高度分析）
        self._mp_pose = None  # 懒加载
        self._mobile_pose_frame_count = 0
        self._mobile_pose_skip = 10  # 每10帧检测一次
        self._last_mobile_alert_time = 0
        self._mobile_alert_cooldown = 8  # 秒
        self._mobile_posture_alert = None  # 供外部读取的提醒

        self._setup_routes()

    @property
    def is_mobile_connected(self) -> bool:
        """手机端 WebSocket 是否已连接（供 camera_manager 分辨率决策）"""
        return self._mobile_ws_connected

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

        @app.get("/certs/cert.pem")
        async def download_cert():
            """手机端下载SSL证书（用于信任自签名证书）"""
            cert_path = PROJECT_ROOT / "certs" / "cert.pem"
            if not cert_path.exists():
                raise HTTPException(status_code=404, detail="证书不存在")
            return FileResponse(str(cert_path), media_type="application/x-pem-file", filename="StudyBuddy.crt")

        @app.get("/api/health")
        async def health():
            return {"status": "ok", "timestamp": time.time()}

        @app.get("/api/ping")
        async def ping():
            return {"ok": True, "timestamp": time.time()}

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
            from core.ai_tutor_local import get_tutor_status
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

        # ── 手机端远程录制控制 ──

        @app.post("/api/mobile/start")
        async def start_mobile_recording(request: Request = None):
            """PC 端通知：开始手机端录制"""
            if self._mobile_recording:
                return JSONResponse({"success": False, "error": "手机端已在录制中"})

            # 尝试从请求体解析 session_id
            session_id = ""
            if request is not None:
                try:
                    body = await request.json()
                    session_id = body.get("session_id", "")
                except Exception:
                    pass

            timestamp = time.strftime("%Y%m%d_%H%M%S")
            recordings_dir = PROJECT_ROOT / "recordings"
            recordings_dir.mkdir(parents=True, exist_ok=True)

            if session_id:
                self._mobile_recording_path = recordings_dir / f"mobile_session_{session_id}_{timestamp}.mp4"
            else:
                self._mobile_recording_path = recordings_dir / f"mobile_{timestamp}.mp4"

            self._mobile_frame_count = 0
            self._first_frame_received = False
            self._mobile_recording = True

            # Fix 2: 只发给第一个移动端客户端（去重后仅1个），不再广播
            if self.mobile_ws_clients:
                first_ws = next(iter(self.mobile_ws_clients))
                try:
                    await first_ws.send_json({"type": "start_mobile_recording"})
                except Exception as e:
                    logger.error(f"发送录制启动消息到手机端失败: {e}")

            logger.info(f"手机端录制已启动（帧共享模式）: {self._mobile_recording_path}")
            return JSONResponse({
                "success": True,
                "path": str(self._mobile_recording_path),
            })

        @app.post("/api/mobile/stop")
        async def stop_mobile_recording():
            """PC 端通知：停止手机端录制"""
            if not self._mobile_recording:
                return JSONResponse({"success": False, "error": "手机端当前未在录制"})

            self._mobile_recording = False

            # 广播给所有手机端：停止录制
            await self.broadcast({"type": "stop_mobile_recording"})

            final_path = str(self._mobile_recording_path) if self._mobile_recording_path else ""
            frame_count = self._mobile_frame_count

            # 清空共享缓冲区
            global _mobile_frame
            with _mobile_frame_lock:
                _mobile_frame = None

            logger.info(f"手机端录制已停止: {final_path}, 共 {frame_count} 帧")
            return JSONResponse({
                "success": True,
                "path": final_path,
                "frame_count": frame_count,
            })

        @app.get("/api/mobile/status")
        async def get_mobile_status():
            """查询手机端录制状态"""
            return JSONResponse({
                "recording": self._mobile_recording,
                "path": str(self._mobile_recording_path) if self._mobile_recording_path else "",
                "frame_count": self._mobile_frame_count,
                "first_frame_received": self._first_frame_received,
            })

        @app.get("/api/mobile/path")
        async def get_mobile_video_path():
            """获取最近一次手机端录制的视频路径"""
            return JSONResponse({
                "path": str(self._mobile_recording_path) if self._mobile_recording_path else "",
            })

        @app.get("/api/mobile/posture_alert")
        async def get_mobile_posture_alert():
            """PC端轮询：获取并消费手机端姿态提醒"""
            alert = self._mobile_posture_alert
            self._mobile_posture_alert = None
            return JSONResponse(alert if alert else {"type": "none"})

        # ── WebSocket ──

        @app.websocket("/ws/mobile")
        async def mobile_websocket(ws: WebSocket):
            """手机端 WebSocket：视频推流 + 双向通信"""
            await ws.accept()

            # ── Fix 1: WebSocket 连接去重（基于来源 IP） ──
            client_ip = ws.client.host if (hasattr(ws, 'client') and ws.client) else "unknown"
            if client_ip != "unknown" and client_ip in self._ws_ip_map:
                old_ws = self._ws_ip_map[client_ip]
                logger.info(f"关闭旧连接(重复): IP={client_ip}")
                try:
                    await old_ws.close(code=1000, reason="duplicate connection")
                except Exception:
                    pass
                self.mobile_ws_clients.discard(old_ws)

            self.mobile_ws_clients.add(ws)
            self._mobile_ws_connected = True
            globals()['_mobile_ws_connected'] = True
            self._ws_ip_map[client_ip] = ws
            logger.info(f"新移动端连接: IP={client_ip} (当前 {len(self.mobile_ws_clients)} 个客户端)")

            # ── Fix 3: 录制状态同步 ──
            if self._mobile_recording:
                logger.info(f"录制状态同步：当前正在录制，通知新连接 IP={client_ip}")
                try:
                    await ws.send_json({"type": "start_mobile_recording"})
                except Exception as e:
                    logger.error(f"通知新连接录制状态失败: {e}")

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
                                # 手机端视频帧（base64 JPEG）
                                frame_data = msg.get("data", "")
                                if frame_data and self._mobile_recording:
                                    self._handle_mobile_frame_b64(frame_data)
                                elif frame_data and not self._mobile_recording:
                                    logger.debug("收到 camera_frame 但当前未处于录制状态，已忽略")

                        except json.JSONDecodeError:
                            pass

                    elif "bytes" in data:
                        # 二进制视频帧（JPEG 字节流）
                        if self._mobile_recording:
                            self._handle_mobile_frame_bytes(data["bytes"])
                        else:
                            logger.debug(f"收到 {len(data['bytes'])} bytes 二进制帧但当前未录制，已忽略")

            except WebSocketDisconnect:
                logger.info("手机端断开连接")
            except Exception as e:
                logger.error(f"WebSocket 异常: {e}")
            finally:
                self.mobile_ws_clients.discard(ws)
                # 清理 IP 映射（仅当映射仍指向当前连接时）
                if self._ws_ip_map.get(client_ip) is ws:
                    del self._ws_ip_map[client_ip]
                self._mobile_ws_connected = bool(self.mobile_ws_clients)
                globals()['_mobile_ws_connected'] = bool(self.mobile_ws_clients)
                logger.info(f"手机端已断开 (剩余 {len(self.mobile_ws_clients)} 个客户端)")

    def _ensure_schedule_loaded(self):
        """确保排程已加载"""
        if self._schedule_loaded:
            return
        if not self.schedule_html_path:
            logger.info("未配置排程文件路径，跳过排程加载")
            self._schedule_loaded = True
            return
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
            self._schedule_loaded = True

    def _ensure_certs(self):
        """确保 SSL 证书存在，不存在则自动生成"""
        import socket
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        import datetime

        cert_dir = PROJECT_ROOT / "certs"
        cert_path = cert_dir / "cert.pem"
        key_path = cert_dir / "key.pem"

        if cert_path.exists() and key_path.exists():
            return str(cert_path), str(key_path)

        logger.info("SSL证书缺失，正在自动生成...")
        cert_dir.mkdir(parents=True, exist_ok=True)

        # 获取本机局域网IP
        lan_ip = "192.168.1.1"
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            lan_ip = s.getsockname()[0]
            s.close()
        except Exception:
            pass

        hostname = socket.gethostname()

        # 生成密钥
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        # 构建证书
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, f"StudyBuddy-{hostname}"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "StudyBuddy"),
        ])

        san = x509.SubjectAlternativeName([
            x509.DNSName("localhost"),
            x509.IPAddress(x509.IPv4Address(lan_ip)),
        ])

        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.utcnow())
            .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=3650))
            .add_extension(san, critical=False)
            .sign(key, hashes.SHA256())
        )

        with open(key_path, "wb") as f:
            f.write(key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            ))

        with open(cert_path, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))

        logger.info(f"SSL证书已生成: {cert_path} (IP: {lan_ip}, 有效期10年)")
        return str(cert_path), str(key_path)

    def start_async(self):
        """在后台线程启动 uvicorn 服务器"""
        import uvicorn

        def run_server():
            cert_path, key_path = self._ensure_certs()

            uvicorn.run(
                self.app,
                host=self.host,
                port=self.port,
                log_level="warning",
                access_log=False,
                ssl_certfile=cert_path,
                ssl_keyfile=key_path,
            )

        thread = threading.Thread(target=run_server, daemon=True, name="web-server")
        thread.start()

        protocol = "https" if (PROJECT_ROOT / "certs" / "cert.pem").exists() else "http"
        logger.info(f"Web 服务器已启动: {protocol}://{self.host}:{self.port}")

        if protocol == "https":
            import socket as _sock
            lan_ip = "192.168.1.1"
            try:
                s = _sock.socket(_sock.AF_INET, _sock.SOCK_DGRAM)
                s.connect(("8.8.8.8", 80))
                lan_ip = s.getsockname()[0]
                s.close()
            except Exception:
                pass
            logger.info(f"手机端访问: https://{lan_ip}:{self.port}")
            logger.info(f"如手机提示不安全，请在手机浏览器打开 https://{lan_ip}:{self.port}/certs/cert.pem 下载证书并安装")

    async def broadcast(self, message: dict):
        """向所有手机端推送消息"""
        dead = set()
        for ws in self.mobile_ws_clients:
            try:
                await ws.send_json(message)
            except Exception:
                dead.add(ws)
        self.mobile_ws_clients -= dead

    def get_mobile_posture_alert(self) -> Optional[Dict]:
        """获取并消费手机端姿态提醒"""
        alert = self._mobile_posture_alert
        self._mobile_posture_alert = None
        return alert

    # ── 手机端视频帧录制（后台线程） ──

    def _handle_mobile_frame_b64(self, b64_data: str):
        """处理 base64 JPEG 帧 → 入队"""
        try:
            import base64
            # 移除可能的 data:image/jpeg;base64, 前缀
            if "," in b64_data:
                b64_data = b64_data.split(",", 1)[1]
            raw = base64.b64decode(b64_data)
            self._enqueue_mobile_frame(raw)
        except Exception as e:
            logger.debug(f"base64 帧解码失败: {e}")

    def _handle_mobile_frame_bytes(self, raw_bytes: bytes):
        """处理二进制 JPEG 帧 → 入队"""
        self._enqueue_mobile_frame(raw_bytes)

    def _enqueue_mobile_frame(self, jpeg_bytes: bytes):
        """将 JPEG 字节解码后写入共享缓冲区（供 camera_manager 实时合成画中画），并对采样帧做头部高度分析"""
        global _mobile_frame
        with self._mobile_lock:
            if not self._mobile_recording:
                return

            # 首帧到达日志（诊断手机端 0 帧问题）
            if not self._first_frame_received:
                self._first_frame_received = True
                logger.info(f"手机端首帧已接收: {len(jpeg_bytes)} bytes")
                self._mobile_frame_count = 1
            else:
                self._mobile_frame_count += 1

        # 解码并写入共享缓冲区
        try:
            frame = cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)
            if frame is not None:
                with _mobile_frame_lock:
                    _mobile_frame = frame
        except Exception as e:
            logger.debug(f"手机端帧解码失败: {e}")

        # 采样分析（每 N 帧检测一次，仅录制时）
        self._mobile_pose_frame_count += 1
        if self._mobile_pose_frame_count % self._mobile_pose_skip == 0:
            try:
                self._analyze_mobile_head_height(jpeg_bytes)
            except Exception as e:
                logger.debug(f"手机端头部高度分析异常: {e}")

    def _init_mp_pose(self):
        """懒加载 MediaPipe Pose（Lite 模型，不阻塞）"""
        if self._mp_pose is None:
            # 限制重试次数，防止无法下载模型时无限刷屏（如被墙）
            self._mp_pose_retry_count = getattr(self, '_mp_pose_retry_count', 0)
            if self._mp_pose_retry_count >= 3:
                logger.warning("手机端 MediaPipe Pose 初始化已达最大重试次数(3)，已禁用")
                return
            self._mp_pose_retry_count += 1

            try:
                import mediapipe as mp
                self._mp_pose = mp.solutions.pose.Pose(
                    static_image_mode=True,
                    model_complexity=0,  # Lite 模型加速
                    min_detection_confidence=0.5,
                )
                logger.info("手机端 MediaPipe Pose 已初始化（Lite 模型）")
                self._mp_pose_retry_count = 0  # 成功后重置
            except Exception as e:
                logger.warning(
                    f"手机端 MediaPipe Pose 初始化失败(第{self._mp_pose_retry_count}/3次): {e}"
                )
                import time
                time.sleep(10)  # 避免短时间内反复尝试

    def _analyze_mobile_head_height(self, jpeg_bytes: bytes):
        """分析手机端帧中头部高度，检测是否太低"""
        import time as _time
        now = _time.time()

        # 冷却检查
        if now - self._last_mobile_alert_time < self._mobile_alert_cooldown:
            return

        # 懒加载 Pose
        self._init_mp_pose()
        if self._mp_pose is None:
            return

        # 解码 JPEG
        frame = cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return

        h, w = frame.shape[:2]

        # 下采样到 320px 宽加速
        if w > 320:
            scale = 320.0 / w
            frame = cv2.resize(frame, (320, int(h * scale)))
            h, w = frame.shape[:2]

        # MediaPipe Pose 检测
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self._mp_pose.process(rgb)

        if not results.pose_landmarks:
            return

        landmarks = results.pose_landmarks.landmark

        # 关键点：0=鼻子, 11=左肩, 12=右肩
        nose = landmarks[0]
        left_shoulder = landmarks[11]
        right_shoulder = landmarks[12]

        if nose.visibility < 0.5:
            return

        nose_y = nose.y
        shoulder_mid_y = (left_shoulder.y + right_shoulder.y) / 2

        # 头部相对画面位置
        head_ratio = nose_y / h

        # 判定1：头在画面下半部分（太低）
        head_too_low = head_ratio > 0.55

        # 判定2：鼻子与肩膀距离太近（趴桌特征）
        nose_shoulder_diff = nose_y - shoulder_mid_y
        diff_ratio = nose_shoulder_diff / h
        head_near_shoulder = diff_ratio > -0.02  # 几乎同一水平线

        if head_too_low or head_near_shoulder:
            self._last_mobile_alert_time = now
            self._mobile_posture_alert = {
                "type": "posture_alert",
                "alert": "head_too_low",
                "message": "抬起来一些",
            }
            logger.info(
                f"手机端检测到头太低: head_ratio={head_ratio:.3f}, "
                f"diff_ratio={diff_ratio:.3f}"
            )


