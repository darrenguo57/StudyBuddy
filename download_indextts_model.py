"""
IndexTTS-2 模型下载脚本（支持断点续传）
"""
import os, sys, time

CACHE_DIR = r"G:\StudyBuddy\models\indextts_checkpoints"
LOG = r"G:\StudyBuddy\models\download_log.txt"

def log(msg):
    t = time.strftime("%H:%M:%S")
    line = f"[{t}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

log("开始下载 IndexTeam/IndexTTS-2")
try:
    from modelscope import snapshot_download
except ImportError:
    os.system(f"{sys.executable} -m pip install modelscope -q")
    from modelscope import snapshot_download

path = snapshot_download("IndexTeam/IndexTTS-2", cache_dir=CACHE_DIR)
log(f"下载完成: {path}")
