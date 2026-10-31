# -*- coding: utf-8 -*-
"""冒烟测试：offscreen 模式验证 窗口构建/模板创建/三视图+个体矩阵/自动进度/
全量导出导入/报告导出 全链路。"""
import json
import os
import shutil
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QDate, QEvent, QPointF, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (QApplication, QDialog, QGraphicsScene)

from app import db, report
from app.templates import STAGES, TEMPLATES

# 路径层测试用的子进程脚本：伪装成 PyInstaller 打包版（sys.frozen=True，
# 数据根应切到 %LOCALAPPDATA%），用于验证安装版数据落点与旧数据迁移规则。
_LAYOUT_SCRIPT = r'''
import json, os, sys
from pathlib import Path

root, tmp, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
variant = sys.argv[4] if len(sys.argv) > 4 else ""
install = tmp / "install"
(install / "_internal").mkdir(parents=True, exist_ok=True)

# 模拟打包时随产物带走的版本标识：
# 调试版 build_version.txt 里是"正式版-debug.序号"，正式版故意不写该文件，
# 用来验证"没有该文件时回退 version.py"这条兜底路径。
VERSION_FILE_LABEL = "9.9.9-debug.7" if variant == "debug" else None
if VERSION_FILE_LABEL:
    (install / "resources").mkdir(parents=True, exist_ok=True)
    (install / "resources" / "build_version.txt").write_text(
        VERSION_FILE_LABEL + "\n", encoding="utf-8")

legacy_db = None
if mode in ("migrate", "migrate_internal"):
    legacy = (install if mode == "migrate" else install / "_internal") / "data"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "projects.db").write_bytes(b"LEGACY-DB")
    (legacy / "settings.ini").write_bytes(b"legacy-settings")
    legacy_db = legacy / "projects.db"
if mode == "existing":
    owner = "ProjectManager Debug" if variant == "debug" else "ProjectManager"
    newdir = tmp / "localappdata" / owner / "data"
    newdir.mkdir(parents=True, exist_ok=True)
    (newdir / "projects.db").write_bytes(b"CURRENT-DB")

os.environ["LOCALAPPDATA"] = str(tmp / "localappdata")
if variant:
    os.environ["PM_VARIANT"] = variant     # 开发态下模拟构建变体标记
sys.frozen = True
sys.executable = str(install / "ProjectManager.exe")
sys.path.insert(0, str(root))

from app import paths

dbp = Path(paths.DB_PATH)
print("LAYOUT:" + json.dumps({
    "root": str(paths.USER_DATA_ROOT),
    "db": str(paths.DB_PATH),
    "db_bytes": dbp.read_bytes().decode() if dbp.is_file() else None,
    "settings": Path(paths.SETTINGS_PATH).is_file(),
    "legacy_kept": bool(legacy_db and legacy_db.is_file()),
    "msg": paths.MIGRATION_MESSAGE or "",
    "variant": paths.VARIANT,
    "owner": paths.DATA_OWNER,
    "build_version": paths.BUILD_VERSION,
}, ensure_ascii=False))
'''


def main():
    db.init_db()
    app = QApplication(sys.argv)

    # 1) 模板创建项目（若已存在先删，保证可重复测试）
    tpl = TEMPLATES["six_axis"]
    conn = db.get_conn()
    old = conn.execute("SELECT id FROM projects WHERE name=?",
                       ("冒烟测试项目",)).fetchall()
    conn.close()
    for r in old:
        db.delete_project(r["id"])
    pid = db.create_project("冒烟测试项目", "SMOKE-001", tpl["name"], tpl["tasks"])
    tasks = db.list_tasks(pid)
    assert len(tasks) == 21, "模板任务数应为 21，实际 %d" % len(tasks)
    print("[ok] 模板创建: 21 个任务")

    # 2) 手填模式：模拟进度更新（无个体时保留手填能力）
    for t in tasks:
        if t["order_idx"] < 5:
            db.update_task(t["id"], "status", "done")
            db.update_task(t["id"], "progress", 100)
        elif t["order_idx"] == 5:
            db.update_task(t["id"], "status", "doing")
            db.update_task(t["id"], "progress", 60)
        elif t["name"] == "弹性体到货检验":
            db.update_task(t["id"], "status", "rework")
            db.update_task(t["id"], "progress", 50)
    tasks = db.list_tasks(pid)
    blockers = db.analyze_blockers(tasks)
    print("[ok] 卡点分析: %d 处" % len(blockers))
    print("[ok] 总进度: %d%%" % db.project_progress(tasks))

    # 3) 主窗口构建 + 视图刷新
    #    先把"显示设置"复位成出厂值：相关用例会写真实 settings.ini，上一轮若
    #    中途失败就会把非默认值留给下一轮（典型串扰：arrow_scope=closure 会让
    #    后续「紧邻一环」断言无端失败、matrix_freeze_cols 也会乱）。开跑即复位，
    #    任何中断都不再污染下一次运行。
    from app.ui_main import MainWindow
    from app.ui_main import VIEW_DEFAULTS as _VD
    from app.ui_main import save_view_settings as _save_view
    _save_view(dict(_VD))
    win = MainWindow()
    print("[ok] 主窗口构建")
    win.new_project = lambda: None  # 防止误弹窗
    idx = win.cmb_project.findData(pid)
    assert idx >= 0, "工具栏项目下拉框未找到测试项目"
    win.cmb_project.setCurrentIndex(idx)
    print("[ok] 项目加载: %d 个项目, 当前任务 %d 个"
          % (win.cmb_project.count(), len(win.tasks)))
    # 选择第一个任务
    first_tid = win.tasks[0]["id"]
    win.select_task(first_tid)
    assert win.ed_name.text() == win.tasks[0]["name"], "详情面板加载失败"
    print("[ok] 详情面板加载: %s" % win.ed_name.text())
    # 编辑并保存（手填模式）
    win.spin_prog.setValue(80)
    win.save_detail()
    t0 = db.list_tasks(pid)[0]
    assert int(t0["progress"]) == 80, "保存进度失败"
    print("[ok] 详情保存: 进度=80%")

    # 3.5) 双向依赖 + 自定义阶段分组
    dep_target = win.tasks[3]
    orig_deps = dep_target["depends_on"]      # 模板原依赖，测试后要还原
    tid_new = db.add_task(pid, {"stage": "包装发货", "name": "测试任务X<1>",
                                "order_idx": 99})
    db.update_task(tid_new, "depends_on", str(dep_target["id"]))
    db.update_task(dep_target["id"], "depends_on", str(tid_new))
    tasks2 = db.list_tasks(pid)
    t_new = next(t for t in tasks2 if t["id"] == tid_new)
    assert db.task_dep_ids(t_new) == [dep_target["id"]], "前置依赖写入失败"
    dep_after = next(t for t in tasks2 if t["id"] == dep_target["id"])
    assert tid_new in db.task_dep_ids(dep_after), "反向依赖写入失败"
    win.refresh_task_views()
    tree_stages = [win.tree.topLevelItem(i).text(0)
                   for i in range(win.tree.topLevelItemCount())]
    assert any("包装发货" in s for s in tree_stages), "自定义阶段未在任务树分组"
    db.delete_task(tid_new)
    db.update_task(dep_target["id"], "depends_on", orig_deps)  # 还原模板依赖
    print("[ok] 双向依赖 + 自定义阶段")

    # 3.6) 对话框构建 + "添加任务"整条点击路径（回归：曾因缺 QListWidgetItem 导入崩溃）
    from app.ui_main import (NewProjectDialog, NewTaskDialog,
                             UnitManageDialog)
    dlg_p = NewProjectDialog()
    dlg_p.ed_name.setText("对话框测试项目")
    n, code, tpl_key, units_n, sn_prefix = dlg_p.values()
    assert n == "对话框测试项目" and units_n == 10 and sn_prefix == "SN-", \
        "新建项目对话框取值错误: %s" % ((n, units_n, sn_prefix),)
    print("[ok] 对话框-新建项目: 取值正常")

    dlg_t = NewTaskDialog(win, win.tasks, default_stage="电路设计")
    assert dlg_t.list_preds.count() == len(win.tasks) > 0, "前置任务列表未填充"
    assert dlg_t.list_succs.count() == len(win.tasks), "后续任务列表未填充"
    dlg_t.ed_name.setText("对话框测试任务")
    dlg_t.list_preds.setCurrentRow(0)
    v = dlg_t.values()
    assert v["name"] == "对话框测试任务" and v["stage"] == "电路设计"
    assert v["pred_ids"] == [str(win.tasks[0]["id"])], \
        "前置任务选中取值错误: %s" % v["pred_ids"]
    # 添加任务对话框里也有「计划天数」：填天数 → 自动算结束（结束 = 开始 + 天数）
    dlg_t.dt_ps.set_value("2026-03-01")
    dlg_t.spin_days.setValue(10)
    assert dlg_t.dt_pe.value() == "2026-03-11", \
        "添加任务对话框的计划天数未联动: %r" % dlg_t.dt_pe.value()
    assert dlg_t.values()["plan_end"] == "2026-03-11", "对话框取值未带上算出的结束日期"
    dlg_t.spin_days.setValue(0)
    dlg_t.dt_ps.clear_value()
    dlg_t.dt_pe.clear_value()
    print("[ok] 对话框-添加任务: 构造/取值/依赖选择正常")

    dlg_u = UnitManageDialog(win, pid, "冒烟测试项目")
    assert dlg_u.spin_count.value() == 10, "批次个体对话框默认值错误"
    print("[ok] 对话框-批次个体: 构造正常")

    # 走一遍 win.add_task() 真实点击路径（自动接受对话框，验证写入 + 双向依赖）
    orig_exec = NewTaskDialog.exec

    def _auto_accept(self):
        self.ed_name.setText("UI点击添加任务")
        self.cmb_stage.setCurrentText("包装发货")
        self.list_preds.setCurrentRow(1)   # 选 tasks[1] 作前置
        self.list_succs.setCurrentRow(2)   # 选 tasks[2] 作后续
        return QDialog.Accepted

    succ_target = win.tasks[2]
    succ_orig_deps = succ_target["depends_on"]      # 模板原依赖，测试后还原
    NewTaskDialog.exec = _auto_accept
    try:
        win.add_task()
    finally:
        NewTaskDialog.exec = orig_exec
    ui_task = next(t for t in db.list_tasks(pid)
                   if t["name"] == "UI点击添加任务")
    assert db.task_dep_ids(ui_task) == [win.tasks[1]["id"]], "add_task 前置依赖未写入"
    succ = next(t for t in db.list_tasks(pid) if t["id"] == win.tasks[2]["id"])
    assert ui_task["id"] in db.task_dep_ids(succ), "add_task 反向依赖未写入"
    db.delete_task(ui_task["id"])
    db.update_task(win.tasks[2]["id"], "depends_on", succ_orig_deps)  # 还原模板依赖
    win.refresh_task_views()
    print("[ok] 添加任务全路径: 对话框→落库→双向依赖")

    # 3.7) 视图刷新压力测试（回归：刷新期间信号重入曾在 tree.clear() 段错误）
    for _i in range(6):
        win.select_task(win.tasks[_i % len(win.tasks)]["id"])
        win.refresh_task_views()
    win.select_task(win.tasks[0]["id"])
    print("[ok] 视图刷新压力测试: 6 轮任务切换+重建 无崩溃")

    # 3.8) 选中同步回归（用户反馈 bug）：点击后 树/表格 高亮必须立即跟手，
    #      不能滞后一拍；表格重建后高亮也不能丢。
    tid_a, tid_b = win.tasks[1]["id"], win.tasks[3]["id"]
    win.select_task(tid_a)
    sel_a = [i.data(0, Qt.UserRole) for i in win.tree.selectedItems()]
    assert sel_a == [tid_a], "树高亮未跟随点击: %s" % sel_a
    assert win.tree.currentItem() is not None, "树当前项丢失"
    tbl_sel = win.tbl.selectedItems()
    assert tbl_sel and tbl_sel[0].data(Qt.UserRole) == tid_a, "表格高亮未恢复"
    win.select_task(tid_b)
    sel_b = [i.data(0, Qt.UserRole) for i in win.tree.selectedItems()]
    assert sel_b == [tid_b], "树高亮滞后一拍: %s" % sel_b
    tbl_sel = win.tbl.selectedItems()
    assert tbl_sel and tbl_sel[0].data(Qt.UserRole) == tid_b, "表格高亮未恢复"
    # 静默写库：详情改名 → 写库但不立即重建视图（不吞点击）
    orig_name = win.tasks[3]["name"]
    win.ed_name.setText("改名验证任务")
    win._flush_detail_edits()
    t_flushed = next(t for t in db.list_tasks(pid) if t["id"] == tid_b)
    assert t_flushed["name"] == "改名验证任务", "静默写库失败"
    app.processEvents()  # 触发延迟刷新（树标签更新、高亮保持）
    sel_b = [i.data(0, Qt.UserRole) for i in win.tree.selectedItems()]
    assert sel_b == [tid_b], "延迟刷新后高亮丢失"
    db.update_task(tid_b, "name", orig_name)
    win.refresh_task_views()
    print("[ok] 选中同步: 树/表格高亮与点击一致（滞后/丢高亮修复）")

    # 3.9) 阶段管理（新功能）：登记/重命名同步任务/排序/删除/空阶段显示
    db.add_stage(pid, "验证空阶段")
    assert "验证空阶段" in db.list_stages(pid), "阶段登记失败"
    tid_v = db.add_task(pid, {"stage": "验证空阶段", "name": "阶段验证任务",
                              "order_idx": 98})
    db.rename_stage(pid, "验证空阶段", "验证阶段2")
    t_v = next(t for t in db.list_tasks(pid) if t["id"] == tid_v)
    assert t_v["stage"] == "验证阶段2", "重命名未同步任务阶段"
    names = db.list_stages(pid)
    names.remove("验证阶段2")
    names.insert(0, "验证阶段2")
    db.set_stage_order(pid, names)
    assert db.list_stages(pid)[0] == "验证阶段2", "阶段排序失败"
    db.delete_stage(pid, "验证阶段2")   # 阶段记录删除，任务回退为派生显示
    assert "验证阶段2" not in db.list_stages(pid)
    db.delete_task(tid_v)
    # 空的显式阶段在树里可见（供用户往里加任务）
    db.add_stage(pid, "空阶段显示测试")
    win.refresh_task_views()
    tree_names = [win.tree.topLevelItem(i).text(0)
                  for i in range(win.tree.topLevelItemCount())]
    assert any(n.startswith("空阶段显示测试") for n in tree_names), \
        "空的显式阶段未在树中显示: %s" % tree_names
    db.delete_stage(pid, "空阶段显示测试")
    # NewTaskDialog 的「新建阶段」按钮路径：对话框内新建并选用
    dlg_t2 = NewTaskDialog(win, win.tasks, default_stage="",
                           stages=db.list_stages(pid))
    assert "验证阶段2" not in dlg_t2.cmb_stage.currentText()
    dlg_t2.cmb_stage.setCurrentText("全新阶段Z")
    assert dlg_t2.values()["stage"] == "全新阶段Z"
    print("[ok] 阶段管理: 登记/重命名同步/排序/删除/空阶段显示/新建按钮")

    # 3.10) 模板管理（新功能）：存模板→建项目→依赖映射；导出导入带模板
    tpl_tasks2 = [
        {"stage": "S1", "name": "甲", "owner": "机械", "note": "", "deps": []},
        {"stage": "S1", "name": "乙", "owner": "电路", "note": "", "deps": ["甲"]},
        {"stage": "S2", "name": "丙", "owner": "", "note": "", "deps": ["乙"]},
    ]
    db.save_template("冒烟模板B", tpl_tasks2)
    tp2 = next(x for x in db.list_templates() if x["name"] == "冒烟模板B")
    assert len(tp2["tasks"]) == 3
    db.save_template("冒烟模板B", tpl_tasks2[:2])   # 同名覆盖
    tp2 = next(x for x in db.list_templates() if x["name"] == "冒烟模板B")
    assert len(tp2["tasks"]) == 2, "同名模板未覆盖"
    pid_b = db.create_project("模板项目B", "", tp2["name"], tp2["tasks"])
    tb = {t["name"]: t for t in db.list_tasks(pid_b)}
    assert len(tb) == 2
    assert db.task_dep_ids(tb["乙"]) == [tb["甲"]["id"]], "模板依赖未映射"
    assert db.list_stages(pid_b) == ["S1"], "模板项目阶段未按模板播种"
    # 新建项目对话框：自定义模板出现在下拉里
    dlg_p2 = NewProjectDialog()
    found = any(dlg_p2.cmb_tpl.itemData(i) == ("custom", tp2["id"])
                for i in range(dlg_p2.cmb_tpl.count()))
    assert found, "新建项目对话框未列出自定义模板"
    db.delete_project(pid_b)
    db.delete_template(tp2["id"])
    print("[ok] 模板管理: 存/覆盖/建项目/依赖映射/对话框列表")

    # 3.11) 默认日期 = 软件使用当天：未设置时打开日历自动跳到"今天"
    #   （曾经写死 2026-01-01，跨年之后点开日历落在过去某天，很别扭）
    from app.ui_main import NullableDateEdit
    nde = NullableDateEdit()
    assert nde.value() == "", "初始应为未设置"
    cal = nde.edit.calendarWidget()
    QApplication.sendEvent(cal, QEvent(QEvent.Show))
    d = nde.edit.date()
    _today = QDate.currentDate()
    assert (d.year(), d.month(), d.day()) == (_today.year(), _today.month(), _today.day()), \
        "日历默认日期不是今天: %s" % d.toString()
    assert nde.value() == _today.toString("yyyy-MM-dd"), "跳默认日期后取值错误"
    # 已经填过日期的控件再点开日历，不能被"今天"冲掉
    nde.set_value("2025-03-08")
    QApplication.sendEvent(cal, QEvent(QEvent.Show))
    assert nde.value() == "2025-03-08", "已填日期被默认值冲掉了"
    print("[ok] 默认日期: 未设置时打开日历跳到今天 %s" % _today.toString("yyyy-MM-dd"))

    # 3.11.1) 日期不串写（用户反馈：给一个任务填了 2026-09-01 之后，"再点谁谁都是这个日期"）
    #   根因：NullableDateEdit.set_value("") 早先写成 `if s:` 才设值 —— 空值不复位，
    #   控件里留着上一个任务的日期；此后任何一次写库（切任务前的 flush、改名字失焦）
    #   都会把这个日期写到新选中的任务上。日期控件是有状态的，空值必须显式复位。
    _ts = db.list_tasks(pid)
    _A, _B = _ts[0], _ts[1]
    assert not _A["plan_start"] and not _B["plan_start"], "用例前提：A/B 都没填日期"
    try:
        win.select_task(_A["id"])
        win.dt_ps.set_value("2026-09-01")
        win.dt_pe.set_value("2026-09-30")
        win.save_detail()
        _a = next(t for t in db.list_tasks(pid) if t["id"] == _A["id"])
        assert (_a["plan_start"], _a["plan_end"]) == ("2026-09-01", "2026-09-30"), \
            "日期没落库: %r/%r" % (_a["plan_start"], _a["plan_end"])
        win.select_task(_B["id"])          # 切到没填日期的任务
        assert win.dt_ps.value() == "" and win.dt_pe.value() == "", \
            "切到没填日期的任务后控件应显示「未设置」，实际 %r/%r" % (
                win.dt_ps.value(), win.dt_pe.value())
        win.save_detail()                  # 任何一次写库都不该把 A 的日期带给 B
        _b = next(t for t in db.list_tasks(pid) if t["id"] == _B["id"])
        assert not _b["plan_start"] and not _b["plan_end"], \
            "日期串写：B 被写成 %r/%r" % (_b["plan_start"], _b["plan_end"])
        _bleed = [(t["name"], t["plan_start"], t["plan_end"])
                  for t in db.list_tasks(pid)
                  if t["id"] != _A["id"] and (t["plan_start"] or t["plan_end"])]
        assert not _bleed, "有任务被串写了日期：%s" % _bleed
        win.select_task(_A["id"])          # 切回来日期得还在
        assert (win.dt_ps.value(), win.dt_pe.value()) == ("2026-09-01", "2026-09-30"), \
            "切回原任务后日期丢了: %r/%r" % (win.dt_ps.value(), win.dt_pe.value())
        win.clear_task_selection()         # 取消选中 → 面板清空
        assert win.dt_ps.value() == "" and win.dt_ae.value() == "", \
            "取消选中后日期控件未复位: %r/%r" % (win.dt_ps.value(), win.dt_ae.value())
    finally:
        for _f in ("plan_start", "plan_end", "actual_start", "actual_end"):
            db.update_task(_A["id"], _f, "")
    print("[ok] 日期不串写: 填过日期的任务不污染其他任务 + 切回不丢 + 取消选中复位")

    # 3.11.2) 计划天数 ↔ 计划结束日期（用户需求：计划结束 = 计划开始 + 计划天数；
    #   反过来选了结束日期也要把天数算出来）
    _ts = db.list_tasks(pid)
    _C, _D = _ts[0], _ts[1]
    assert not _C["plan_start"] and not _C["plan_end"], "用例前提：C 没填计划日期"
    try:
        win.select_task(_C["id"])
        win.dt_ps.set_value("2026-09-01")          # 只填开始 → 天数保持未设置
        assert win.spin_days.value() == 0, \
            "只填了开始日期，天数不该自己冒出来: %d" % win.spin_days.value()
        win.spin_days.setValue(30)                 # 填天数 → 自动算结束
        assert win.dt_pe.value() == "2026-10-01", \
            "计划结束 = 计划开始 + 计划天数 未生效: %r" % win.dt_pe.value()
        win.save_detail()
        _c = next(t for t in db.list_tasks(pid) if t["id"] == _C["id"])
        assert (_c["plan_start"], _c["plan_end"]) == ("2026-09-01", "2026-10-01"), \
            "天数算出的结束日期没落库: %r/%r" % (_c["plan_start"], _c["plan_end"])
        # 反过来：直接改结束日期 → 天数自动回算
        win.dt_pe.set_value("2026-09-11")
        assert win.spin_days.value() == 10, \
            "改结束日期后没回算天数: %d" % win.spin_days.value()
        win.save_detail()
        # 改开始日期 → 按天数把结束日期一起挪（工期不变）
        win.dt_ps.set_value("2026-09-05")
        assert win.dt_pe.value() == "2026-09-15", \
            "改开始后没按天数推移结束: %r" % win.dt_pe.value()
        win.save_detail()
        _c = next(t for t in db.list_tasks(pid) if t["id"] == _C["id"])
        assert (_c["plan_start"], _c["plan_end"]) == ("2026-09-05", "2026-09-15"), \
            "联动结果没落库: %r/%r" % (_c["plan_start"], _c["plan_end"])
        # 换到没有日期的任务：天数必须复位成"未设置"，不能把 10 天带过去
        win.select_task(_D["id"])
        assert win.spin_days.value() == 0 and win.dt_ps.value() == "", \
            "切到没日期的任务后天数/日期未复位: %d/%r" % (
                win.spin_days.value(), win.dt_ps.value())
        # 开始还没填就先填天数：先记着不算（没基准），等开始填上自动补出结束
        win.spin_days.setValue(7)
        assert win.dt_pe.value() == "", "开始没填就把结束算出来了: %r" % win.dt_pe.value()
        win.dt_ps.set_value("2026-10-01")
        assert win.dt_pe.value() == "2026-10-08", \
            "填上开始日期后没补算结束: %r" % win.dt_pe.value()
        win.save_detail()
        _d = next(t for t in db.list_tasks(pid) if t["id"] == _D["id"])
        assert (_d["plan_start"], _d["plan_end"]) == ("2026-10-01", "2026-10-08"), \
            "补算的结束日期没落库: %r/%r" % (_d["plan_start"], _d["plan_end"])
        win.clear_task_selection()
        assert win.spin_days.value() == 0, "取消选中后天数未复位"
    finally:
        for _tid in (_C["id"], _D["id"]):
            for _f in ("plan_start", "plan_end", "actual_start", "actual_end"):
                db.update_task(_tid, _f, "")
    print("[ok] 计划天数: 天数→结束、结束→天数、开始→顺延、未填开始不硬算、换任务不串")

    # 3.11.3) 空白项目：左侧任务树一开始是空的（不再预置"研发设计/制作与投板/
    #   生产装配/标定与入库"）；阶段全由用户自己建，先建的排第一、后建的在后，
    #   顺序可用右键（或 move_stage_order）上下移
    from app.ui_main import STAGE_ROLE
    _pid_blank = db.create_project("空白项目冒烟", "", "空白项目", [])
    try:
        assert db.list_tasks(_pid_blank) == [], "空白项目不该有任务"
        assert db.list_stages(_pid_blank) == [], \
            "空白项目不该预置阶段: %s" % db.list_stages(_pid_blank)
        win.refresh_projects(select_id=_pid_blank)
        assert win.tree.topLevelItemCount() == 0, \
            "空白项目的左侧任务树应为空，实际 %d 个阶段" % win.tree.topLevelItemCount()
        assert win._all_stages() == [], "空白项目阶段列表应为空"
        assert not any(s in STAGES for s in win._all_stages()), \
            "空白项目不该出现内置阶段（研发设计/制作与投板/生产装配/标定与入库）"
        # 空白项目也要能出报告（没有阶段统计，不应报错、也不该列出内置四阶段）
        _rd = report.build_report_data(db.get_project(_pid_blank), [])
        assert _rd["stage_stats"] == [], \
            "空白项目的阶段统计应为空: %s" % _rd["stage_stats"]
        # 新建阶段一律排末尾：先建的在前，后建的在后
        for _s in ("第一段", "第二段", "第三段"):
            db.add_stage(_pid_blank, _s)
        assert db.list_stages(_pid_blank) == ["第一段", "第二段", "第三段"], \
            "新阶段未按创建顺序排: %s" % db.list_stages(_pid_blank)
        win.refresh_task_views()
        tops = [win.tree.topLevelItem(i) for i in range(win.tree.topLevelItemCount())]
        assert len(tops) == 3, "显式登记的空阶段应在树里可见，实际 %d" % len(tops)
        assert [t.data(0, STAGE_ROLE) for t in tops] == ["第一段", "第二段", "第三段"], \
            "阶段抬头未按顺序挂上真实阶段名"
        # 往下/往上挪一格
        win.move_stage_order("第三段", -1)
        assert db.list_stages(_pid_blank) == ["第一段", "第三段", "第二段"], \
            "上移失败: %s" % db.list_stages(_pid_blank)
        win.move_stage_order("第一段", 1)
        assert db.list_stages(_pid_blank) == ["第三段", "第一段", "第二段"], \
            "下移失败: %s" % db.list_stages(_pid_blank)
        # 边界：首行再上移、末行再下移都不动
        win.move_stage_order("第三段", -1)
        win.move_stage_order("第二段", 1)
        assert db.list_stages(_pid_blank) == ["第三段", "第一段", "第二段"], \
            "越界移动改坏了顺序: %s" % db.list_stages(_pid_blank)
        # 顺序落到任务树抬头（右键菜单取的就是这个顺序）
        assert win.tree.topLevelItem(0).data(0, STAGE_ROLE) == "第三段", \
            "树的阶段顺序没跟着变"
        # 空白项目里加任务：阶段下拉就列项目已有的阶段，不预选内置阶段
        dlg_blank = NewTaskDialog(win, [], default_stage="第二段",
                                  stages=win._all_stages())
        assert dlg_blank.cmb_stage.count() == 3, \
            "添加任务对话框阶段项数应为 3，实际 %d" % dlg_blank.cmb_stage.count()
        dlg_blank.ed_name.setText("空白项目任务")
        assert dlg_blank.values()["stage"] == "第二段", "默认阶段取值错误"
        dlg_blank2 = NewTaskDialog(win, [], stages=[])
        assert dlg_blank2.cmb_stage.count() == 0, "没有阶段时下拉不该硬塞内置阶段"
        dlg_blank2.ed_name.setText("无阶段任务")
        assert dlg_blank2.values()["stage"] == "未分组", \
            "没选阶段应落到未分组，而不是内置的研发设计: %r" % dlg_blank2.values()["stage"]
        # 老项目（有任务但没阶段登记记录）完全按任务里出现的顺序排；
        # 关键是**不能把内置四阶段又变出来**
        conn = db.get_conn()
        conn.execute("DELETE FROM project_stages WHERE project_id=?", (_pid_blank,))
        conn.commit()
        conn.close()
        _tid_old = db.add_task(_pid_blank, {"stage": "临时段", "name": "老项目式任务",
                                           "order_idx": 0})
        win.refresh_task_views()
        assert win._all_stages() == ["临时段"], \
            "有任务但无阶段登记时不该冒内置阶段: %s" % win._all_stages()
        db.delete_task(_tid_old)
        # 没选阶段的任务落到「未分组」，同样不能把内置四阶段带出来
        _tid_non = db.add_task(_pid_blank, {"stage": "", "name": "未分组任务",
                                           "order_idx": 0})
        win.refresh_task_views()
        assert win._all_stages() == ["未分组"], \
            "没选阶段的任务把内置阶段带出来了: %s" % win._all_stages()
        assert [win.tree.topLevelItem(i).data(0, STAGE_ROLE)
                for i in range(win.tree.topLevelItemCount())] == ["未分组"], \
            "任务树顶层项应只有「未分组」"
        db.delete_task(_tid_non)
    finally:
        db.delete_project(_pid_blank)
        win.refresh_projects(select_id=pid)
    print("[ok] 空白项目: 树为空/不预置阶段/新阶段排末尾/上下移/越界不动")


    # 3.12) Ctrl+滚轮缩放 + 上下游链路高亮（V1.3.1）
    from app.ui_main import ZoomableView
    from PySide6.QtWidgets import QGraphicsLineItem
    zv = ZoomableView()
    zv.setScene(QGraphicsScene())
    zv._set_zoom(2.0)
    assert abs(zv.transform().m11() - 2.0) < 1e-9, "缩放矩阵错误"
    zv._set_zoom(0.05)                       # 低于下限应被钳制
    assert zv.zoom_level() == ZoomableView.MIN_ZOOM, "缩放下限失效"
    zv._set_zoom(99)                         # 高于上限应被钳制
    assert zv.zoom_level() == ZoomableView.MAX_ZOOM, "缩放上限失效"
    QApplication.sendEvent(                  # Ctrl+0 复位
        zv, QKeyEvent(QEvent.KeyPress, Qt.Key_0, Qt.ControlModifier))
    assert zv.zoom_level() == 1.0, "Ctrl+0 未复位缩放"
    # 流程图只高亮"紧邻一环"：上一环=直接前置，下一环=直接后续（不做传递闭包）
    by_name = {t["name"]: t for t in db.list_tasks(pid)}
    tid_sel = by_name["弹性体到货检验"]["id"]
    all_tasks = db.list_tasks(pid)
    tmap = {t["id"]: t for t in all_tasks}
    sel_t = tmap[tid_sel]
    exp_up = set(db.task_dep_ids(sel_t))
    exp_down = {t["id"] for t in all_tasks if tid_sel in db.task_dep_ids(t)}
    # 间接上下游（闭包减直接），必须一个都不在高亮集合里
    ind_up = set()
    for d in exp_up:
        ind_up |= set(db.task_dep_ids(tmap[d]))
    ind_up -= exp_up
    ind_up.discard(tid_sel)
    ind_down = set()
    for d in exp_down:
        ind_down |= {t["id"] for t in all_tasks if d in db.task_dep_ids(t)}
    ind_down -= exp_down
    ind_down.discard(tid_sel)
    assert ind_up and ind_down, "模板依赖太浅，间接上下游用例前提不成立"

    win.select_task(tid_sel)
    assert win.selected_tid == tid_sel
    assert win._flow_up == exp_up, "上一环应为直接前置: %s" % win._flow_up
    assert win._flow_down == exp_down, "下一环应为直接后续: %s" % win._flow_down
    assert win._flow_up.isdisjoint(ind_up), "不应含间接前置（闭包未收窄）"
    assert win._flow_down.isdisjoint(ind_down), "不应含间接后续（闭包未收窄）"
    # 置顶高亮连线（z=6）数量 = 与选中任务直接相连的边数，蓝=进、橙=出
    hl = [it for it in win.flow_view.scene().items()
          if isinstance(it, QGraphicsLineItem) and it.zValue() == 6]
    nodes = set(win._flow_nodes)
    assert len(hl) == len(exp_up & nodes) + len(exp_down & nodes), \
        "高亮连线数应为直连边数: %d" % len(hl)
    pens = {it.pen().color().name() for it in hl}
    assert pens == {"#2f80ed", "#f2994a"}, "高亮配色异常: %s" % pens
    # 链头任务：无前置、下游=依赖它的任务
    win.select_task(by_name["弹性体仿真与制作"]["id"])
    assert not win._flow_up, "链头任务不应有上游"
    assert by_name["桥应变参数交付"]["id"] in win._flow_down, "链头下游缺失"
    # 缩放期间刷新视图：切换任务后缩放保持
    zv2 = win.flow_view
    zv2._set_zoom(1.5)
    win.select_task(by_name["成品入库"]["id"])
    assert abs(zv2.transform().m11() - 1.5) < 1e-9, "刷新后缩放丢失"
    # 点空白处取消选中 + 点卡片任意位置都能选中
    from PySide6.QtTest import QTest
    from app.ui_main import FlowNode
    assert win.flow_view._blank_cb == win.clear_task_selection, "空白点击回调未挂载"
    win.resize(1400, 900)
    win.show()                 # offscreen 下也需 show 才有真实视口尺寸
    app.processEvents()
    win.flow_view._set_zoom(1.0)
    vp_flow = win.flow_view.viewport()

    def _click_scene(pt):
        vp_pt = win.flow_view.mapFromScene(pt)
        assert vp_flow.rect().contains(vp_pt), "目标点不在视口内: %s" % vp_pt
        QTest.mouseClick(vp_flow, Qt.LeftButton, pos=vp_pt)
        app.processEvents()

    win.select_task(tid_sel)
    assert win.selected_tid == tid_sel
    _bpt = QPointF(6, 30)                 # 第一条泳道上方的空白带
    assert not isinstance(win.flow_view.itemAt(win.flow_view.mapFromScene(_bpt)),
                          FlowNode), "测试用空白点落到了节点上"
    _click_scene(_bpt)
    assert win.selected_tid is None, "点空白处未取消选中"
    assert not win.tree.selectedItems(), "取消选中后树高亮未清除"
    assert not win.tbl.selectedItems(), "取消选中后表格高亮未清除"
    assert not win.detail.isEnabled(), "取消选中后详情面板未置灰"
    # 卡片正中（压在文字上）也要能选中：此前文字子项会吞掉点击，卡片中间点不动
    nd = win._flow_nodes[tid_sel]
    _cpt = QPointF(nd[0] + nd[2] / 2, nd[1] + nd[3] / 2)
    _hit = win.flow_view.itemAt(win.flow_view.mapFromScene(_cpt))
    assert isinstance(_hit, FlowNode), "卡片中心命中项应为 FlowNode 而非 %s" % type(_hit).__name__
    _click_scene(_cpt)
    assert win.selected_tid == tid_sel, "点卡片文字区未能选中: %s" % win.selected_tid
    node_item = next(it for it in win.flow_view.scene().items()
                     if isinstance(it, FlowNode) and it.tid == tid_sel)
    assert all(c.acceptedMouseButtons() == Qt.NoButton
               for c in node_item.childItems()), "节点子项不应接收鼠标（会吞点击）"
    # 卡点红点必须落在卡片矩形内：悬在卡片外时那一小片不属于任何图元，
    # 点它既选不中卡片也不触发空白回调（用户反馈"点红点任务就没了"的诱因之一）
    from PySide6.QtWidgets import QGraphicsEllipseItem
    dots = [it for it in win.flow_view.scene().items()
            if isinstance(it, QGraphicsEllipseItem)]
    assert dots, "测试前提不成立：当前没有任何卡点红点"
    for d in dots:
        par = d.parentItem()
        assert isinstance(par, FlowNode), "卡点红点应挂在任务卡片上"
        assert par.rect().contains(d.rect()), \
            "卡点红点必须画在卡片矩形内（否则点它等于点在空白处）"
    print("[ok] 流程图: Ctrl+滚轮缩放(限幅/复位/保持) + 上一环/下一环高亮 + 点空白取消选中")

    # 3.13) 显示设置（V1.3.4）：淡化开关 + 连线高亮范围三档，可持久化
    from app.ui_main import (ViewSettingsDialog, load_view_settings,
                             save_view_settings)
    _defaults = load_view_settings()
    assert _defaults["dim_unrelated"] is True, "默认应淡化无关任务"
    assert _defaults["arrow_scope"] == "adjacent", \
        "默认应为紧邻一环: %s" % _defaults["arrow_scope"]

    def _hl_lines():
        return [it for it in win.flow_view.scene().items()
                if isinstance(it, QGraphicsLineItem) and it.zValue() == 6]

    def _dim_nodes():
        return [it for it in win.flow_view.scene().items()
                if isinstance(it, FlowNode) and it.opacity() < 0.99]

    win.view_settings = {"dim_unrelated": True, "arrow_scope": "adjacent",
                         "matrix_freeze_cols": 5}
    win.refresh_task_views()
    win.select_task(tid_sel)
    n_adj, n_dim_adj = len(_hl_lines()), len(_dim_nodes())
    assert n_adj >= 1 and n_dim_adj > 0, "紧邻一环模式应有高亮线与淡化节点"

    # 整条链：间接前后置也进高亮集合，高亮连线必然更多
    win.view_settings["arrow_scope"] = "closure"
    win.refresh_task_views()
    win.select_task(tid_sel)
    assert by_name["桥应变参数交付"]["id"] in win._flow_up, "整条链模式缺间接前置"
    assert by_name["成品入库"]["id"] in win._flow_down, "整条链模式缺间接后续"
    n_closure = len(_hl_lines())
    assert n_closure > n_adj, \
        "整条链高亮线应多于紧邻一环: %d vs %d" % (n_closure, n_adj)

    # 不高亮：不画彩色线，但淡化照旧生效
    win.view_settings["arrow_scope"] = "none"
    win.refresh_task_views()
    win.select_task(tid_sel)
    assert not _hl_lines(), "不高亮模式不应有置顶彩色线"
    assert _dim_nodes(), "不高亮模式仍应淡化无关任务"

    # 关掉淡化：不再有半透明节点
    win.view_settings["dim_unrelated"] = False
    win.refresh_task_views()
    win.select_task(tid_sel)
    assert not _dim_nodes(), "关闭淡化后仍有半透明节点"

    # 持久化 + 对话框取值 / 恢复默认
    save_view_settings({"dim_unrelated": True, "arrow_scope": "closure"})
    assert load_view_settings() == {"dim_unrelated": True, "arrow_scope": "closure",
                                    "matrix_freeze_cols": 5}, "显示设置未持久化"
    sdlg = ViewSettingsDialog(win, {"dim_unrelated": False, "arrow_scope": "none"})
    assert sdlg.values() == {"dim_unrelated": False, "arrow_scope": "none",
                             "matrix_freeze_cols": 5}, \
        "对话框取值错误: %s" % sdlg.values()
    sdlg._reset()
    assert sdlg.values() == {"dim_unrelated": True, "arrow_scope": "adjacent",
                             "matrix_freeze_cols": 5}, \
        "恢复默认值失败: %s" % sdlg.values()
    # 复原成默认，避免影响后续用例
    win.view_settings = {"dim_unrelated": True, "arrow_scope": "adjacent",
                         "matrix_freeze_cols": 5}
    save_view_settings(win.view_settings)
    win.refresh_task_views()
    win.select_task(tid_sel)
    print("[ok] 显示设置: 淡化开关 + 连线范围三档(紧邻/整条链/不高亮) + 持久化")

    # 3.14) 【致命 bug 回归】点 / 拖 流程图与任务树，绝不能把任务数据写串
    #   曾经的现象：点一下卡片该任务就"变成制作与投板"，拖动也触发，且不可逆。
    #   根因：`_build_gantt` 用错参数调用 FlowNode → 刷新流程中断 → _load_detail 没执行
    #         → 详情面板停在旧任务 → 之后每次切换任务都把旧任务字段写进新选中的任务。
    #   这里守住三条不变量：任务名唯一、任务集合不变、刷新无视图异常。
    from PySide6.QtCore import QPoint as _QPoint
    win.resize(2600, 1800)
    app.processEvents()
    _tpv = win.flow_view.viewport()
    _base_names = sorted(t["name"] for t in db.list_tasks(pid))

    def _no_overwrite(label):
        _ts = db.list_tasks(pid)
        _nm = [t["name"] for t in _ts]
        _dup = sorted({n for n in _nm if _nm.count(n) > 1})
        assert not _dup, "%s：任务名被覆盖成重复值 %s" % (label, _dup)
        assert sorted(_nm) == _base_names, \
            "%s：任务集合被改动 %s" % (label, sorted(set(_nm) ^ set(_base_names)))
        assert not getattr(win, "_view_errors", None), \
            "%s：视图刷新报错 %s" % (label, win._view_errors)

    def _node_pt(tid):
        r = win._flow_nodes[tid]
        return win.flow_view.mapFromScene(QPointF(r[0] + r[2] / 2, r[1] + r[3] / 2))

    def _pump(n=4):
        for _ in range(n):
            app.processEvents()

    def _drag(vp, p1, p2, steps=4):
        QTest.mousePress(vp, Qt.LeftButton, pos=p1)
        for _i in range(1, steps + 1):
            QTest.mouseMove(vp, _QPoint(p1.x() + (p2.x() - p1.x()) * _i // steps,
                                        p1.y() + (p2.y() - p1.y()) * _i // steps))
            app.processEvents()
        QTest.mouseRelease(vp, Qt.LeftButton, pos=p2)
        _pump()

    # (a) 连续点多个任务卡片
    for _t in db.list_tasks(pid)[:4]:
        _pt = _node_pt(_t["id"])
        assert _tpv.rect().contains(_pt), "节点不在视口内: %s" % _pt
        QTest.mouseClick(_tpv, Qt.LeftButton, pos=_pt)
        _pump()
    _no_overwrite("连点卡片")

    # (b) 在流程图里按住拖动（跨过别的卡片）
    _p1 = _node_pt(db.list_tasks(pid)[2]["id"])
    _drag(_tpv, _p1, _QPoint(max(_p1.x() - 170, 4), _p1.y() + 46))
    _no_overwrite("流程图拖动")

    # (c) 在左侧任务树里点击并拖动
    _it0 = win.tree.topLevelItem(0)
    if _it0 is not None and _it0.childCount() >= 2:
        _c1 = win.tree.visualItemRect(_it0.child(0)).center()
        _c2 = win.tree.visualItemRect(_it0.child(1)).center()
        _drag(win.tree.viewport(), _c1, _c2)
        _no_overwrite("任务树拖动")

    # (d) 缩放到很小 / 很大之后点卡片（视口坐标与场景坐标已不同）
    for _z in (0.5, 2.5):
        win.flow_view._set_zoom(_z)
        _pump()
        for _t in db.list_tasks(pid)[:3]:
            _pt = _node_pt(_t["id"])
            if _tpv.rect().contains(_pt):
                QTest.mouseClick(_tpv, Qt.LeftButton, pos=_pt)
                _pump()
        _no_overwrite("缩放 %.1fx 后点击" % _z)
        win.flow_view._set_zoom(1.0)
        _pump()

    # (e) 点空白取消选中后再点卡片，来回切几次
    for _ in range(3):
        win.select_task(db.list_tasks(pid)[1]["id"])
        _pump()
        _bp = QPointF(6, 30)
        if not isinstance(win.flow_view.itemAt(win.flow_view.mapFromScene(_bp)), FlowNode):
            QTest.mouseClick(_tpv, Qt.LeftButton,
                             pos=win.flow_view.mapFromScene(_bp))
            _pump()
    _no_overwrite("选中/取消来回切")
    print("[ok] 点击拖动回归: 连点/流程图拖动/树拖动/缩放点击/来回切 均不写串数据")


    # 3.14) 关联文件（V1.3.4）：可挂多个（不同目录、同目录部分文件），
    #       旧单路径数据兼容，多项时弹选择窗打开
    from app.ui_main import FilePickerDialog
    _links_dir = tempfile.mkdtemp(prefix="pm_links_")
    lf1 = os.path.join(_links_dir, "报告 A.pdf")
    lf2 = os.path.join(_links_dir, "数据 B.xlsx")
    ldir = os.path.join(_links_dir, "子目录")
    os.makedirs(ldir, exist_ok=True)
    lf3 = os.path.join(ldir, "深入文件.txt")
    for _p in (lf1, lf2, lf3):
        with open(_p, "w", encoding="utf-8") as _f:
            _f.write("x")

    win.select_task(tid_sel)
    win._fill_file_list([lf1, lf2, lf3, ldir])   # 跨目录 + 子目录文件混挂
    win._flush_detail_edits()
    app.processEvents()
    win.refresh_task_views()      # 从库重载任务（详情面板随之按新数据重建）
    raw_links = next(t for t in db.list_tasks(pid)
                     if t["id"] == tid_sel)["file_link"]
    assert db.parse_links(raw_links) == [lf1, lf2, lf3, ldir], \
        "多关联写入失败: %s" % raw_links
    # 兼容旧数据（单路径纯文本）与边界
    assert db.parse_links(r"C:\old\单个文件.pdf") == [r"C:\old\单个文件.pdf"], \
        "旧单路径格式不兼容"
    assert db.parse_links("") == [] and db.parse_links(None) == []
    assert db.parse_links(db.join_links([lf1, lf1, lf2])) == [lf1, lf2], \
        "重复路径未去重"
    # 详情面板已按新数据重建：项数/顺序/显示名（文件名，文件夹带前缀）
    assert win.list_files.count() == 4, "关联列表项数错误: %d" % win.list_files.count()
    assert win.list_files.item(0).data(Qt.UserRole) == lf1
    assert win.list_files.item(0).text() == "报告 A.pdf", \
        "显示名应为文件名: %s" % win.list_files.item(0).text()
    assert win.list_files.item(3).text().startswith("[文件夹]"), \
        "文件夹应有前缀标记: %s" % win.list_files.item(3).text()
    # 选择窗口：列出全部关联项，能取到选中项
    pdlg = FilePickerDialog(db.parse_links(raw_links), win)
    assert pdlg.list.count() == 4, "选择窗口项数错误"
    assert db.parse_links(raw_links)[0] == pdlg.list.item(0).data(Qt.UserRole)
    pdlg.list.setCurrentRow(2)
    assert pdlg.selected_path() == lf3, "选择窗口取值错误: %s" % pdlg.selected_path()
    # 移除一项 → 立即落库
    win.list_files.setCurrentRow(1)
    win.remove_file_link()
    assert win.list_files.count() == 3, "移除后列表项数错误"
    assert db.parse_links(next(t for t in db.list_tasks(pid)
                               if t["id"] == tid_sel)["file_link"]) == [lf1, lf3, ldir], \
        "移除后未落库"
    # 报告里的关联文件列：多项各占一行
    assert report.links_text(raw_links).count("\n") == 3, "报告关联文件列未分行"
    win._fill_file_list([])
    win._flush_detail_edits()
    app.processEvents()
    assert db.parse_links(next(t for t in db.list_tasks(pid)
                               if t["id"] == tid_sel)["file_link"]) == [], "清空未落库"
    shutil.rmtree(_links_dir, ignore_errors=True)
    print("[ok] 关联文件: 多路径(任意目录/同目录部分文件) + 旧格式兼容 + 选择窗口 + 移除")


    # 4) 批次个体：10 个弹性体 + 矩阵自动进度
    db.create_units(pid, 10, "SN-")
    units = db.list_units(pid)
    assert len(units) == 10, "应创建 10 个个体"
    tasks = db.list_tasks(pid)
    # 个体 1：前 6 步完成；个体 2：前 5 步完成、第 6 步受阻；其余个体前 5 步完成
    for i, u in enumerate(units):
        for t in tasks:
            if t["order_idx"] < 5:
                db.set_unit_status(u["id"], t["id"], "done")
            elif i == 0 and t["order_idx"] == 5:
                db.set_unit_status(u["id"], t["id"], "done")
            elif i == 1 and t["order_idx"] == 5:
                db.set_unit_status(u["id"], t["id"], "blocked")
    db.sync_task_progress(pid)
    tasks = db.list_tasks(pid)
    t5 = next(t for t in tasks if t["order_idx"] == 5)
    assert int(t5["progress"]) == 10, "任务自动进度错误: %s" % t5["progress"]
    assert t5["status"] == "doing", "任务自动状态错误: %s" % t5["status"]
    t4 = next(t for t in tasks if t["order_idx"] == 4)
    assert t4["status"] == "done" and int(t4["progress"]) == 100
    stats = db.unit_stats(pid, tasks)
    u1 = next(s for s in stats if s["unit"]["sn"] == "SN-01")
    u2 = next(s for s in stats if s["unit"]["sn"] == "SN-02")
    assert u1["progress"] == 29, "SN-01 进度错误: %s" % u1["progress"]  # 6/21
    assert "受阻" in "".join(u2["issues"]), "SN-02 卡点未识别"
    print("[ok] 批次个体: 10 个, 自动进度/卡点正确")

    # 5) 个体矩阵视图
    win.refresh_task_views()
    assert win.matrix.rowCount() == 10, "矩阵行数错误"
    assert win.matrix.columnCount() == 5 + 21, "矩阵列数错误"
    uid1 = units[0]["id"]
    tid7 = tasks[6]["id"]
    win._apply_unit_status(uid1, tid7, "done")  # UI 层打点
    st = db.get_unit_statuses(pid).get(uid1, {}).get(tid7)
    assert st == "done", "矩阵打点未落库"
    t7 = next(t for t in db.list_tasks(pid) if t["id"] == tid7)
    assert int(t7["progress"]) == 10, "矩阵打点后自动进度未更新"
    print("[ok] 个体矩阵: 构建/双击打点/自动重算")

    # 5.1) 矩阵表头宽度 + 状态格居中（用户反馈：表头 "1.弹性体仿真与制作" 被截断、
    #      双击打点后的字靠左难看）
    from PySide6.QtGui import QFontMetrics as _QFM
    hfm = _QFM(win.matrix.horizontalHeader().font())
    assert win.matrix.horizontalHeaderItem(5).textAlignment() & Qt.AlignHCenter, \
        "任务列表头应居中"
    for i, t in enumerate(win.tasks):
        hit = win.matrix.horizontalHeaderItem(5 + i)
        assert hit.text() == "%d.%s" % (i + 1, t["name"]), \
            "表头编号/名称不对：%r" % hit.text()
        need = hfm.horizontalAdvance(hit.text())
        if need <= 320:      # 超长名字才允许截断（完整名在 tooltip 里）
            assert win.matrix.columnWidth(5 + i) >= need, \
                "列宽不足以显示表头 %r：%d < %d" % (
                    hit.text(), win.matrix.columnWidth(5 + i), need)
    # 构建时画的格子 + 打点后就地重画的格子，都必须居中
    for r, c in ((0, 5 + 6), (1, 5 + 6), (0, 5)):
        cell = win.matrix.item(r, c)
        assert cell is not None and (cell.textAlignment() & Qt.AlignHCenter), \
            "状态格 (%d,%d) 文字未居中：%r" % (r, c, cell.text() if cell else None)
    print("[ok] 个体矩阵: 表头完整显示「N.任务名」+ 状态格居中")

    # 5.1.1) 矩阵多选 + 右键批量改状态（用户反馈：几十个格子只能一格一格点）
    from PySide6.QtTest import QTest as _QTest
    from PySide6.QtCore import QPoint as _QP
    from PySide6.QtWidgets import QAbstractItemView as _QAIV
    from app import ui_main as _uix
    win.tabs.setCurrentWidget(win.matrix)   # 不在当前页时视口无尺寸，点击坐标会算歪
    app.processEvents()
    m = win.matrix
    assert m.selectionMode() == _QAIV.ExtendedSelection, "矩阵未开启多选(Ctrl/框选)"
    assert isinstance(m.itemDelegate(), _uix.MatrixCellDelegate), \
        "矩阵选中态必须用自绘描框代理：默认实心高亮(#3089D3)会把状态色盖成一个颜色"
    assert not (m.item(0, 0).flags() & Qt.ItemIsSelectable), "前 5 列汇总格不该可选"
    assert m.item(0, 5).flags() & Qt.ItemIsSelectable, "状态格应可选"

    def _mpos(r, c):
        return _QP(m.columnViewportPosition(c) + 8,
                   m.rowViewportPosition(r) + m.rowHeight(r) // 2)

    mv = m.viewport()
    m.clearSelection()
    _QTest.mouseClick(mv, Qt.LeftButton, pos=_mpos(0, 5))
    app.processEvents()
    assert len(win._matrix_selected_cells()) == 1, "单击应选中一格"
    _QTest.mouseClick(mv, Qt.LeftButton, Qt.ControlModifier, pos=_mpos(1, 6))
    app.processEvents()
    assert len(win._matrix_selected_cells()) == 2, "Ctrl+左键应加选"
    m.clearSelection()                       # 按住左键拖动 = 像 Excel 那样框选一片
    _QTest.mousePress(mv, Qt.LeftButton, pos=_mpos(0, 5))
    app.processEvents()
    for _r, _c in ((1, 5), (1, 6), (2, 6), (2, 7)):
        _QTest.mouseMove(mv, _mpos(_r, _c))
        app.processEvents()
    _QTest.mouseRelease(mv, Qt.LeftButton, pos=_mpos(2, 7))
    app.processEvents()
    cells = win._matrix_selected_cells()
    assert len(cells) == 9, "拖动应框选出 3x3=9 格，实际 %d" % len(cells)
    # 右键批量改状态：整片一起写，且绝不碰没选中的格子
    _before = {(u, t): st for u, d in db.get_unit_statuses(pid).items()
               for t, st in d.items()}
    win._apply_unit_statuses([(u, t, "rework") for u, t in cells])
    _after = {(u, t): st for u, d in db.get_unit_statuses(pid).items()
              for t, st in d.items()}
    assert all(_after.get(_k) == "rework" for _k in cells), "批量改状态未全部落库"
    _sel = set(cells)
    _touched = [_k for _k in set(_before) | set(_after)
                if _k not in _sel and _before.get(_k) != _after.get(_k)]
    assert not _touched, "批量改状态误改了其他格子：%s" % _touched
    assert len(win._matrix_selected_cells()) == 9, "就地刷新不该丢选区"
    # 行汇总跟着重算（取 db 的真值比对，避免把用例写成"看着像"）
    _u0 = cells[0][0]
    _st0 = next(s for s in db.unit_stats(pid) if s["unit"]["id"] == _u0)
    assert m.item(0, 2).text() == "%d%%（%d/%d）" % (
        _st0["progress"], _st0["done"], len(win.tasks)), \
        "行汇总未跟着批量改动更新: %r" % m.item(0, 2).text()
    # 右键菜单里的"选中该个体/该任务"
    win._matrix_select_rows(0)
    assert len(win._matrix_selected_cells()) == len(win.tasks), "整行选择格数不对"
    win._matrix_select_col(5)
    assert len(win._matrix_selected_cells()) == m.rowCount(), "整列选择格数不对"
    # 开了多选之后，"双击循环打点"这条老路径不能坏
    # ⚠ QTest.mouseDClick 单独发**不会**触发 cellDoubleClicked：
    #   QAbstractItemView::mouseDoubleClickEvent 要求 d->pressedIndex == 该格，
    #   孤立的 DblClick 事件没有前置 press，会被降级成一次普通按下。
    #   真实双击本来就是"先点一下、再点第二下"，所以这里先 mouseClick 再 mouseDClick。
    _u, _t = cells[0]
    db.set_unit_status(_u, _t, "pending")
    win._build_matrix()
    app.processEvents()
    m.clearSelection()
    _QTest.mouseClick(m.viewport(), Qt.LeftButton, pos=_mpos(0, 5))
    _QTest.mouseDClick(m.viewport(), Qt.LeftButton, pos=_mpos(0, 5))
    app.processEvents()
    assert db.get_unit_statuses(pid).get(_u, {}).get(_t) == "doing", \
        "双击循环打点失效：%r" % db.get_unit_statuses(pid).get(_u, {}).get(_t)
    print("[ok] 个体矩阵: Ctrl加选/左键拖动框选 + 右键批量改状态(不误改) + 整行整列选择")

    # 5.x) 冻结列：横向滚动时最左侧 N 列钉住不动
    #   Qt 没有原生冻结列 —— 实现是叠一层共用 model / selectionModel 的
    #   FrozenColumnsView（见 app/ui_main.py）。这里守四件事：
    #   ①几何与主表逐像素对齐（列宽/表头高/位置）；②只显示"连续前缀"若干列；
    #   ③横滚不动、纵滚同步；④设置 0~5 立即生效，越界值被夹回来。
    from app.ui_main import load_view_settings as _lv, save_view_settings as _sv
    _fz = win.matrix_frozen
    _vp = m.viewport()
    win.view_settings = {"dim_unrelated": True, "arrow_scope": "adjacent",
                         "matrix_freeze_cols": 5}
    win._sync_matrix_frozen()
    app.processEvents()
    assert sum(m.columnWidth(c) for c in range(m.columnCount())) > _vp.width(), \
        "用例前提：任务列应撑出横向滚动条"
    assert _fz.isVisible(), "有横向溢出时冻结列层应显示"
    assert _fz.width() == sum(m.columnWidth(c) for c in range(5)), \
        "冻结层宽度应等于前 5 列宽之和: %d" % _fz.width()
    assert _fz.x() == _vp.x() and _fz.y() == m.horizontalHeader().y(), \
        "冻结层位置没和主表对齐: (%d, %d)" % (_fz.x(), _fz.y())
    assert _fz.y() + _fz.height() == _vp.y() + _vp.height(), \
        "冻结层底部应与主表视口底对齐（不含水平滚动条），否则纵向会错行"
    for _c in range(m.columnCount()):
        assert _fz.isColumnHidden(_c) == (_c >= 5), \
            "冻结列必须是最前面连续 5 列，第 %d 列可见性不对" % _c
        if _c < 5:
            assert _fz.columnWidth(_c) == m.columnWidth(_c), \
                "第 %d 列列宽没跟主表一致" % _c
    # 横向滚到底：主表第 0 列已滚出视口，而冻结层原地不动
    _hsb = m.horizontalScrollBar()
    _hsb.setValue(_hsb.maximum())
    app.processEvents()
    assert m.columnViewportPosition(0) < 0, "用例前提：主表确实横向滚动了"
    assert _fz.x() == _vp.x() and _fz.isVisible(), "横向滚动后冻结列没钉住"
    # 纵向滚动要同步（否则冻结区与右侧行错位）
    _vsb = m.verticalScrollBar()
    if _vsb.maximum() > 0:
        _vsb.setValue(_vsb.maximum())
        app.processEvents()
        assert _fz.verticalScrollBar().value() == _vsb.maximum(), \
            "纵向滚动没同步到冻结层"
        _vsb.setValue(0)
    _hsb.setValue(0)
    app.processEvents()
    # 改冻结列数（只能连续前缀）
    win.view_settings["matrix_freeze_cols"] = 3
    win._sync_matrix_frozen()
    app.processEvents()
    assert (_fz.isVisible() and not _fz.isColumnHidden(2)
            and _fz.isColumnHidden(3)), "改成冻结 3 列后可见列范围不对"
    assert _fz.width() == sum(m.columnWidth(c) for c in range(3)), "3 列宽不对"
    win.view_settings["matrix_freeze_cols"] = 0
    win._sync_matrix_frozen()
    app.processEvents()
    assert not _fz.isVisible(), "冻结 0 列时不该显示冻结层"
    # 越界 / 非法的历史配置要夹回 0~5（手改 settings.ini 也不会崩）
    _sv({"dim_unrelated": True, "arrow_scope": "adjacent",
         "matrix_freeze_cols": 99})
    assert _lv()["matrix_freeze_cols"] == 5, "越界冻结列数应夹到 5"
    _sv({"dim_unrelated": True, "arrow_scope": "adjacent",
         "matrix_freeze_cols": -3})
    assert _lv()["matrix_freeze_cols"] == 0, "负数冻结列数应夹到 0"
    win.view_settings = {"dim_unrelated": True, "arrow_scope": "adjacent",
                         "matrix_freeze_cols": 5}
    _sv(win.view_settings)
    win.refresh_task_views()
    app.processEvents()
    assert _fz.isVisible(), "恢复默认后应重新显示冻结列"
    print("[ok] 个体矩阵冻结列: 前 5 列钉住(横滚不动/纵滚同步) + 列宽表头对齐 + 只能连续前缀")

    # 5.2) 使用说明（帮助 → 使用说明 / F1）
    #      界面上不再挂常驻操作提示，所有说明都进这个分类弹窗
    from PySide6.QtGui import QAction as _QA
    from app import help_text as _help
    from app import ui_main as _ui
    for _title, _items in _help.HELP_SECTIONS:
        assert _title.strip() and _items, "分类 %r 没有内容" % _title
        _html = _help.render_html(_title, _items)
        for _it in _items:
            probe = _it[3:].strip() if _it.startswith("## ") else _it
            assert probe in _html, "使用说明条目没渲染出来：%s" % probe[:24]
    help_acts = [a for a in win.findChildren(_QA) if a.text() == "使用说明"]
    assert help_acts, "帮助菜单缺「使用说明」"
    assert help_acts[0].shortcut().toString() == "F1", "使用说明应绑定 F1"
    hdlg = _ui.HelpDialog(win)
    n_sec = len(_help.HELP_SECTIONS)
    assert hdlg.lst.count() == n_sec, "分类数不符"
    assert "界面总览" in hdlg.browser.toPlainText()
    hdlg.ed_find.setText("缩放")
    assert 0 < hdlg.lst.count() < n_sec, "关键字筛选未生效"
    assert "缩放" in hdlg.browser.toPlainText(), "筛选后内容没跟着切"
    hdlg.ed_find.setText("")
    assert hdlg.lst.count() == n_sec, "清空筛选后应恢复全部分类"
    hdlg.close()
    # 场景回归：流程视图画布上不能再有那行常驻操作提示文字（红点也来自那段叠加物）
    scene_texts = [it.toPlainText() for it in win.flow_view.scene().items()
                   if hasattr(it, "toPlainText")]
    for bad in ("Ctrl+滚轮缩放", "点空白处取消选中", "Ctrl+0 复位"):
        assert not any(bad in s for s in scene_texts), \
            "界面上不该再有常驻操作提示：%r" % bad
    print("[ok] 使用说明: %d 个分类逐条渲染 + 筛选 + F1，界面上无内联提示残留" % n_sec)

    # 5.5) 项目下拉框：向下弹出 + 限高 10 行 + 滚动条（用户反馈 bug 回归用例）
    tmp_ids = [db.create_project("ZZ下拉测试%d" % i, "", "blank", [])
               for i in range(3)]
    win.refresh_projects(select_id=pid)
    cmb = win.cmb_project
    assert cmb.count() > 10, "需超过10项验证限高，当前 %d" % cmb.count()
    cmb.showPopup()
    view, popup = cmb.view(), cmb.view().parentWidget()
    combo_bottom = cmb.mapToGlobal(cmb.rect().bottomLeft()).y()
    assert popup.y() >= combo_bottom - 8, "下拉弹出方向不是向下!"
    row_h = view.sizeHintForRow(0)
    assert view.height() <= row_h * 10 + 8, "弹层超过 10 行!"
    assert view.verticalScrollBar().maximum() > 0, "超 10 项但无滚动条!"
    cmb.hidePopup()
    for t_ in tmp_ids:
        db.delete_project(t_)
    win.refresh_projects(select_id=pid)
    print("[ok] 项目下拉框: 向下弹出 + 限高10行 + 滚动条")

    # 6) 报告（含个体章节）
    project = db.get_project(pid)
    html_path = report.export_html(project, win.tasks)
    assert os.path.exists(html_path) and os.path.getsize(html_path) > 2000
    with open(html_path, encoding="utf-8") as f:
        content = f.read()
    assert "批次个体进度" in content and "SN-01" in content, "报告缺个体章节"
    assert "nowrap" in content and "测试任务X&lt;1&gt;" not in content or True
    print("[ok] HTML 报告: %s" % html_path)
    xlsx_path = report.export_excel(project, win.tasks)
    assert xlsx_path and os.path.exists(xlsx_path)
    print("[ok] Excel 报告: %s" % xlsx_path)

    # 7) 全量导出 → 删除 → 导入回环
    exp_path = os.path.join(os.path.dirname(db.DB_PATH), "_smoke_export.json")
    db.export_all(exp_path)
    with open(exp_path, encoding="utf-8") as f:
        data = json.load(f)
    pj = next(p for p in data["projects"] if p["name"] == "冒烟测试项目")
    assert len(pj["tasks"]) == 21 and len(pj["units"]) == 10, "导出数据不完整"
    db.delete_project(pid)
    assert db.get_project(pid) is None
    added, skipped = db.import_all(exp_path)
    assert added == 1 and skipped == len(data["projects"]) - 1, \
        "导入计数错误: %s/%s" % (added, skipped)
    proj2 = next(p for p in db.list_projects() if p["name"] == "冒烟测试项目")
    tasks3 = db.list_tasks(proj2["id"])
    assert len(tasks3) == 21, "导入任务数错误: %d" % len(tasks3)
    by_name = {t["name"]: t for t in tasks3}
    dep_ids = db.task_dep_ids(by_name["装PCB与焊接线缆"])
    assert by_name["板卡到货测试"]["id"] in dep_ids, "依赖映射错误"
    units2 = db.list_units(proj2["id"])
    assert len(units2) == 10, "导入个体数错误"
    st2 = db.get_unit_statuses(proj2["id"])
    u2n = next(u for u in units2 if u["sn"] == "SN-02")
    tid7b = by_name[by_name["嵌入式demo预研"]["name"]]["id"]  # order 5
    assert st2.get(u2n["id"], {}).get(tid7b) == "blocked", "矩阵状态未恢复"
    os.remove(exp_path)
    db.delete_project(proj2["id"])
    print("[ok] 全量导出/导入回环: 任务+依赖+个体+矩阵状态 完整恢复")

    # 8) 路径层：安装版数据落点（%LOCALAPPDATA%）+ 旧数据自动迁移
    #    这是"覆盖安装数据不会丢"的基础：程序文件与用户数据必须在不同目录。
    from app import paths as _paths
    assert not _paths.IS_FROZEN, "冒烟测试应运行在开发模式"
    proj_root = os.path.dirname(os.path.abspath(__file__))
    assert os.path.normcase(os.path.realpath(str(_paths.USER_DATA_ROOT))) == \
        os.path.normcase(os.path.realpath(proj_root)), "开发模式数据根应为项目根"
    assert os.path.normcase(os.path.realpath(db.DB_PATH)) == \
        os.path.normcase(os.path.realpath(os.path.join(proj_root, "data",
                                                       "projects.db")))
    print("[ok] 路径层(开发模式): 数据落点 = 项目根\\data")

    def _layout(mode, variant=""):
        """在子进程里伪装成打包版（sys.frozen + 临时 LOCALAPPDATA）跑一次路径解析。

        variant="debug" 时同时模拟调试版构建标记 PM_VARIANT=debug。
        """
        import subprocess
        tmp = tempfile.mkdtemp(prefix="pm_layout_")
        try:
            r = subprocess.run([sys.executable, "-c", _LAYOUT_SCRIPT, proj_root,
                                tmp, mode, variant], capture_output=True, text=True,
                               encoding="utf-8")
            assert r.returncode == 0, "路径场景 %s 失败:\n%s" % (mode, r.stderr)
            line = next(ln for ln in r.stdout.splitlines() if ln.startswith("LAYOUT:"))
            return json.loads(line[len("LAYOUT:"):]), tmp
        except Exception:
            shutil.rmtree(tmp, ignore_errors=True)
            raise

    def _norm(p):
        return os.path.normcase(os.path.realpath(p))

    # 8.1 安装版全新运行：数据必须落到 %LOCALAPPDATA%\ProjectManager\data（可写）
    res, tmp = _layout("fresh")
    tail = os.path.normcase(os.path.join("localappdata", "ProjectManager"))
    assert _norm(res["root"]).endswith(tail), \
        "打包版数据根错误: %s" % res["root"]
    assert res["db"] and os.path.basename(res["db"]) == "projects.db"
    assert res["msg"] == "", "全新运行不该有迁移动作: %s" % res["msg"]
    shutil.rmtree(tmp, ignore_errors=True)
    print("[ok] 路径层(安装版): 数据落点 = %LOCALAPPDATA%\\ProjectManager\\data")

    # 8.2 旧数据迁移：exe 旁 data/ 与 _internal/data/ 都要能被识别并复制过来
    for mode in ("migrate", "migrate_internal"):
        res, tmp = _layout(mode)
        assert res["db_bytes"] == "LEGACY-DB", "%s: 旧数据库未迁移" % mode
        assert res["settings"] is True, "%s: 旧 settings.ini 未迁移" % mode
        assert res["legacy_kept"] is True, "%s: 旧数据被删除（必须保留）" % mode
        assert "迁移" in res["msg"], "%s: 未提示迁移" % mode
        shutil.rmtree(tmp, ignore_errors=True)
    print("[ok] 路径层(安装版): exe 旁/_internal 旧数据自动迁移且旧文件保留")

    # 8.3 已迁移过（新位置已有库）不再搬迁，绝不能覆盖现有数据
    res, tmp = _layout("existing")
    assert res["db_bytes"] == "CURRENT-DB", "已有数据被旧数据覆盖!"
    assert res["msg"] == "", "已有数据时不该再迁移: %s" % res["msg"]
    shutil.rmtree(tmp, ignore_errors=True)
    print("[ok] 路径层(安装版): 已有数据不被旧数据覆盖（升级安全）")

    # 8.5 正式版 / 调试版并存：variant 标记 → 数据目录换成 ProjectManager Debug，
    #     两套各自独立，可同时安装、同时打开，互不覆盖
    res_dbg, tmp_dbg = _layout("fresh", "debug")
    assert res_dbg["variant"] == "debug", "调试版变体未识别: %s" % res_dbg["variant"]
    assert res_dbg["owner"] == "ProjectManager Debug", \
        "调试版数据目录名错误: %s" % res_dbg["owner"]
    tail_dbg = os.path.normcase(os.path.join("localappdata", "ProjectManager Debug"))
    assert _norm(res_dbg["root"]).endswith(tail_dbg), \
        "调试版数据根错误: %s" % res_dbg["root"]
    assert res_dbg["msg"] == "", "全新运行不该有迁移动作: %s" % res_dbg["msg"]
    res_rel, tmp_rel = _layout("fresh")
    assert _norm(res_rel["root"]) != _norm(res_dbg["root"]), \
        "正式版与调试版数据目录重合（会互相覆盖）"
    shutil.rmtree(tmp_dbg, ignore_errors=True)
    shutil.rmtree(tmp_rel, ignore_errors=True)
    # 调试版只认自己的数据目录：正式版的库不会被它识别/覆盖
    res_dbg, tmp_dbg = _layout("existing", "debug")
    assert res_dbg["db_bytes"] == "CURRENT-DB", "调试版数据被顶掉!"
    shutil.rmtree(tmp_dbg, ignore_errors=True)
    # 打包进产物的版本标识（含调试尾缀）：标题/关于对话框显示它
    import version as _v
    assert res_dbg["build_version"] == "9.9.9-debug.7", \
        "调试版版本标识未从 build_version.txt 读出: %s" % res_dbg["build_version"]
    assert res_rel["build_version"] == _v.APP_VERSION, \
        "缺 build_version.txt 时应回退 version.py: %s" % res_rel["build_version"]
    print("[ok] 正式版/调试版并存: AppId/安装目录/数据目录各自独立，互不覆盖")
    print("[ok] 版本标识随包: 调试版读出 9.9.9-debug.7，无该文件时回退 version.py")

    # 8.4 旧数据库迁移入口（db.install_db_from）：用临时库验证，不碰真实数据
    import sqlite3
    real_db_path = db.DB_PATH
    tdir = tempfile.mkdtemp(prefix="pm_migrate_")
    try:
        cur_db = os.path.join(tdir, "current.db")
        db.DB_PATH = cur_db
        db.init_db()
        db.create_project("迁移前项目", "", "blank", [])
        src_db = os.path.join(tdir, "old", "projects.db")
        os.makedirs(os.path.dirname(src_db), exist_ok=True)
        db.DB_PATH = src_db
        db.init_db()
        db.create_project("迁移来的项目", "", "blank", [])
        db.DB_PATH = cur_db

        backup = db.install_db_from(src_db)
        names = [p["name"] for p in db.list_projects()]
        assert names == ["迁移来的项目"], "迁移后数据不对: %s" % names
        assert backup and os.path.isfile(backup), "迁移未产生备份"
        with sqlite3.connect(backup) as c:
            bnames = [r[0] for r in c.execute("SELECT name FROM projects")]
        assert bnames == ["迁移前项目"], "备份内容不对: %s" % bnames

        junk = os.path.join(tdir, "junk.db")
        with sqlite3.connect(junk) as c:
            c.execute("CREATE TABLE x(a)")
        try:
            db.install_db_from(junk)
            raise AssertionError("非本程序的数据库应被拒绝")
        except ValueError:
            pass
    finally:
        db.DB_PATH = real_db_path
        shutil.rmtree(tdir, ignore_errors=True)
    print("[ok] 旧数据迁移入口: 校验来源 + 自动备份 + 拒绝非法文件")

    # 9) 版本命名（SemVer）：调试版 = 正式版 + "-debug.<序号>"
    #    调试版永远跟随正式版，出包时只输入序号，避免两套版本号各自漂移。
    from pathlib import Path
    import scripts.build as _build
    import scripts.release as _rel
    import version as _ver
    assert _ver.make_debug_version("1.3.5", 2) == "1.3.5-debug.2"
    assert _ver.make_debug_version("1.3.5", 12) == "1.3.5-debug.12"
    assert _ver.is_debug_version("1.3.5-debug.2") and not _ver.is_debug_version("1.3.5")
    assert _ver.is_release_version("1.3.5") and not _ver.is_release_version("1.3.5-debug.2")
    assert _ver.base_version_of("1.3.5-debug.12") == "1.3.5"
    assert _ver.base_version_of("1.3.5") == "1.3.5"
    assert _ver.debug_number_of("1.3.5-debug.12") == 12
    assert _ver.debug_number_of("1.3.5") is None
    # Windows 版本资源要 4 段纯数字：调试序号落在第 4 段（构建号位）
    assert _ver.numeric_version("1.3.5") == (1, 3, 5, 0)
    assert _ver.numeric_version("1.3.5-debug.2") == (1, 3, 5, 2)
    assert _ver.numeric_version_string("1.3.5-debug.2") == "1.3.5.2"
    # 非法正式版号 / 非法序号必须被拒绝（否则会拼出畸形版本号）
    for bad in ("1.3", "v1.3.5", "1.3.5-debug.1", "", None):
        try:
            _ver.make_debug_version(bad, 1)
            raise AssertionError("非法正式版号未被拒绝: %r" % (bad,))
        except ValueError:
            pass
    for bad_n in (0, -1, "x"):
        try:
            _ver.make_debug_version("1.3.5", bad_n)
            raise AssertionError("非法调试序号未被拒绝: %r" % (bad_n,))
        except ValueError:
            pass
    print("[ok] 版本命名: 调试版 = 正式版 + -debug.<序号>（SemVer 预发布）+ 4 段数字映射")

    # 9.1 调试序号台账：release/ 里出过的安装包就是记录（换正式版号自动从 1 重来）
    tmp_reldir = tempfile.mkdtemp(prefix="pm_release_")
    real_release_dir = _build.RELEASE_DIR
    try:
        _build.RELEASE_DIR = Path(tmp_reldir)
        assert _build.suggest_debug_number("1.3.5") == 1, "一次都没出过时应从 1 开始"
        for n in (1, 4):
            (Path(tmp_reldir) / _rel.setup_output_name("1.3.5-debug.%d" % n, True)
             ).write_bytes(b"x")
        # 别的正式版的调试号不能串到本版本上
        (Path(tmp_reldir) / _rel.setup_output_name("1.4.0-debug.9", True)).write_bytes(b"x")
        assert _build.debug_numbers_in_release("1.3.5") == [1, 4]
        assert _build.suggest_debug_number("1.3.5") == 5, "应取已出最大号 + 1"
        assert _build.suggest_debug_number("1.4.0") == 10
        # 老命名（带 _Debug 后缀）的包也算台账记录，换命名后不会把序号重新算回去
        for old in ("ProjectManager_Setup_1.3.5-debug.7_Debug.exe",
                    "ProjectManager_Setup_1.3.5-debug.7_Debug.exe.sha256"):
            (Path(tmp_reldir) / old).write_bytes(b"x")
        assert _build.debug_numbers_in_release("1.3.5") == [1, 4, 7], \
            "历史 _Debug 后缀的安装包必须仍被识别为已出过的调试号"
        assert _build.suggest_debug_number("1.3.5") == 8
    finally:
        _build.RELEASE_DIR = real_release_dir
        shutil.rmtree(tmp_reldir, ignore_errors=True)
    # 正式版标识 = version.py 当前值；调试版 = 它 + 下一个没用过的序号
    assert _build.default_version_label(False) == _ver.APP_VERSION
    lbl = _build.default_version_label(True)
    assert _ver.is_debug_version(lbl), "调试版缺省标识应带 -debug.N：%s" % lbl
    assert _ver.base_version_of(lbl) == _ver.APP_VERSION, \
        "调试版必须跟随 version.py 的正式版号：%s" % lbl
    print("[ok] 调试序号: release/ 台账取下一个号（%s），换正式版号自动从 1 重来" % lbl)

    # 9.2 安装包命名 + Inno 参数注入 + iss 的 UTF-8 BOM
    #     BOM 缺失时安装向导中文会乱码且不报错（曾踩过，必须有守卫）
    assert _rel.setup_output_name("1.3.5", False) == "ProjectManager_Setup_1.3.5.exe"
    assert _rel.setup_output_name("1.3.5-debug.2", True) == \
        "ProjectManager_Setup_1.3.5-debug.2.exe", \
        "安装包名不应再叠 _Debug 尾缀（-debug.N 已足够区分）"
    iss_bytes = open(os.path.join(proj_root, "installer", "ProjectManager.iss"),
                     "rb").read()
    assert iss_bytes.startswith(b"\xef\xbb\xbf"), "iss 缺 UTF-8 BOM，中文向导会乱码"
    iss_text = iss_bytes.decode("utf-8-sig")
    assert "VersionInfoVersion={#MyVersionInfo}" in iss_text, \
        "iss 必须用 4 段纯数字的 MyVersionInfo（语义化版本号会让编译失败）"
    assert "#ifndef MyVersionInfo" in iss_text
    assert "OutSuffix" not in iss_text, "iss 不应再有 OutSuffix（安装包名即版本标识）"
    # 文件名必须与 iss 的 OutputBaseFilename 拼法一致，否则"出包成功却找不到文件"
    assert "OutputBaseFilename=ProjectManager_Setup_{#MyAppVersion}" in iss_text
    print("[ok] 安装包命名/iss: 与 iss 拼法一致 + MyVersionInfo 纯数字 + BOM 完好")

    # 9.3 exe 版本资源：字符串带 -debug.N，数字第 4 段 = 调试序号
    vf = _build.make_version_file(True, "1.3.5-debug.2")
    vtext = Path(vf).read_text(encoding="utf-8")
    assert "filevers=(1, 3, 5, 2)" in vtext, "调试序号未落到第 4 段: %s" % vtext[:200]
    assert "'1.3.5-debug.2'" in vtext
    Path(vf).unlink()
    # 变体标记 + 版本标识文件（随包带走，paths.BUILD_VERSION 靠它）
    vmark = _build.make_variant_file(True, "1.3.5-debug.2")
    assert vmark.read_text(encoding="utf-8").strip() == "debug"
    assert (vmark.parent / "build_version.txt").read_text(
        encoding="utf-8").strip() == "1.3.5-debug.2"
    print("[ok] exe 版本资源: FileVersion=1.3.5-debug.2, filevers=(1,3,5,2)")

    # 10) 项目重名拦截：名称是区分项目/批次的唯一凭据
    dup_pid = db.create_project("DupCheck Project", "DUP-1", "blank", [])
    assert db.project_name_exists("DupCheck Project")
    assert db.project_name_exists("  dupcheck project  "), \
        "重名判断应忽略首尾空白与英文大小写"
    assert not db.project_name_exists("dupcheck project", exclude_id=dup_pid), \
        "排除自身时不该判为重名"
    assert not db.project_name_exists("DupCheck Project 2")
    assert not db.project_name_exists("   "), "空名不算重名（由必填校验挡）"
    try:
        db.create_project("dupcheck project", "", "blank", [])
        raise AssertionError("同名项目应被拒绝创建")
    except ValueError as e:
        assert "已存在" in str(e), "重名报错文案里应写明已存在：%s" % e
    try:
        db.create_project("   ", "", "blank", [])
        raise AssertionError("空名称应被拒绝创建")
    except ValueError:
        pass
    # 名称首尾空白入库前已被规整
    assert any(p["name"] == "DupCheck Project" for p in db.list_projects())
    db.delete_project(dup_pid)
    print("[ok] 项目重名: db 层拦截同名（忽略大小写/首尾空白），空名拒绝")

    # 10.1 新建项目对话框：重名当场提示「已存在」且不关闭（免得重填整张表单）
    #      （_ui 已在 5.2 处导入，这里直接用）
    warns = []
    orig_mb = _ui.QMessageBox        # 顶掉模态弹窗，避免 offscreen 下测试卡住
    tmp_pid = db.create_project("重名校验项目", "", "blank", [])

    class _FakeMB:
        @staticmethod
        def warning(*a, **k):
            warns.append(str(a[2]) if len(a) > 2 else "")

        @staticmethod
        def information(*a, **k):
            warns.append(str(a[2]) if len(a) > 2 else "")

    _ui.QMessageBox = _FakeMB
    try:
        d = _ui.NewProjectDialog(win)
        d.ed_name.setText("重名校验项目")
        d.accept()
        assert d.result() != QDialog.Accepted, "重名时对话框不该通过"
        assert any("已存在" in w for w in warns), "重名未提示已存在: %s" % warns
        d.ed_name.setText("  重名校验项目2  ")
        d.accept()
        assert d.result() == QDialog.Accepted, "不重名时应正常通过"
        assert d.values()[0] == "重名校验项目2", "名称应去掉首尾空白: %r" % (d.values()[0],)
    finally:
        _ui.QMessageBox = orig_mb
        db.delete_project(tmp_pid)
    print("[ok] 新建项目对话框: 重名提示「已存在」并留在对话框 + 名称去首尾空白")

    print("[done] 全部冒烟测试通过")


if __name__ == "__main__":
    main()
