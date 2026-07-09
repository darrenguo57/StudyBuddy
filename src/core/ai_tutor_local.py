"""
AI 辅导模块 - 本地 llama.cpp 推理
使用 llama-server 提供 OpenAI 兼容 API，纯本地离线运行。
如果本地模型不可用，自动回退到 Ollama。
"""
import logging
import os
import subprocess
import time
import requests
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# llama.cpp 路径配置
LLAMA_SERVER_EXE = PROJECT_ROOT / "tools" / "llama.cpp" / "llama-server.exe"
MODEL_PATH = PROJECT_ROOT / "tools" / "models" / "qwen2.5-1.5b-instruct-q4_k_m.gguf"

# 本地服务端口（与 Web 服务器 8910 错开）
LOCAL_SERVER_PORT = 8911
LOCAL_SERVER_URL = f"http://127.0.0.1:{LOCAL_SERVER_PORT}"

# 系统提示词
SYSTEM_PROMPT = """你是思思的学习伙伴 AI 导师。你是一位耐心、鼓励型的辅导老师。

规则：
1. 用小学生能听懂的语言解释
2. 分步骤引导思考，不直接给出最终答案
3. 如果学生明显没有思路，给一个关键提示再让他尝试
4. 回答简洁，不超过 150 字
5. 始终用中文回答"""

# 全局状态
_server_process: Optional[subprocess.Popen] = None
_server_lock = threading.Lock()
_server_ready = False
_last_error: str = ""


def _is_server_alive() -> bool:
    """检查 llama-server 是否在运行"""
    try:
        resp = requests.get(f"{LOCAL_SERVER_URL}/health", timeout=2)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def start_local_server() -> bool:
    """启动 llama-server 后台进程（线程安全）"""
    global _server_process, _server_ready, _last_error

    with _server_lock:
        if _server_ready and _is_server_alive():
            return True

        if not LLAMA_SERVER_EXE.exists():
            _last_error = f"llama-server.exe 未找到: {LLAMA_SERVER_EXE}"
            logger.warning(_last_error)
            return False

        if not MODEL_PATH.exists():
            _last_error = f"模型文件未找到: {MODEL_PATH}"
            logger.warning(_last_error)
            return False

        # 先杀掉可能残留的旧进程
        _kill_server()

        try:
            cmd = [
                str(LLAMA_SERVER_EXE),
                "-m", str(MODEL_PATH),
                "--host", "127.0.0.1",
                "--port", str(LOCAL_SERVER_PORT),
                "--ctx-size", "2048",
                "--threads", "4",
                "--no-webui",
            ]
            _server_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            logger.info(f"llama-server 已启动 (PID={_server_process.pid})")

            # 等待服务就绪（最多 30 秒）
            for i in range(30):
                time.sleep(1)
                if _is_server_alive():
                    _server_ready = True
                    _last_error = ""
                    logger.info("llama-server 就绪")
                    return True

            _last_error = "llama-server 启动超时"
            logger.warning(_last_error)
            return False

        except Exception as e:
            _last_error = str(e)
            logger.warning(f"llama-server 启动失败: {e}")
            return False


def _kill_server():
    """终止 llama-server 进程"""
    global _server_process, _server_ready
    if _server_process and _server_process.poll() is None:
        try:
            _server_process.terminate()
            _server_process.wait(timeout=5)
        except Exception:
            try:
                _server_process.kill()
            except Exception:
                pass
    _server_process = None
    _server_ready = False


def stop_local_server():
    """停止 llama-server"""
    with _server_lock:
        _kill_server()
        logger.info("llama-server 已停止")


def local_chat(prompt: str) -> Optional[str]:
    """通过本地 llama-server 生成回复"""
    if not _server_ready and not start_local_server():
        logger.warning(f"本地模型不可用: {_last_error}")
        return None

    try:
        resp = requests.post(
            f"{LOCAL_SERVER_URL}/v1/chat/completions",
            json={
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 300,
                "temperature": 0.6,
                "stop": [],
            },
            timeout=60,
        )
        if resp.status_code == 200:
            data = resp.json()
            choices = data.get("choices", [])
            if choices:
                return choices[0]["message"]["content"].strip()
        logger.warning(f"本地模型返回非 200: {resp.status_code}")
        return None
    except requests.RequestException as e:
        logger.warning(f"本地模型调用失败: {e}")
        return None


# ── 公开接口（兼容原有 ai_tutor 接口） ──

def tutor(question: str, subject: str = "") -> Optional[str]:
    """
    辅导接口：优先使用本地 llama.cpp，失败则回退到 Ollama。
    """
    subject_hint = f"（科目：{subject}）" if subject else ""
    prompt = f"{subject_hint}\n问题：{question}"

    # 优先本地模型
    result = local_chat(prompt)
    if result:
        return result

    # 回退到 Ollama
    logger.info("本地模型不可用，回退到 Ollama")
    try:
        from .ai_tutor import ollama_chat
        return ollama_chat(prompt)
    except Exception:
        pass

    return None


def is_local_available() -> bool:
    """检查本地 llama.cpp 模型是否可用"""
    return MODEL_PATH.exists() and LLAMA_SERVER_EXE.exists()


def is_model_ready() -> bool:
    """检查本地模型是否已加载就绪"""
    return _server_ready and _is_server_alive()


def get_tutor_status() -> dict:
    """获取 AI 辅导服务状态"""
    local_ok = is_local_available()
    model_ready = is_model_ready() if local_ok else False
    
    status = {
        "local_model_available": local_ok,
        "local_model_ready": model_ready,
        "last_error": _last_error if local_ok and not model_ready else "",
    }
    
    # 检查 Ollama 作为后备
    try:
        from .ai_tutor import is_ollama_available
        status["ollama_available"] = is_ollama_available()
    except Exception:
        status["ollama_available"] = False

    return status
