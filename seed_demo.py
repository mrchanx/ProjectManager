# -*- coding: utf-8 -*-
"""创建演示项目：带真实状态的六维力传感器产研流程示例数据。"""
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import db
from app.templates import TEMPLATES


def main():
    tpl = TEMPLATES["six_axis"]
    conn = db.get_conn()
    old = conn.execute("SELECT id FROM projects WHERE name=?", ("演示项目 · XX型六维力传感器 2026-01批",)).fetchall()
    conn.close()
    for r in old:
        db.delete_project(r["id"])

    today = date.today()
    pid = db.create_project("演示项目 · XX型六维力传感器 2026-01批",
                            "FSS-2026-001", tpl["name"], tpl["tasks"])
    tasks = db.list_tasks(pid)

    # 计划日期：整体从 20 天前排到 60 天后
    span = len(tasks)
    for t in tasks:
        i = t["order_idx"]
        s = today - timedelta(days=20) + timedelta(days=int(i * 80 / span))
        e = s + timedelta(days=4)
        db.update_task(t["id"], "plan_start", s.isoformat())
        db.update_task(t["id"], "plan_end", e.isoformat())

    # 模拟进度：阶段一全部完成
    plan = {
        "弹性体仿真与制作": ("done", 100),
        "桥应变参数交付": ("done", 100),
        "原理图与PCB绘制": ("done", 100),
        "机械结构微调": ("done", 100),
        "板卡制作与投板确认": ("done", 100),
        "嵌入式demo预研": ("done", 100),
        "弹性体投加工": ("done", 100),
        "弹性体到货检验": ("rework", 60),   # 返工循环示例
        "编制SN码": ("doing", 40),
        "板卡到货测试": ("doing", 70),
        "线缆规格提供": ("blocked", 30),    # 受阻示例
        "软件版本分发": ("doing", 50),
    }
    for t in tasks:
        if t["name"] in plan:
            st, pr = plan[t["name"]]
            db.update_task(t["id"], "status", st)
            db.update_task(t["id"], "progress", pr)
            if st == "done":
                db.update_task(t["id"], "actual_end", today.isoformat())

    tasks = db.list_tasks(pid)
    print("演示项目已创建：%d 个任务，总进度 %d%%，卡点 %d 处"
          % (len(tasks), db.project_progress(tasks),
             len(db.analyze_blockers(tasks))))


if __name__ == "__main__":
    main()
