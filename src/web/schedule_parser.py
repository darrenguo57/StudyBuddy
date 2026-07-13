"""
暑假作业排程解析器 - 从 HTML 计划表中提取结构化任务数据
"""
import re
import json
import logging
from pathlib import Path
from typing import List, Dict, Optional
from dataclasses import dataclass, field, asdict
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


@dataclass
class Task:
    """单条作业任务"""
    subject: str       # math / chinese / english / pe
    category: str      # daily_practice / reading / handwriting / recitation / aloud / preview / copy / writing / exercise / review / ulearn
    description: str   # 具体任务描述
    duration_minutes: int = 0  # 预计用时（分钟）
    is_optional: bool = False


@dataclass
class DayPlan:
    """单日作业计划"""
    day: int            # 第几天 (1-30)
    weekday: str        # 周一~周日
    phase: str          # 阶段名称
    day_type: str       # uclass / normal / weekend / review
    tasks: List[Task] = field(default_factory=list)


def _clean_text(text: str) -> str:
    """清理多余空白"""
    return re.sub(r'\s+', ' ', text.strip())


def _infer_category(desc: str) -> str:
    """根据描述推断任务类别"""
    desc_lower = desc.lower()
    if '每日一练' in desc:
        return 'daily_practice'
    if '特色作业' in desc or '拓展' in desc:
        return 'extension'
    if '预习' in desc:
        return 'preview'
    if '阅读' in desc:
        return 'reading'
    if '练字' in desc or '钢笔字' in desc:
        return 'handwriting'
    if '背诵' in desc or '日积月累' in desc:
        return 'recitation'
    if '朗读' in desc:
        return 'aloud'
    if '抄写' in desc or '单词' in desc and '抄' in desc:
        return 'copy'
    if '作文' in desc or '日记' in desc:
        return 'writing'
    if 'u学' in desc_lower or '线上练习' in desc:
        return 'ulearn'
    if '听写' in desc or '闯关' in desc:
        return 'dictation'
    if '阶段小结' in desc or '复习' in desc:
        return 'review'
    if '休息' in desc or '无' in desc:
        return 'rest'
    # 体育类
    if any(w in desc for w in ['高抬腿', '跳绳', '深蹲', '开合跳', '平板支撑', '坐位体前屈',
                                 '单脚站立', '后踢腿', '靠墙静蹲', '散步', '慢跑', '拍球',
                                 '户外', '主动休息', '拉伸', '自由活动']):
        return 'exercise'
    return 'other'


def _infer_duration(task: Task) -> int:
    """推断任务预计用时（分钟）"""
    cat = task.category
    desc = task.description
    if cat == 'daily_practice':
        return 30
    if cat == 'reading':
        return 30
    if cat == 'handwriting':
        return 15
    if cat == 'recitation':
        return 10
    if cat == 'aloud':
        if '课文+单词' in desc:
            return 15
        return 10
    if cat == 'preview':
        return 15
    if cat == 'copy':
        return 10
    if cat == 'writing':
        return 30
    if cat == 'dictation':
        return 10
    if cat == 'ulearn':
        return 20
    if cat == 'exercise':
        return 15
    if cat == 'review':
        return 10
    if cat == 'rest':
        return 0
    return 10


def _parse_task_cell(cell, subject: str) -> List[Task]:
    """解析单个学科单元格中的任务列表

    HTML 结构：<td> <span class="task-tag">标签</span> 内容<br> ... </td>
    按 <br> 切分行，每行一个任务（tag + 后续文字合并）
    """
    tasks = []
    html = str(cell)
    soup = BeautifulSoup(html, 'html.parser')

    # 如果只有纯文本（无 br），整体作为一个任务
    if not soup.find('br'):
        text = soup.get_text(' ', strip=True)
        text = _clean_text(text)
        if text:
            category = _infer_category(text)
            task = Task(subject=subject, category=category, description=text)
            task.duration_minutes = _infer_duration(task)
            tasks.append(task)
        return tasks

    # 有 br：按 br 拆分
    # 先把 cell 的 HTML 按 <br/> 或 <br> 拆分
    parts = re.split(r'<br\s*/?>', html, flags=re.IGNORECASE)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # 去掉残留的 HTML 标签
        sub_soup = BeautifulSoup(part, 'html.parser')
        text = sub_soup.get_text(' ', strip=True)
        text = _clean_text(text)
        if not text:
            continue
        category = _infer_category(text)
        task = Task(
            subject=subject,
            category=category,
            description=text,
            is_optional=(category == 'rest'),
        )
        task.duration_minutes = _infer_duration(task)
        tasks.append(task)
    return tasks


def parse_schedule(html_path: str) -> List[DayPlan]:
    """
    解析 HTML 暑假作业计划表

    返回 30 天的 DayPlan 列表
    """
    path = Path(html_path)
    if not path.exists():
        raise FileNotFoundError(f"排程文件不存在: {html_path}")

    with open(path, 'r', encoding='utf-8') as f:
        html = f.read()

    soup = BeautifulSoup(html, 'html.parser')
    table = soup.find('table', class_='schedule-table')
    if not table:
        raise ValueError("未找到 schedule-table")

    plans: List[DayPlan] = []
    current_phase = ""
    day_map = {'周一': 'Monday', '周二': 'Tuesday', '周三': 'Wednesday',
               '周四': 'Thursday', '周五': 'Friday', '周六': 'Saturday', '周日': 'Sunday'}

    for row in table.find('tbody').find_all('tr'):
        # 阶段分隔行（class="phase-row" 在 tr 上）
        if 'phase-row' in row.get('class', []):
            phase_td = row.find('td')
            if phase_td:
                current_phase = phase_td.get_text(strip=True)
                # 去掉 📚 📖 🔥 等 emoji 前缀，保留中文
                current_phase = re.sub(r'^[^\u4e00-\u9fff]*', '', current_phase)
            continue

        cols = row.find_all('td')
        if len(cols) < 6:
            continue

        # 第1列：天数标签
        day_span = cols[0].find('span', class_='day-num')
        if not day_span:
            continue
        day_num = int(day_span.get_text(strip=True))

        # 判断 day_type
        classes = day_span.get('class', [])
        day_type = 'normal'
        for c in classes:
            if 'day-uclass' in c:
                day_type = 'uclass'
            elif 'day-weekend' in c:
                day_type = 'weekend'
            elif 'day-review' in c:
                day_type = 'review'

        # 第2列：星期
        weekday_raw = cols[1].get_text(strip=True)
        weekday = weekday_raw

        # 第3列：数学
        math_tasks = _parse_task_cell(cols[2], 'math')

        # 第4列：语文
        chinese_tasks = _parse_task_cell(cols[3], 'chinese')

        # 第5列：英语
        english_tasks = _parse_task_cell(cols[4], 'english')

        # 第6列：体育
        pe_tasks = _parse_task_cell(cols[5], 'pe')

        plan = DayPlan(
            day=day_num,
            weekday=weekday,
            phase=current_phase,
            day_type=day_type,
            tasks=math_tasks + chinese_tasks + english_tasks + pe_tasks,
        )
        plans.append(plan)

    logger.debug(f"解析完成: {len(plans)} 天作业计划")
    return plans


def plans_to_json(plans: List[DayPlan]) -> str:
    """将计划列表序列化为 JSON"""
    data = []
    for p in plans:
        data.append({
            'day': p.day,
            'weekday': p.weekday,
            'phase': p.phase,
            'day_type': p.day_type,
            'tasks': [asdict(t) for t in p.tasks],
        })
    return json.dumps(data, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    # 测试解析
    project_root = Path(__file__).resolve().parent.parent.parent
    schedule_path = project_root / "docs" / "summer_homework_plan.html"
    if not schedule_path.exists():
        schedule_path = Path(r'G:\思思学习资料\summer_homework_plan.html')
    plans = parse_schedule(str(schedule_path))
    for p in plans:
        print(f"第{p.day}天 [{p.day_type}] {p.weekday} - {len(p.tasks)}个任务")
    print(f"\n总计: {len(plans)} 天, {sum(len(p.tasks) for p in plans)} 个任务")
    print(plans_to_json(plans[:3]))
