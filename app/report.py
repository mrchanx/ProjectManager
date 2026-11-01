# -*- coding: utf-8 -*-
"""进度报告导出：HTML（自动打开）+ Excel。"""
import html as _html
import os
import webbrowser
from datetime import datetime

from . import db, paths


def esc(s):
    """HTML 转义，防止任务名/备注中的特殊字符破坏页面结构。"""
    return _html.escape(str(s or ""), quote=True)


def links_text(raw):
    """关联文件列：多项各占一行（兼容旧的单路径数据）。"""
    return "\n".join(db.parse_links(raw))

STATUS_NAMES = db.STATUS_NAMES

STATUS_COLORS = {
    "pending": "#9AA0A6",
    "doing": "#2F80ED",
    "done": "#27AE60",
    "blocked": "#EB5757",
    "rework": "#F2994A",
}


def _stage_stats(tasks, project_id=None):
    # 阶段顺序以项目登记的为准（先建的在前，用户可调顺序），
    # 老项目无登记记录时按任务里出现的顺序排（老项目的任务顺序＝内置模板顺序，
    # 结果与内置四阶段一致）。刻意不回退内置 STAGES：空白项目里那些空阶段
    # 本来就该没有，硬塞进来只会让报告和界面/任务树对不上。
    stages = db.list_stages(project_id) if project_id else []
    for t in tasks:
        s = t["stage"] or "未分组"
        if s not in stages:
            stages.append(s)
    stats = []
    for s in stages:
        st = [t for t in tasks if (t["stage"] or "未分组") == s]
        if not st:
            continue
        prog = sum(int(t["progress"] or 0) for t in st) / len(st)
        done = sum(1 for t in st if t["status"] == "done")
        stats.append({"stage": s, "count": len(st), "done": done,
                      "progress": round(prog)})
    return stats


def build_report_data(project, tasks):
    blockers = db.analyze_blockers(tasks)
    by_id = {t["id"]: t for t in tasks}

    def dep_names(t):
        names = []
        for did in db.task_dep_ids(t):
            d = by_id.get(did)
            if d:
                names.append(esc(d["name"]))
        return ", ".join(names)

    rows = []
    for t in tasks:
        rows.append({
            "stage": t["stage"], "name": esc(t["name"]), "owner": esc(t["owner"]),
            "status": STATUS_NAMES.get(t["status"], t["status"]),
            "status_key": t["status"], "progress": int(t["progress"] or 0),
            "plan": "%s ~ %s" % (t["plan_start"] or "-", t["plan_end"] or "-"),
            "deps": dep_names(t), "note": esc(t["note"]),
            "file_link": links_text(t["file_link"]),
            "blockers": [esc(b) for b in blockers.get(t["id"], [])],
        })
    # 批次个体进度（有个体的项目才有）
    unit_rows = []
    for s in db.unit_stats(project.get("id"), tasks) if project.get("id") else []:
        u = s["unit"]
        unit_rows.append({
            "sn": esc(u["sn"]), "name": esc(u["name"]),
            "progress": s["progress"], "current": esc(s["current"]),
            "issues": esc("；".join(s["issues"])),
        })
    return {
        "project": project,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "total_progress": db.project_progress(tasks),
        "total": len(tasks),
        "done": sum(1 for t in tasks if t["status"] == "done"),
        "doing": sum(1 for t in tasks if t["status"] == "doing"),
        "blocked": sum(1 for t in tasks if t["status"] == "blocked"),
        "rework": sum(1 for t in tasks if t["status"] == "rework"),
        "pending": sum(1 for t in tasks if t["status"] == "pending"),
        "stage_stats": _stage_stats(tasks, project.get("id")),
        "rows": rows,
        "blocker_count": len(blockers),
        "unit_rows": unit_rows,
        "unit_count": len(unit_rows),
    }


def export_html(project, tasks, path=None):
    d = build_report_data(project, tasks)
    if not path:
        out_dir = str(paths.REPORT_DIR)
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "报告_%s_%s.html" % (
            project["name"], datetime.now().strftime("%Y%m%d_%H%M%S")))

    cards = ""
    for label, val, cls in [
            ("总进度", "%d%%" % d["total_progress"], ""),
            ("已完成", "%d / %d" % (d["done"], d["total"]), ""),
            ("进行中", d["doing"], "c-blue"),
            ("受阻/返工", d["blocked"] + d["rework"], "c-red"),
            ("未开始", d["pending"], "")]:
        cards += ('<div class="card %s"><div class="num">%s</div>'
                  '<div class="lbl">%s</div></div>' % (cls, val, label))

    stages_html = ""
    for s in d["stage_stats"]:
        stages_html += (
            '<div class="stage-row"><span class="stage-name">%s</span>'
            '<div class="bar"><div class="fill" style="width:%d%%"></div></div>'
            '<span class="stage-pct">%d%%（%d/%d 完成）</span></div>'
            % (esc(s["stage"]), s["progress"], s["progress"], s["done"], s["count"]))

    blocker_rows = ""
    has_blocker = False
    for r in d["rows"]:
        if r["blockers"]:
            has_blocker = True
            blocker_rows += ('<tr><td>%s</td><td>%s</td><td>%s</td>'
                             '<td class="red">%s</td></tr>'
                             % (r["name"], r["owner"], esc(r["stage"]),
                                "；".join(r["blockers"])))
    if not has_blocker:
        blocker_rows = ('<tr><td colspan="4" class="ok">当前无卡点，流程正常'
                        '推进</td></tr>')

    units_html = ""
    if d["unit_rows"]:
        for ur in d["unit_rows"]:
            issues = ('<span class="red">%s</span>' % ur["issues"]
                      if ur["issues"] else "—")
            units_html += ('<tr><td class="mono">%s</td><td>%s</td>'
                           '<td class="nowrap">%d%%</td><td>%s</td>'
                           '<td>%s</td></tr>'
                           % (ur["sn"] or "—", ur["name"] or "—",
                              ur["progress"], ur["current"], issues))
    else:
        units_html = ('<tr><td colspan="5" class="ok">本批次未启用个体跟踪'
                      '（任务菜单 → 批次个体管理）</td></tr>')

    detail_rows = ""
    for r in d["rows"]:
        badge = ('<span class="badge" style="background:%s">%s</span>'
                 % (STATUS_COLORS.get(r["status_key"], "#999"), r["status"]))
        blocker_txt = ("<br>".join(r["blockers"]) if r["blockers"] else "")
        detail_rows += (
            '<tr><td>%s</td><td>%s</td><td>%s</td><td class="nowrap">%s</td>'
            '<td class="nowrap"><div class="mini-bar"><div class="fill" style="width:%d%%">'
            '</div></div><span class="mini-pct">%d%%</span></td>'
            '<td class="mono">%s</td><td>%s</td><td class="red">%s</td></tr>'
            % (esc(r["stage"]), r["name"], r["owner"], badge, r["progress"],
               r["progress"], r["plan"], r["deps"], blocker_txt))

    html = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<title>%(proj_name)s · 进度报告</title>
<style>
body{font-family:"Microsoft YaHei",sans-serif;margin:0;background:#f5f6f8;color:#333}
.wrap{max-width:1100px;margin:0 auto;padding:32px 24px}
h1{font-size:22px;margin:0 0 4px}
.sub{color:#888;font-size:13px;margin-bottom:24px}
.cards{display:flex;gap:12px;margin-bottom:24px;flex-wrap:wrap}
.card{background:#fff;border-radius:8px;padding:14px 22px;min-width:120px;
box-shadow:0 1px 2px rgba(0,0,0,.06)}
.card .num{font-size:26px;font-weight:600}
.card .lbl{font-size:12px;color:#888;margin-top:2px}
.c-blue .num{color:#2F80ED}.c-red .num{color:#EB5757}
h2{font-size:16px;margin:28px 0 12px;border-left:4px solid #2F80ED;padding-left:10px}
.stage-row{display:flex;align-items:center;gap:12px;margin-bottom:10px}
.stage-name{width:130px;font-size:13px;white-space:nowrap;flex-shrink:0}
.stage-pct{width:180px;font-size:12px;color:#666;white-space:nowrap;flex-shrink:0}
.bar{flex:1;height:14px;background:#e8e8e8;border-radius:7px;overflow:hidden}
.fill{height:100%%;background:linear-gradient(90deg,#2F80ED,#56CCF2)}
table{width:100%%;border-collapse:collapse;background:#fff;font-size:13px;
border-radius:8px;overflow:hidden;box-shadow:0 1px 2px rgba(0,0,0,.06)}
th,td{padding:8px 10px;border-bottom:1px solid #eee;text-align:left;
vertical-align:top}
th{background:#fafafa;font-weight:600;font-size:12px;color:#666}
.badge{display:inline-block;white-space:nowrap;color:#fff;font-size:11px;
padding:2px 10px;border-radius:10px}
.nowrap{white-space:nowrap}
.mini-pct{white-space:nowrap}
.red{color:#EB5757}.ok{text-align:center;color:#27AE60;padding:18px}
.mono{font-family:Consolas,monospace;font-size:12px;color:#666}
.mini-bar{display:inline-block;width:70px;height:8px;background:#e8e8e8;
border-radius:4px;overflow:hidden;vertical-align:middle}
.mini-bar .fill{background:#27AE60}
.mini-pct{font-size:12px;margin-left:6px;color:#666}
</style></head><body><div class="wrap">
<h1>%(proj_name)s · 进度报告</h1>
<div class="sub">项目编号：%(proj_code)s ｜ 生成时间：%(generated)s</div>
<div class="cards">%(cards)s</div>
<h2>阶段进度</h2>%(stages_html)s
<h2>批次个体进度（%(unit_count)s 台）</h2>
<table><tr><th>SN</th><th>名称</th><th>进度</th><th>当前停在</th><th>受阻/返工</th></tr>
%(units_html)s</table>
<h2>卡点与风险（%(blocker_count)s 项）</h2>
<table><tr><th>任务</th><th>负责人</th><th>阶段</th><th>卡点原因</th></tr>
%(blocker_rows)s</table>
<h2>任务明细</h2>
<table><tr><th>阶段</th><th>任务</th><th>负责人</th><th>状态</th>
<th>进度</th><th>计划</th><th>依赖</th><th>卡点</th></tr>
%(detail_rows)s</table>
</div></body></html>""" % {
        "proj_name": esc(d["project"]["name"]),
        "proj_code": esc(d["project"].get("code") or "-"),
        "generated": d["generated"],
        "cards": cards,
        "stages_html": stages_html,
        "units_html": units_html,
        "unit_count": d["unit_count"],
        "blocker_rows": blocker_rows,
        "detail_rows": detail_rows,
        "blocker_count": d["blocker_count"],
    }
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


def export_excel(project, tasks, path=None):
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter
    except ImportError:
        return None

    d = build_report_data(project, tasks)
    if not path:
        # 与 HTML 报告保持一致：走 paths.REPORT_DIR（安装版在 %LOCALAPPDATA%，
        # 硬编码到程序目录在 Program Files 下会写不进去）
        out_dir = str(paths.REPORT_DIR)
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "报告_%s_%s.xlsx" % (
            project["name"], datetime.now().strftime("%Y%m%d_%H%M%S")))

    wb = Workbook()
    header_fill = PatternFill("solid", fgColor="2F80ED")
    header_font = Font(color="FFFFFF", bold=True)
    center = Alignment(horizontal="center", vertical="center")

    # 概览
    ws = wb.active
    ws.title = "概览"
    ws["A1"] = "%s · 进度报告" % project["name"]
    ws["A1"].font = Font(size=14, bold=True)
    overview = [
        ("项目编号", project.get("code") or "-"),
        ("生成时间", d["generated"]),
        ("总进度", "%d%%" % d["total_progress"]),
        ("任务总数", d["total"]),
        ("已完成", d["done"]),
        ("进行中", d["doing"]),
        ("受阻", d["blocked"]),
        ("返工中", d["rework"]),
        ("未开始", d["pending"]),
        ("卡点数量", d["blocker_count"]),
    ]
    for i, (k, v) in enumerate(overview, start=3):
        ws.cell(row=i, column=1, value=k).font = Font(bold=True)
        ws.cell(row=i, column=2, value=v)
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 26

    # 阶段进度
    ws.cell(row=14, column=1, value="阶段进度").font = Font(bold=True)
    for c, title in enumerate(["阶段", "任务数", "已完成", "进度%"], start=1):
        cell = ws.cell(row=15, column=c, value=title)
        cell.fill, cell.font, cell.alignment = header_fill, header_font, center
    for r, s in enumerate(d["stage_stats"], start=16):
        ws.cell(row=r, column=1, value=s["stage"])
        ws.cell(row=r, column=2, value=s["count"])
        ws.cell(row=r, column=3, value=s["done"])
        ws.cell(row=r, column=4, value=s["progress"])

    # 明细
    ws2 = wb.create_sheet("任务明细")
    headers = ["阶段", "任务", "负责人", "状态", "进度%", "计划开始", "计划结束",
               "依赖", "备注", "关联文件", "卡点"]
    for c, h in enumerate(headers, start=1):
        cell = ws2.cell(row=1, column=c, value=h)
        cell.fill, cell.font, cell.alignment = header_fill, header_font, center
    for r, t in enumerate(tasks, start=2):
        by_id = {x["id"]: x for x in tasks}
        dep_names = []
        for did in db.task_dep_ids(t):
            dp = by_id.get(did)
            if dp:
                dep_names.append(dp["name"])
        blockers = db.analyze_blockers(tasks).get(t["id"], [])
        vals = [t["stage"], t["name"], t["owner"],
                STATUS_NAMES.get(t["status"], t["status"]),
                int(t["progress"] or 0), t["plan_start"], t["plan_end"],
                ", ".join(dep_names), t["note"], links_text(t["file_link"]),
                "；".join(blockers)]
        for c, v in enumerate(vals, start=1):
            ws2.cell(row=r, column=c, value=v)
    widths = [12, 24, 10, 10, 8, 12, 12, 30, 24, 24, 30]
    for i, w in enumerate(widths, start=1):
        ws2.column_dimensions[get_column_letter(i)].width = w

    # 卡点
    ws3 = wb.create_sheet("卡点清单")
    for c, h in enumerate(["任务", "负责人", "阶段", "卡点原因"], start=1):
        cell = ws3.cell(row=1, column=c, value=h)
        cell.fill, cell.font, cell.alignment = header_fill, header_font, center
    row = 2
    for r in d["rows"]:
        if r["blockers"]:
            ws3.cell(row=row, column=1, value=r["name"])
            ws3.cell(row=row, column=2, value=r["owner"])
            ws3.cell(row=row, column=3, value=r["stage"])
            ws3.cell(row=row, column=4, value="；".join(r["blockers"]))
            row += 1
    if row == 2:
        ws3.cell(row=2, column=1, value="当前无卡点")
    for i, w in enumerate([24, 10, 12, 50], start=1):
        ws3.column_dimensions[get_column_letter(i)].width = w

    # 个体进度 + 个体矩阵（有个体的项目才有）
    if d["unit_rows"]:
        STATUS_SHORT = {"pending": "—", "doing": "进", "done": "完",
                        "blocked": "阻", "rework": "返"}
        units = db.list_units(project["id"])
        statuses = db.get_unit_statuses(project["id"])
        stats = {s["unit"]["id"]: s for s in db.unit_stats(project["id"], tasks, statuses)}
        ws4 = wb.create_sheet("个体进度")
        for c, h in enumerate(["SN", "名称", "进度%", "当前停在", "受阻/返工"],
                               start=1):
            cell = ws4.cell(row=1, column=c, value=h)
            cell.fill, cell.font, cell.alignment = header_fill, header_font, center
        for r, u in enumerate(units, start=2):
            s = stats.get(u["id"], {})
            vals = [u["sn"], u["name"], s.get("progress", 0),
                    s.get("current", "—"), s.get("issues") and "；".join(s["issues"]) or "—"]
            for c, v in enumerate(vals, start=1):
                ws4.cell(row=r, column=c, value=v)
        for i, w in enumerate([12, 12, 8, 22, 30], start=1):
            ws4.column_dimensions[get_column_letter(i)].width = w

        ws5 = wb.create_sheet("个体矩阵")
        headers = (["SN", "名称"]
                   + ["%d.%s" % (i + 1, t["name"]) for i, t in enumerate(tasks)]
                   + ["进度%"])
        for c, h in enumerate(headers, start=1):
            cell = ws5.cell(row=1, column=c, value=h)
            cell.fill, cell.font, cell.alignment = header_fill, header_font, center
        for r, u in enumerate(units, start=2):
            s = stats.get(u["id"], {})
            row_vals = [u["sn"], u["name"]]
            for t in tasks:
                st = statuses.get(u["id"], {}).get(t["id"], "pending")
                row_vals.append(STATUS_SHORT.get(st, "—"))
            row_vals.append(s.get("progress", 0))
            for c, v in enumerate(row_vals, start=1):
                cell = ws5.cell(row=r, column=c, value=v)
                if c > 2:
                    cell.alignment = center
        ws5.column_dimensions["A"].width = 12
        ws5.column_dimensions["B"].width = 12
        for i in range(len(tasks)):
            ws5.column_dimensions[get_column_letter(3 + i)].width = 14
        ws5.column_dimensions[get_column_letter(3 + len(tasks))].width = 8

    wb.save(path)
    return path


def open_file(path):
    if path and os.path.exists(path):
        webbrowser.open("file://" + os.path.abspath(path).replace("\\", "/"))
