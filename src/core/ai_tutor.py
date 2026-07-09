"""
AI 辅导模块 - 最简实现
通过 Ollama 本地模型提供题目解答
"""
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

OLLAMA_URL = "http://localhost:11434/api/generate"

# 系统提示词：引导 AI 以辅导方式回答，而非直接给答案
SYSTEM_PROMPT = """你是思思的学习伙伴 AI 导师。你是一位耐心、鼓励型的辅导老师。

规则：
1. 用小学生能听懂的语言解释
2. 分步骤引导思考，不直接给出最终答案
3. 如果学生明显没有思路，给一个关键提示再让他尝试
4. 回答简洁，不超过 150 字
5. 始终用中文回答"""


def ollama_chat(prompt: str, model: str = "qwen2.5:0.5b") -> Optional[str]:
    """调用 Ollama 生成回复"""
    full_prompt = f"{SYSTEM_PROMPT}\n\n学生提问：{prompt}"
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model": model,
                "prompt": full_prompt,
                "stream": False,
                "options": {"num_predict": 300, "temperature": 0.6},
            },
            timeout=30,
        )
        if resp.status_code == 200:
            return resp.json().get("response", "").strip()
        logger.warning(f"Ollama 返回非 200: {resp.status_code}")
        return None
    except (requests.RequestException, ValueError) as e:
        logger.warning(f"Ollama 调用失败: {e}")
        return None


def tutor(question: str, subject: str = "") -> Optional[str]:
    """
    辅导接口：接收问题，返回 AI 解答
    如果 Ollama 不可用，返回 None（前端应展示离线提示）
    """
    subject_hint = f"（科目：{subject}）" if subject else ""
    prompt = f"{subject_hint}\n问题：{question}"
    result = ollama_chat(prompt)
    return result


def is_ollama_available() -> bool:
    """检查 Ollama 服务是否可用"""
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=3)
        return resp.status_code == 200
    except (requests.RequestException, ValueError):
        return False
