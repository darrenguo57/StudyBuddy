"""
摄像头管理模块 - 使用 VidGear WriteGear 替代 OpenCV VideoWriter
修复: 视频录制黑屏问题（codec 兼容 + 帧深拷贝）
新增: 麦克风同步录音（pyaudio → WAV → FFmpeg 合并 MP4）
"""
import logging
import time
import threading
import queue
import wave
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Callable

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class CameraConfig:
    index: int = 0
    resolution: tuple = (1280, 720)
    record_fps: float = 15.0
    preview_fps: float = 30.0


class CameraManager:
    """摄像头管理器 - VidGear WriteGear 录制"""

    def __init__(self, config: CameraConfig = None, recording_dir: Path = None):
        self.config = config or CameraConfig()
        if recording_dir is None:
            self.recording_dir = Path("recordings")
        elif isinstance(recording_dir, str):
            self.recording_dir = Path(recording_dir)
        else:
            self.recording_dir = recording_dir
        self.recording_dir.mkdir(parents=True, exist_ok=True)

        self._cap: Optional[cv2.VideoCapture] = None
        self._writer = None
        self._recording = False
        self._paused = False
        self._frame_count = 0
        self._recording_start = 0.0

        self._capture_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        self._preview_frame: Optional[np.ndarray] = None
        self._preview_lock = threading.Lock()

        self.last_recording_path: Optional[Path] = None
        self._last_record_time: float = 0.0
        self._queue_drop_count: int = 0  # 录制丢帧计数器

        # 录制队列与写线程（解耦采集与编码，避免 VideoWriter 阻塞预览）
        self._record_queue: Optional[queue.Queue] = None
        self._writer_thread: Optional[threading.Thread] = None
        self._writer_stop_event: Optional[threading.Event] = None

        # 音频录制（pyaudio → WAV）
        self._audio_thread: Optional[threading.Thread] = None
        self._audio_queue: Optional[queue.Queue] = None
        self._audio_stop_event: Optional[threading.Event] = None
        self._audio_wav_path: Optional[Path] = None
        self._audio_start_time: float = 0.0
        self._audio_sample_rate: int = 44100
        self._audio_channels: int = 1
        self._audio_chunk_size: int = 1024
        self._audio_format: int = 8  # paInt16

        # 坐姿检测回调
        self.on_frame: Optional[Callable[[np.ndarray], None]] = None

        self.is_connected = False
        self.is_previewing = False
        self.is_recording = False

    # ── 连接与预览 ──

    @staticmethod
    def _is_near_black(frame: np.ndarray, nz_threshold: float = 5.0) -> bool:
        """判断帧是否近全黑（非零像素比例低于阈值）。

        使用非零像素比例替代亮度均值，避免 DirectShow YUYV→BGR
        转换异常时 max=255 但整体极暗的误判（参见 GH-xxx）。
        """
        nz_ratio = np.count_nonzero(frame) / frame.size * 100
        return nz_ratio < nz_threshold

    def _try_recovery_settings(self, cap: cv2.VideoCapture) -> bool:
        """尝试恢复设置：亮度/曝光/增益拉满，强制 MJPG 格式。"""
        try:
            cap.set(cv2.CAP_PROP_BRIGHTNESS, 255)
            cap.set(cv2.CAP_PROP_EXPOSURE, -2)
            cap.set(cv2.CAP_PROP_GAIN, 255)
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            import time as _time
            _time.sleep(0.3)
            # 预热
            for _ in range(10):
                cap.read()
            ret, frame = cap.read()
            if ret and frame is not None and not self._is_near_black(frame):
                logger.info("恢复设置成功：亮度/曝光/增益调整 + MJPG 强制")
                return True
        except Exception as e:
            logger.debug(f"恢复设置失败: {e}")
        return False

    def connect(self) -> bool:
        if self.is_connected:
            return True
        try:
            import sys
            backends = []
            if sys.platform == "win32":
                backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF]
            else:
                backends = [cv2.CAP_ANY]

            for backend in backends:
                backend_name = {
                    cv2.CAP_DSHOW: "DirectShow",
                    cv2.CAP_MSMF: "MSMF",
                    cv2.CAP_ANY: "默认",
                }.get(backend, str(backend))
                logger.info(f"尝试后端: {backend_name}")

                cap = cv2.VideoCapture(self.config.index, backend)
                if not cap.isOpened():
                    logger.info(f"  {backend_name} 无法打开")
                    cap.release()
                    continue

                # 读取默认分辨率帧
                ret, test_frame = cap.read()
                if not ret or test_frame is None:
                    logger.info(f"  {backend_name} 无法读取帧")
                    cap.release()
                    continue

                default_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                default_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                default_nz = np.count_nonzero(test_frame) / test_frame.size * 100
                logger.info(
                    f"  {backend_name} 默认帧: {default_w}x{default_h}, "
                    f"max={test_frame.max()}, mean={test_frame.mean():.1f}, nz={default_nz:.1f}%"
                )

                # 尝试设置目标分辨率
                cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.resolution[0])
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.resolution[1])
                cap.set(cv2.CAP_PROP_FPS, self.config.preview_fps)

                # 预热：丢弃前 10 帧让 AE 稳定（5 帧可能不足）
                import time as _time
                for _ in range(10):
                    cap.read()

                ret2, test_frame2 = cap.read()
                if ret2 and test_frame2 is not None:
                    new_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                    new_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                    new_nz = np.count_nonzero(test_frame2) / test_frame2.size * 100
                    logger.info(
                        f"  {backend_name} 设置后帧: {new_w}x{new_h}, "
                        f"max={test_frame2.max()}, mean={test_frame2.mean():.1f}, nz={new_nz:.1f}%"
                    )

                    if self._is_near_black(test_frame2):
                        logger.warning(
                            f"  {backend_name} 目标分辨率近黑帧(nz={new_nz:.1f}%)，"
                            f"尝试恢复设置"
                        )
                        if self._try_recovery_settings(cap):
                            # 恢复后重新检查
                            ret_r, frame_r = cap.read()
                            if ret_r and frame_r is not None and not self._is_near_black(frame_r):
                                logger.info(f"  {backend_name} 恢复后帧质量合格")
                                self._cap = cap
                                self.is_connected = True
                                actual_w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                                actual_h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                                logger.info(f"摄像头已连接: 后端={backend_name}, 分辨率=({actual_w}, {actual_h})")
                                return True

                        # 恢复失败，回退默认分辨率
                        logger.warning(f"  {backend_name} 恢复无效，回退默认分辨率")
                        cap.set(cv2.CAP_PROP_FRAME_WIDTH, default_w)
                        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, default_h)
                        for _ in range(10):
                            cap.read()
                        ret3, confirm = cap.read()
                        if ret3 and confirm is not None:
                            if self._is_near_black(confirm):
                                logger.warning(
                                    f"  {backend_name} 默认分辨率也近黑帧"
                                    f"(nz={np.count_nonzero(confirm)/confirm.size*100:.1f}%), 跳过"
                                )
                                cap.release()
                                continue
                        else:
                            logger.warning(f"  {backend_name} 回退后无法读帧，跳过")
                            cap.release()
                            continue
                else:
                    logger.warning(f"  {backend_name} 设置分辨率后无法读帧，保留默认分辨率")
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, default_w)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, default_h)
                    for _ in range(10):
                        cap.read()
                    ret3, confirm = cap.read()
                    if not ret3 or confirm is None:
                        logger.warning(f"  {backend_name} 回退后仍无法读帧，跳过")
                        cap.release()
                        continue

                # 成功
                self._cap = cap
                self.is_connected = True
                actual_w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                actual_h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                logger.info(f"摄像头已连接: 后端={backend_name}, 分辨率=({actual_w}, {actual_h})")
                return True

            logger.error(
                "所有后端均无法打开摄像头或输出近黑帧。"
                "建议：1) 用系统自带\"相机\"应用测试摄像头是否正常；"
                "2) 检查是否有 OBS 等虚拟摄像头占用 DirectShow 管线；"
                "3) 尝试 USB 换口或在设备管理器禁用→启用摄像头"
            )
            return False
        except Exception as e:
            logger.error(f"连接摄像头异常: {e}")
            return False

    def start_preview(self):
        if not self.is_connected:
            raise RuntimeError("摄像头未连接")
        self.is_previewing = True
        self._stop_event.clear()
        self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._capture_thread.start()
        logger.info("预览线程已启动")

    def _capture_loop(self):
        """采集线程主循环 - 外层保护防止异常导致线程崩溃"""
        consecutive_errors = 0
        frame_seq = 0
        last_hb = time.time()
        while not self._stop_event.is_set():
            try:
                frame_seq += 1
                # 每 5 秒健康检查日志
                if time.time() - last_hb > 5.0:
                    qsize = self._record_queue.qsize() if self._record_queue else 0
                    logger.info(f"[采集线程] 存活 帧#{frame_seq} 录制={self._recording} 写队列={qsize} 丢帧={self._queue_drop_count}")
                    last_hb = time.time()

                t0 = time.time()
                ret, frame = self._cap.read()
                dt_read = time.time() - t0
                if dt_read > 0.05:
                    logger.debug(f"[采集线程] cv2.read 耗时 {dt_read*1000:.0f}ms")

                if not ret:
                    consecutive_errors += 1
                    if consecutive_errors > 100:
                        logger.warning("摄像头连续读取失败，尝试重新连接")
                        self._reconnect_camera()
                        consecutive_errors = 0
                    time.sleep(0.01)
                    continue
                consecutive_errors = 0

                # DirectShow 可能返回 BGRA (4通道)，转换为 BGR (3通道)
                if len(frame.shape) == 3 and frame.shape[2] == 4:
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

                # 更新预览帧
                with self._preview_lock:
                    self._preview_frame = frame.copy()

                # 坐姿检测回调
                if self.on_frame is not None:
                    try:
                        cb_t0 = time.time()
                        self.on_frame(frame.copy())
                        cb_elapsed = time.time() - cb_t0
                        if cb_elapsed > 0.1:
                            logger.debug(f"[采集线程] on_frame 回调耗时 {cb_elapsed*1000:.0f}ms")
                    except Exception as e:
                        logger.error(f"[采集线程] on_frame 异常: {e}", exc_info=True)

                # 录制写入 - 按 record_fps 跳帧，推入队列由写线程处理
                if self._recording and not self._paused and self._record_queue is not None:
                    now = time.time()
                    if now - self._last_record_time >= 1.0 / self.config.record_fps:
                        try:
                            # 非阻塞放入队列，队列满则丢弃最旧帧
                            if self._record_queue.full():
                                try:
                                    self._record_queue.get_nowait()
                                except queue.Empty:
                                    pass
                                self._queue_drop_count += 1
                                if self._queue_drop_count % 30 == 1:
                                    logger.warning(f"[采集线程] 录制队列满! 已丢帧 {self._queue_drop_count} 次, 写线程跟不上")
                            self._record_queue.put_nowait(frame.copy())
                            self._last_record_time = now
                        except queue.Full:
                            pass
                        except Exception as e:
                            logger.error(f"录制入队失败: {e}")

                # 帧率控制
                time.sleep(1.0 / self.config.preview_fps)
            except Exception as e:
                logger.error(f"采集循环异常: {e}")
                time.sleep(0.1)

    def get_preview_frame(self) -> Optional[np.ndarray]:
        with self._preview_lock:
            return self._preview_frame.copy() if self._preview_frame is not None else None

    # ── 录制 ──

    def start_recording(self) -> Path:
        if not self.is_connected:
            raise RuntimeError("摄像头未连接")

        # 捕获当前摄像头实际分辨率（主线程安全读取，后续传给写线程）
        actual_w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if actual_w < 1 or actual_h < 1:
            raise RuntimeError(f"无法获取摄像头分辨率: {actual_w}x{actual_h}")

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        # AVI + MJPG 编码最快，避免 MP4 格式不支持 MJPG 导致回退到慢速 mp4v
        path = self.recording_dir / f"session_{timestamp}.avi"
        self.last_recording_path = path

        # 创建录制队列和写线程
        self._record_queue = queue.Queue(maxsize=60)
        self._writer_stop_event = threading.Event()
        self._writer_thread = threading.Thread(
            target=self._writer_loop,
            args=(path, actual_w, actual_h),
            daemon=True,
        )
        self._writer_thread.start()

        # 启动音频录制线程
        self._start_audio_recording(timestamp)

        self._recording = True
        self._paused = False
        self._frame_count = 0
        self._queue_drop_count = 0
        self._recording_start = time.time()
        self._last_record_time = 0.0
        self.is_recording = True
        logger.info(f"录制已启动: {path} ({actual_w}x{actual_h})")
        return path

    def _writer_loop(self, path: Path, actual_w: int, actual_h: int):
        """后台写线程：从队列取帧并写入视频（参数由主线程传入，避免跨线程访问 self._cap）"""
        # 检测 ffmpeg
        ffmpeg_available = False
        try:
            import shutil
            ffmpeg_available = shutil.which("ffmpeg") is not None
        except Exception:
            pass

        writer = None
        try:
            if ffmpeg_available:
                try:
                    from vidgear.gears import WriteGear
                    output_params = {
                        "-vcodec": "libx264",
                        "-crf": "23",
                        "-preset": "fast",
                        "-input_framerate": int(self.config.record_fps),
                        "-pix_fmt": "yuv420p",
                    }
                    writer = WriteGear(
                        output=str(path),
                        compression_mode=True,
                        logging=False,
                        **output_params,
                    )
                    logger.info(f"WriteGear 写线程已启动: {path}")
                except Exception as e:
                    logger.error(f"WriteGear 初始化失败: {e}")
                    ffmpeg_available = False

            if not ffmpeg_available:
                # 回退到 OpenCV VideoWriter，使用 MJPG 编码（最快）
                fourcc = cv2.VideoWriter_fourcc(*"MJPG")
                writer = cv2.VideoWriter(
                    str(path), fourcc, self.config.record_fps,
                    (actual_w, actual_h),
                )
                if not writer.isOpened():
                    raise RuntimeError(f"VideoWriter 打开失败: {path} ({actual_w}x{actual_h})")
                logger.info(f"VideoWriter 写线程已启动 (MJPG): {path} ({actual_w}x{actual_h})")

            last_hb = time.time()
            while not self._writer_stop_event.is_set() or not self._record_queue.empty():
                try:
                    frame = self._record_queue.get(timeout=0.5)
                    if frame is None:
                        continue
                    if writer is None:
                        continue

                    # 每 5 秒写线程健康检查
                    if time.time() - last_hb > 5.0:
                        logger.info(f"[写线程] 存活, 已写入 {self._frame_count} 帧, 队列待处理={self._record_queue.qsize()}")
                        last_hb = time.time()

                    # 帧尺寸校验：防御采集线程帧尺寸漂移
                    fh, fw = frame.shape[:2]
                    if fw != actual_w or fh != actual_h:
                        frame = cv2.resize(frame, (actual_w, actual_h))
                    t_w = time.time()
                    writer.write(frame)
                    dt_w = time.time() - t_w
                    if dt_w > 0.05:
                        logger.debug(f"[写线程] writer.write 耗时 {dt_w*1000:.0f}ms")
                    self._frame_count += 1
                except queue.Empty:
                    continue
                except Exception as e:
                    logger.error(f"写帧失败: {e}")
        finally:
            if writer is not None:
                try:
                    if hasattr(writer, "close"):
                        writer.close()
                    else:
                        writer.release()
                except Exception as e:
                    logger.error(f"释放 writer 出错: {e}")
            logger.info(f"写线程结束，共写入 {self._frame_count} 帧")

    # ── 音频录制 ──

    def _start_audio_recording(self, timestamp: str):
        """启动后台音频采集线程（pyaudio → WAV）"""
        self._audio_stop_event = threading.Event()
        self._audio_queue = queue.Queue()
        self._audio_wav_path = self.recording_dir / f"audio_{timestamp}.wav"
        self._audio_thread = threading.Thread(
            target=self._audio_capture_loop,
            daemon=True,
        )
        self._audio_thread.start()
        logger.info(f"音频录制线程已启动: {self._audio_wav_path}")

    def _audio_capture_loop(self):
        """音频采集线程：pyaudio 采集 → 队列 → WAV 文件"""
        raw_chunks = []  # 在内存中缓存所有原始音频帧
        try:
            import pyaudio
            p = pyaudio.PyAudio()
            stream = p.open(
                format=self._audio_format,
                channels=self._audio_channels,
                rate=self._audio_sample_rate,
                input=True,
                frames_per_buffer=self._audio_chunk_size,
            )
            logger.info(f"pyaudio 麦克风已打开: {self._audio_sample_rate}Hz, {self._audio_channels}ch")

            while not self._audio_stop_event.is_set():
                try:
                    data = stream.read(self._audio_chunk_size, exception_on_overflow=False)
                    raw_chunks.append(data)
                except Exception as e:
                    logger.error(f"音频采集异常: {e}")
                    break

            stream.stop_stream()
            stream.close()
            p.terminate()
            logger.info(f"音频采集结束，共 {len(raw_chunks)} 帧")
        except ImportError:
            logger.warning("pyaudio 未安装，尝试 sounddevice 回退")
            raw_chunks = self._audio_capture_sounddevice()
        except OSError as e:
            logger.warning(f"pyaudio 打开麦克风失败 ({e})，尝试 sounddevice 回退")
            raw_chunks = self._audio_capture_sounddevice()
        except Exception as e:
            logger.error(f"音频采集严重异常: {e}")

        # 写入 WAV 文件
        if raw_chunks:
            try:
                wf = wave.open(str(self._audio_wav_path), "wb")
                wf.setnchannels(self._audio_channels)
                wf.setsampwidth(2)  # 16-bit = 2 bytes
                wf.setframerate(self._audio_sample_rate)
                wf.writeframes(b"".join(raw_chunks))
                wf.close()
                logger.info(f"WAV 已保存: {self._audio_wav_path} ({len(raw_chunks)} 帧)")
            except Exception as e:
                logger.error(f"WAV 写入失败: {e}")
                self._audio_wav_path = None
        else:
            logger.warning("未采集到任何音频数据")
            self._audio_wav_path = None

    def _audio_capture_sounddevice(self) -> list:
        """sounddevice 回退采集"""
        raw_chunks = []
        try:
            import sounddevice as sd
            import numpy as np

            def _callback(indata, frames, time_info, status):
                if status:
                    logger.debug(f"sounddevice status: {status}")
                raw_chunks.append(indata.tobytes())

            with sd.InputStream(
                samplerate=self._audio_sample_rate,
                channels=self._audio_channels,
                dtype="int16",
                callback=_callback,
            ):
                logger.info("sounddevice 麦克风已打开")
                while not self._audio_stop_event.is_set():
                    sd.sleep(100)

            logger.info(f"sounddevice 采集结束，共 {len(raw_chunks)} 帧")
        except ImportError:
            logger.warning("sounddevice 也未安装，跳过音频录制")
        except Exception as e:
            logger.error(f"sounddevice 采集失败: {e}")
        return raw_chunks

    def _stop_audio_recording(self):
        """停止音频录制并等待线程结束"""
        if self._audio_stop_event is not None:
            self._audio_stop_event.set()
        # 等待音频线程完成 WAV 写入
        thread = getattr(self, "_audio_thread", None)
        if thread and thread.is_alive():
            thread.join(timeout=5.0)

    def get_audio_wav_path(self) -> Optional[Path]:
        """获取录制的 WAV 路径"""
        return self._audio_wav_path

    def pause_recording(self):
        self._paused = True

    def resume_recording(self):
        self._paused = False

    def stop_recording(self) -> Optional[Path]:
        self._recording = False
        self.is_recording = False
        self.on_frame = None

        # 通知写线程停止
        if self._writer_stop_event is not None:
            self._writer_stop_event.set()
        # 等待队列排空
        if self._writer_thread is not None:
            self._writer_thread.join(timeout=10.0)
            self._writer_thread = None

        # 停止音频录制
        self._stop_audio_recording()

        path = self.last_recording_path
        duration = time.time() - self._recording_start
        logger.info(
            f"录制结束: {path}, 帧数={self._frame_count}, "
            f"时长={duration:.1f}s, 实际FPS={self._frame_count/max(duration,0.1):.1f}, "
            f"音频WAV={self._audio_wav_path}"
        )
        return path

    # ── 释放 ──

    def _reconnect_camera(self):
        """尝试重新连接摄像头（Windows 优先 DSHOW 后端），并验证帧质量"""
        try:
            import sys, time as _time
            if self._cap is not None:
                self._cap.release()
            backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
            self._cap = cv2.VideoCapture(self.config.index, backend)
            if self._cap.isOpened():
                self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.resolution[0])
                self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.resolution[1])
                # 预热 + 质量检查
                for _ in range(10):
                    self._cap.read()
                ret, frame = self._cap.read()
                if ret and frame is not None and not self._is_near_black(frame):
                    logger.info("摄像头重新连接成功，帧质量合格")
                else:
                    nz = np.count_nonzero(frame) / frame.size * 100 if (ret and frame is not None) else 0
                    logger.warning(f"摄像头重新连接成功但帧近黑(nz={nz:.1f}%)")
            else:
                logger.error("摄像头重新连接失败")
        except Exception as e:
            logger.error(f"重新连接摄像头异常: {e}")

    def release(self):
        self._stop_event.set()
        if self._capture_thread and self._capture_thread.is_alive():
            self._capture_thread.join(timeout=2.0)
        if self._recording:
            self.stop_recording()
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self.is_connected = False
        self.is_previewing = False
        logger.info("摄像头已释放")

    @property
    def recording_duration(self) -> float:
        if self._recording:
            return time.time() - self._recording_start
        return 0.0
