import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))
from src.web.server import WebServer
import time

schedule_path = project_root / "docs" / "summer_homework_plan.html"
if not schedule_path.exists():
    schedule_path = Path(r'G:\思思学习资料\summer_homework_plan.html')
server = WebServer(schedule_html_path=str(schedule_path))
server.start_async()
print("SERVER_READY")
# 保持进程存活
while True:
    time.sleep(1)
