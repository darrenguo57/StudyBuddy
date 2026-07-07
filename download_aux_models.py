"""下载 IndexTTS-2 全部辅助模型（BigVGAN + CAM++ + 语义编解码 + w2v-bert）"""
import sys, os, time

sys.path.insert(0, r'C:\Users\Administrator\index-tts')

LOG = r'G:\StudyBuddy\models\download_log.txt'

def log(msg):
    t = time.strftime("%H:%M:%S")
    line = f"[{t}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

MODEL_DIR = r'G:\StudyBuddy\models\indextts_checkpoints'

log("开始下载辅助模型...")
try:
    from indextts.utils.model_download import ensure_models_available
    paths = ensure_models_available(MODEL_DIR)
    log(f"下载完成: {paths}")
except Exception as e:
    log(f"下载失败: {e}")
    import traceback
    log(traceback.format_exc())
