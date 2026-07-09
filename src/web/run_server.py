import sys
sys.path.insert(0, 'G:/StudyBuddy')
from src.web.server import WebServer
import time

server = WebServer(schedule_html_path=r'G:\思思学习资料\summer_homework_plan.html')
server.start_async()
print("SERVER_READY")
# 保持进程存活
while True:
    time.sleep(1)
