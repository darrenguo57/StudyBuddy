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
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, UploadFile, File, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, HTMLResponse, FileResponse
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

        # 手机端录制器
        self._mobile_recording = False
        self._mobile_recording_path: Optional[Path] = None
        self._mobile_audio_path: Optional[Path] = None
        self._mobile_frame_queue: Optional[queue.Queue] = None
        self._mobile_writer_thread: Optional[threading.Thread] = None
        self._mobile_writer_stop: Optional[threading.Event] = None
        self._mobile_frame_width: int = 640
        self._mobile_frame_height: int = 480
        self._mobile_frame_count: int = 0
        self._mobile_lock = threading.Lock()
        self._first_frame_received = False

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
                # 同时记录音频文件路径
                self._mobile_audio_path = recordings_dir / f"mobile_session_{session_id}_{timestamp}.wav"
            else:
                self._mobile_recording_path = recordings_dir / f"mobile_{timestamp}.mp4"
                self._mobile_audio_path = recordings_dir / f"mobile_{timestamp}.wav"

            # 初始化录制队列和写线程
            self._mobile_frame_queue = queue.Queue(maxsize=120)
            self._mobile_writer_stop = threading.Event()
            self._mobile_frame_count = 0
            self._first_frame_received = False
            self._mobile_recording = True

            self._mobile_writer_thread = threading.Thread(
                target=self._mobile_writer_loop,
                daemon=True,
                name="mobile-writer",
            )
            self._mobile_writer_thread.start()

            # 广播给所有手机端：开始录制
            await self.broadcast({"type": "start_mobile_recording"})

            logger.info(f"手机端录制已启动: {self._mobile_recording_path}")
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

            # 通知写线程停止
            if self._mobile_writer_stop:
                self._mobile_writer_stop.set()

            # 等待写线程完成
            if self._mobile_writer_thread and self._mobile_writer_thread.is_alive():
                self._mobile_writer_thread.join(timeout=15.0)

            # 广播给所有手机端：停止录制
            await self.broadcast({"type": "stop_mobile_recording"})

            # 短暂等待音频上传（手机端收到 stop 广播后会异步上传）
            await asyncio.sleep(2.0)

            # 如果有音频文件且存在，合并到视频中
            final_path = str(self._mobile_recording_path) if self._mobile_recording_path else ""
            audio_path = str(self._mobile_audio_path) if self._mobile_audio_path else ""

            if final_path and audio_path and Path(audio_path).exists() and Path(final_path).exists():
                merged = self._merge_mobile_audio_to_video(final_path, audio_path)
                if merged:
                    final_path = merged

            frame_count = self._mobile_frame_count
            self._mobile_writer_thread = None
            self._mobile_writer_stop = None
            self._mobile_frame_queue = None

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

        @app.post("/api/mobile/audio")
        async def upload_mobile_audio(audio: UploadFile = File(...)):
            """手机端上传录制的音频文件（WebM/Opus → WAV）"""
            if not self._mobile_audio_path:
                return JSONResponse({"success": False, "error": "无对应的音频存储路径"})

            try:
                # 保存上传的 WebM 音频
                webm_path = self._mobile_audio_path.with_suffix(".webm")
                content = await audio.read()
                with open(webm_path, "wb") as f:
                    f.write(content)
                logger.info(f"手机端音频已保存: {webm_path} ({len(content)} bytes)")

                # 使用 ffmpeg 转码为 WAV
                self._convert_webm_to_wav(str(webm_path), str(self._mobile_audio_path))
                logger.info(f"手机端音频已转码为 WAV: {self._mobile_audio_path}")

                # 清理 webm
                try:
                    webm_path.unlink()
                except Exception:
                    pass

                return JSONResponse({"success": True, "path": str(self._mobile_audio_path)})
            except Exception as e:
                logger.error(f"手机端音频处理失败: {e}")
                return JSONResponse({"success": False, "error": str(e)})

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
                                # 手机端视频帧（base64 JPEG）
                                frame_data = msg.get("data", "")
                                if frame_data and self._mobile_recording:
                                    self._handle_mobile_frame_b64(frame_data)

                        except json.JSONDecodeError:
                            pass

                    elif "bytes" in data:
                        # 二进制视频帧（JPEG 字节流）
                        if self._mobile_recording:
                            self._handle_mobile_frame_bytes(data["bytes"])

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
        """将 JPEG 字节推入录制队列，并对采样帧做头部高度分析"""
        with self._mobile_lock:
            if not self._mobile_recording or self._mobile_frame_queue is None:
                return

            # 非阻塞入队，队列满则丢弃最旧帧
            if self._mobile_frame_queue.full():
                try:
                    self._mobile_frame_queue.get_nowait()
                except queue.Empty:
                    pass
            try:
                self._mobile_frame_queue.put_nowait(jpeg_bytes)
            except queue.Full:
                pass

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
            try:
                import mediapipe as mp
                self._mp_pose = mp.solutions.pose.Pose(
                    static_image_mode=True,
                    model_complexity=0,  # Lite 模型加速
                    min_detection_confidence=0.5,
                )
                logger.info("手机端 MediaPipe Pose 已初始化（Lite 模型）")
            except Exception as e:
                logger.warning(f"手机端 MediaPipe Pose 初始化失败: {e}")

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

    def _mobile_writer_loop(self):
        """后台写线程：从队列取 JPEG 帧 → 解码 → 写入 MP4"""
        writer = None
        path = None
        try:
            # 等待第一帧以确定分辨率
            import time as _time
            first_jpeg = None
            deadline = _time.time() + 15.0
            while first_jpeg is None and _time.time() < deadline:
                try:
                    first_jpeg = self._mobile_frame_queue.get(timeout=1.0)
                except queue.Empty:
                    if self._mobile_writer_stop and self._mobile_writer_stop.is_set():
                        logger.warning("手机端录制写线程：未收到任何帧即停止")
                        return

            if first_jpeg is None:
                logger.error("手机端录制写线程：15秒内未收到任何帧，录制失败")
                return

            # 解码第一帧获取分辨率
            frame = cv2.imdecode(np.frombuffer(first_jpeg, np.uint8), cv2.IMREAD_COLOR)
            if frame is None:
                logger.error("手机端录制：无法解码第一帧")
                return

            h, w = frame.shape[:2]
            self._mobile_frame_width = w
            self._mobile_frame_height = h

            path = str(self._mobile_recording_path)
            # 使用 mp4v 编码，不依赖 OpenH264
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(path, fourcc, 15.0, (w, h))
            if not writer.isOpened():
                logger.error(f"手机端录制：无法打开 VideoWriter: {path}")
                return

            self._first_frame_received = True
            writer.write(frame)
            self._mobile_frame_count = 1
            logger.info(f"手机端录制写线程已启动: {path} ({w}x{h})")

            # 持续写入后续帧
            while not (self._mobile_writer_stop and self._mobile_writer_stop.is_set()):
                try:
                    jpeg_bytes = self._mobile_frame_queue.get(timeout=1.0)
                    frame = cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)
                    if frame is not None:
                        fh, fw = frame.shape[:2]
                        if fw != w or fh != h:
                            frame = cv2.resize(frame, (w, h))
                        writer.write(frame)
                        self._mobile_frame_count += 1
                except queue.Empty:
                    continue
                except Exception as e:
                    logger.error(f"手机端录制写帧失败: {e}")

        except Exception as e:
            logger.error(f"手机端录制写线程异常: {e}")
        finally:
            if writer is not None:
                try:
                    writer.release()
                except Exception:
                    pass
            # 排空队列中剩余帧
            remaining = 0
            while self._mobile_frame_queue and not self._mobile_frame_queue.empty():
                try:
                    self._mobile_frame_queue.get_nowait()
                    remaining += 1
                except queue.Empty:
                    break
            logger.info(
                f"手机端录制写线程结束: {path}, "
                f"写入 {self._mobile_frame_count} 帧, "
                f"丢弃 {remaining} 帧"
            )

    def _convert_webm_to_wav(self, webm_path: str, wav_path: str):
        """使用 ffmpeg 将 WebM 音频转码为 WAV"""
        import subprocess
        import imageio_ffmpeg

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        cmd = [
            ffmpeg, "-y",
            "-i", webm_path,
            "-vn",           # 只取音频
            "-acodec", "pcm_s16le",
            "-ar", "44100",
            "-ac", "1",      # 单声道
            wav_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            logger.error(f"WebM→WAV 转码失败: {proc.stderr[-300:]}")
            raise RuntimeError(f"ffmpeg 转码失败")
        logger.info(f"WebM→WAV 转码完成: {wav_path}")

    def _merge_mobile_audio_to_video(self, video_path: str, audio_path: str) -> Optional[str]:
        """使用 ffmpeg 将手机端音频合并到视频中，返回合并后的视频路径"""
        import subprocess
        import imageio_ffmpeg

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        output_path = str(Path(video_path).with_stem(Path(video_path).stem + "_audio"))

        cmd = [
            ffmpeg, "-y",
            "-i", video_path,
            "-i", audio_path,
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "128k",
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-shortest",
            output_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            logger.warning(f"手机端音视频合并失败: {proc.stderr[-300:]}")
            return None

        logger.info(f"手机端音视频合并完成: {output_path}")
        return output_path
