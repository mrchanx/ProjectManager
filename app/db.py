# -*- coding: utf-8 -*-
"""SQLite 数据层：项目 / 任务 / 批次个体 三张表，单文件存储。

个体（unit）= 批次中的一台传感器/一个弹性体。
有个体的项目里，任务进度与状态由「个体矩阵」自动计算，不再手填。
"""
import json
import os
import sqlite3
from datetime import date, datetime

from . import paths

# 数据库位置由 app/paths.py 统一决定：
#   开发模式 = 项目根\data\projects.db；安装版 = %LOCALAPPDATA%\ProjectManager\data\projects.db
# 这样升级只覆盖程序文件，数据不受影响。
DB_PATH = paths.DB_PATH

# 任务状态常量
STATUS_PENDING = "pending"      # 未开始
STATUS_DOING = "doing"          # 进行中
STATUS_DONE = "done"            # 完成
STATUS_BLOCKED = "blocked"      # 受阻
STATUS_REWORK = "rework"        # 返工中

STATUS_NAMES = {
    STATUS_PENDING: "未开始",
    STATUS_DOING: "进行中",
    STATUS_DONE: "完成",
    STATUS_BLOCKED: "受阻",
    STATUS_REWORK: "返工中",
}


# ---------------- 关联文件（可多项：不同文件夹的文件 / 同一文件夹的部分文件）----------------

def parse_links(raw) -> list:
    """file_link 字段 → 路径列表。

    新格式存 JSON 数组字符串；老数据是单个路径的纯文本，
    这里一并兼容（不是 JSON 数组就整体当作一项），无需迁移。
    """
    if raw is None:
        return []
    s = str(raw).strip()
    if not s:
        return []
    if s.startswith("["):
        try:
            data = json.loads(s)
        except (ValueError, TypeError):
            return [s]
        if isinstance(data, list):
            out = []
            for x in data:
                v = str(x).strip()
                if v and v not in out:
                    out.append(v)
            return out
    return [s]


def join_links(links) -> str:
    """路径列表 → 落库字符串（JSON 数组，天然支持中文/空格路径）。"""
    items = []
    for p in (links or []):
        v = str(p).strip()
        if v and v not in items:
            items.append(v)
    return json.dumps(items, ensure_ascii=False) if items else ""

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    code TEXT DEFAULT '',
    template TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    stage TEXT DEFAULT '',
    name TEXT NOT NULL,
    owner TEXT DEFAULT '',
    status TEXT DEFAULT 'pending',
    progress INTEGER DEFAULT 0,
    plan_start TEXT DEFAULT '',
    plan_end TEXT DEFAULT '',
    actual_start TEXT DEFAULT '',
    actual_end TEXT DEFAULT '',
    depends_on TEXT DEFAULT '',
    note TEXT DEFAULT '',
    file_link TEXT DEFAULT '',
    order_idx INTEGER DEFAULT 0,
    FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks (project_id);
CREATE TABLE IF NOT EXISTS units (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    sn TEXT DEFAULT '',
    name TEXT DEFAULT '',
    note TEXT DEFAULT '',
    order_idx INTEGER DEFAULT 0,
    FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_units_project ON units (project_id);
CREATE TABLE IF NOT EXISTS unit_task_status (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_id INTEGER NOT NULL,
    task_id INTEGER NOT NULL,
    status TEXT DEFAULT 'pending',
    note TEXT DEFAULT '',
    updated_at TEXT DEFAULT (datetime('now', 'localtime')),
    UNIQUE (unit_id, task_id),
    FOREIGN KEY (unit_id) REFERENCES units (id) ON DELETE CASCADE,
    FOREIGN KEY (task_id) REFERENCES tasks (id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS project_stages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    order_idx INTEGER DEFAULT 0,
    UNIQUE (project_id, name),
    FOREIGN KEY (project_id) REFERENCES projects (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_pstages_project ON project_stages (project_id);
CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    note TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now', 'localtime')),
    data TEXT NOT NULL DEFAULT '[]'
);
"""


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def install_db_from(src_path):
    """用外部 projects.db 替换当前数据库（换电脑 / 老版本数据搬迁）。

    安全策略：先校验来源确是本程序的数据库，再把现有库**备份**到
    data\\backups\\projects_<时间戳>.db，最后才覆盖。

    返回备份文件路径；原本没有数据库时返回 None。
    """
    import shutil

    if not os.path.isfile(src_path):
        raise ValueError("文件不存在：%s" % src_path)
    if os.path.abspath(src_path) == os.path.abspath(DB_PATH):
        raise ValueError("所选文件就是当前正在使用的数据库，无需迁移")

    con = sqlite3.connect(src_path)
    try:
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        con.close()
    if not {"projects", "tasks"} <= names:
        raise ValueError("该文件不是本程序的数据库（缺少 projects / tasks 表）")

    backup = None
    if os.path.isfile(DB_PATH):
        bdir = os.path.join(os.path.dirname(DB_PATH), "backups")
        os.makedirs(bdir, exist_ok=True)
        backup = os.path.join(bdir, "projects_%s.db"
                              % datetime.now().strftime("%Y%m%d_%H%M%S"))
        shutil.copy2(DB_PATH, backup)
    shutil.copy2(src_path, DB_PATH)
    init_db()          # 保证新库结构完整（老库可能缺 units 等新表）
    return backup


# ---------------- 项目 ----------------

def list_projects():
    conn = get_conn()
    rows = conn.execute("SELECT * FROM projects ORDER BY id DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def project_name_exists(name, exclude_id=None):
    """是否已存在同名项目（忽略首尾空白与英文大小写）。

    exclude_id 用于"改名时排除自己"，创建时不需要传。
    中文名不受大小写影响，NOCASE 只为挡住 Project / project 这类英文重名。
    """
    n = (name or "").strip()
    if not n:
        return False
    conn = get_conn()
    if exclude_id is None:
        row = conn.execute(
            "SELECT 1 FROM projects WHERE TRIM(name) = ? COLLATE NOCASE LIMIT 1",
            (n,)).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM projects WHERE TRIM(name) = ? COLLATE NOCASE "
            "AND id <> ? LIMIT 1", (n, exclude_id)).fetchone()
    conn.close()
    return row is not None


def create_project(name, code, template_name, template_tasks):
    """创建项目并按模板批量生成任务。template_tasks: [dict]

    同名项目一律拒绝（ValueError）：项目名是用户区分项目的唯一凭据，
    重名会让报告、看板、导出数据、个体台账都难以辨认（哪个是哪个批次）。
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("项目名称不能为空")
    if project_name_exists(name):
        raise ValueError("已存在同名项目：%s" % name)
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO projects (name, code, template) VALUES (?, ?, ?)",
        (name, code, template_name))
    pid = cur.lastrowid
    # 播种项目阶段列表：只按模板任务里出现的顺序登记。
    # 空模板（没有任何任务）**不预置任何阶段** —— 空白项目就该从空的开始，
    # 阶段全由用户自己建：先建的排第一，后建的往后排，之后可在
    # 「阶段管理」(Ctrl+G) 或任务树上右键调顺序。
    stages = []
    for t in template_tasks:
        s = (t.get("stage") or "").strip()
        if s and s not in stages:
            stages.append(s)
    for i, s in enumerate(stages):
        conn.execute(
            "INSERT OR IGNORE INTO project_stages "
            "(project_id, name, order_idx) VALUES (?,?,?)", (pid, s, i))
    id_map = {}
    for idx, t in enumerate(template_tasks):
        c = conn.execute(
            "INSERT INTO tasks (project_id, stage, name, owner, status, progress,"
            " plan_start, plan_end, depends_on, note, order_idx)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (pid, t["stage"], t["name"], t.get("owner", ""), STATUS_PENDING, 0,
             t.get("plan_start", ""), t.get("plan_end", ""),
             "", t.get("note", ""), idx))
        # 模板依赖用名称记录，创建后统一换成 id
        id_map[t["name"]] = (c.lastrowid, t.get("deps", []))
    for _name, (tid, deps) in id_map.items():
        dep_ids = [str(id_map[d][0]) for d in deps if d in id_map]
        if dep_ids:
            conn.execute("UPDATE tasks SET depends_on=? WHERE id=?",
                         (",".join(dep_ids), tid))
    conn.commit()
    conn.close()
    return pid


def get_project(pid):
    conn = get_conn()
    row = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def delete_project(pid):
    conn = get_conn()
    conn.execute("DELETE FROM tasks WHERE project_id=?", (pid,))
    conn.execute("DELETE FROM project_stages WHERE project_id=?", (pid,))
    conn.execute("DELETE FROM projects WHERE id=?", (pid,))
    conn.commit()
    conn.close()


# ---------------- 项目阶段 ----------------

def list_stages(pid):
    """项目自定义阶段（有序）。老项目无记录时返回 []，UI 回退到内置阶段。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT name FROM project_stages WHERE project_id=? "
        "ORDER BY order_idx, id", (pid,)).fetchall()
    conn.close()
    return [r["name"] for r in rows]


def add_stage(pid, name):
    """新增阶段（重名忽略），排到列表末尾。"""
    name = (name or "").strip()
    if not name:
        return
    conn = get_conn()
    conn.execute(
        "INSERT OR IGNORE INTO project_stages (project_id, name, order_idx) "
        "VALUES (?,?, (SELECT COALESCE(MAX(order_idx), -1) + 1 "
        "FROM project_stages WHERE project_id=?))", (pid, name, pid))
    conn.commit()
    conn.close()


def set_stage_order(pid, names):
    """按给定顺序整体重排阶段。"""
    conn = get_conn()
    for i, n in enumerate(names):
        conn.execute(
            "UPDATE project_stages SET order_idx=? WHERE project_id=? AND name=?",
            (i, pid, n))
    conn.commit()
    conn.close()


def rename_stage(pid, old, new):
    """重命名阶段，并同步该阶段下所有任务的 stage 字段。"""
    conn = get_conn()
    conn.execute("UPDATE project_stages SET name=? WHERE project_id=? AND name=?",
                 (new, pid, old))
    conn.execute("UPDATE tasks SET stage=? WHERE project_id=? AND stage=?",
                 (new, pid, old))
    conn.commit()
    conn.close()


def delete_stage(pid, name):
    """删除阶段记录（不删任务；该阶段下的任务会归到"未分组"式回退显示）。"""
    conn = get_conn()
    conn.execute("DELETE FROM project_stages WHERE project_id=? AND name=?",
                 (pid, name))
    conn.commit()
    conn.close()


# ---------------- 自定义流程模板 ----------------

def list_templates():
    """自定义模板列表（按名称排序，含任务明细）。内置模板不在此表。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, name, note, created_at, data FROM templates "
        "ORDER BY name").fetchall()
    conn.close()
    out = []
    for r in rows:
        try:
            tasks = json.loads(r["data"])
        except ValueError:
            tasks = []
        out.append({"id": r["id"], "name": r["name"], "note": r["note"],
                    "created_at": r["created_at"], "tasks": tasks})
    return out


def get_template(tid):
    conn = get_conn()
    row = conn.execute("SELECT * FROM templates WHERE id=?", (tid,)).fetchone()
    conn.close()
    if not row:
        return None
    try:
        tasks = json.loads(row["data"])
    except ValueError:
        tasks = []
    return {"id": row["id"], "name": row["name"], "note": row["note"],
            "created_at": row["created_at"], "tasks": tasks}


def save_template(name, tasks, note=""):
    """按名称保存模板（同名覆盖更新）。

    tasks: [{stage, name, owner, note, deps:[依赖任务名]}]
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("模板名称不能为空")
    payload = [{"stage": t.get("stage", ""), "name": t.get("name", ""),
                "owner": t.get("owner", ""), "note": t.get("note", ""),
                "deps": list(t.get("deps", []))} for t in tasks]
    conn = get_conn()
    conn.execute(
        "INSERT INTO templates (name, note, data) VALUES (?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET "
        "note=excluded.note, data=excluded.data",
        (name, note, json.dumps(payload, ensure_ascii=False)))
    conn.commit()
    conn.close()


def rename_template(tid, new_name):
    new_name = (new_name or "").strip()
    if not new_name:
        raise ValueError("模板名称不能为空")
    conn = get_conn()
    conn.execute("UPDATE templates SET name=? WHERE id=?", (new_name, tid))
    conn.commit()
    conn.close()


def delete_template(tid):
    conn = get_conn()
    conn.execute("DELETE FROM templates WHERE id=?", (tid,))
    conn.commit()
    conn.close()


# ---------------- 任务 ----------------

TASK_FIELDS = ["stage", "name", "owner", "status", "progress", "plan_start",
               "plan_end", "actual_start", "actual_end", "depends_on",
               "note", "file_link", "order_idx"]


def list_tasks(pid):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE project_id=? ORDER BY order_idx, id",
        (pid,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_task(pid, task):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO tasks (project_id, stage, name, owner, status, progress,"
        " plan_start, plan_end, actual_start, actual_end, depends_on, note,"
        " file_link, order_idx) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (pid, task.get("stage", ""), task.get("name", "新任务"),
         task.get("owner", ""), task.get("status", STATUS_PENDING),
         task.get("progress", 0), task.get("plan_start", ""),
         task.get("plan_end", ""), task.get("actual_start", ""),
         task.get("actual_end", ""), task.get("depends_on", ""),
         task.get("note", ""), task.get("file_link", ""),
         task.get("order_idx", 999)))
    conn.commit()
    tid = cur.lastrowid
    conn.close()
    return tid


def update_task(tid, field, value):
    if field not in TASK_FIELDS:
        raise ValueError("illegal field: %s" % field)
    conn = get_conn()
    conn.execute("UPDATE tasks SET %s=? WHERE id=?" % field, (value, tid))
    conn.commit()
    conn.close()


def delete_task(tid):
    """删除任务，并清掉其它任务 depends_on 里对它的引用。

    depends_on 是逗号分隔的 id 文本，没有外键约束；不清理的话剩下的任务
    会指向一个已不存在的 id，流程图/甘特图找节点时找不到，依赖列会出现
    空名字，卡点判断也会失准。
    """
    conn = get_conn()
    conn.execute("DELETE FROM tasks WHERE id=?", (tid,))
    token = str(tid)
    rows = conn.execute(
        "SELECT id, depends_on FROM tasks WHERE depends_on LIKE ?",
        ("%" + token + "%",)).fetchall()
    for r in rows:
        parts = [p.strip() for p in (r["depends_on"] or "").split(",") if p.strip()]
        kept = [p for p in parts if p != token]
        if kept != parts:
            conn.execute("UPDATE tasks SET depends_on=? WHERE id=?",
                         (",".join(kept), r["id"]))
    conn.commit()
    conn.close()


def task_dep_ids(task):
    raw = (task.get("depends_on") or "").strip()
    if not raw:
        return []
    out = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out


def today_str():
    return date.today().isoformat()


def _parse(s):
    try:
        return date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def analyze_blockers(tasks):
    """返回每个任务的卡点列表: {task_id: [原因...]}"""
    by_id = {t["id"]: t for t in tasks}
    blockers = {}
    t_today = date.today()
    for t in tasks:
        reasons = []
        if t["status"] == STATUS_BLOCKED:
            reasons.append("人工标记受阻")
        if t["status"] == STATUS_REWORK:
            reasons.append("返工中")
        if t["status"] in (STATUS_PENDING, STATUS_DOING):
            for did in task_dep_ids(t):
                dep = by_id.get(did)
                if dep and dep["status"] != STATUS_DONE:
                    reasons.append("依赖未完成: %s" % dep["name"])
            pend = _parse(t["plan_end"])
            if pend and pend < t_today and t["status"] != STATUS_DONE:
                reasons.append("已逾期 (计划 %s)" % t["plan_end"])
        if reasons:
            blockers[t["id"]] = reasons
    return blockers


def project_progress(tasks):
    """总进度 = 各任务进度均值。"""
    if not tasks:
        return 0
    return round(sum(int(t["progress"] or 0) for t in tasks) / len(tasks))


# ---------------- 批次个体（unit） ----------------

UNIT_FIELDS = ["sn", "name", "note", "order_idx"]


def _next_unit_order(pid):
    conn = get_conn()
    n = conn.execute(
        "SELECT COALESCE(MAX(order_idx), -1) + 1 FROM units WHERE project_id=?",
        (pid,)).fetchone()[0]
    conn.close()
    return n


def create_units(pid, count, sn_prefix="SN-", start_no=1):
    """批量创建批次个体，SN = 前缀 + 两位编号（SN-01 ... SN-10）。"""
    conn = get_conn()
    start = _next_unit_order(pid)
    ids = []
    for i in range(count):
        c = conn.execute(
            "INSERT INTO units (project_id, sn, name, note, order_idx) "
            "VALUES (?,?,?,?,?)",
            (pid, "%s%02d" % (sn_prefix, start_no + i), "", "", start + i))
        ids.append(c.lastrowid)
    conn.commit()
    conn.close()
    return ids


def add_unit(pid, sn, name="", note=""):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO units (project_id, sn, name, note, order_idx) "
        "VALUES (?,?,?,?,?)",
        (pid, sn, name, note, _next_unit_order(pid)))
    conn.commit()
    uid = cur.lastrowid
    conn.close()
    return uid


def list_units(pid):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM units WHERE project_id=? ORDER BY order_idx, id",
        (pid,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_unit(uid, field, value):
    if field not in UNIT_FIELDS:
        raise ValueError("illegal field: %s" % field)
    conn = get_conn()
    conn.execute("UPDATE units SET %s=? WHERE id=?" % field, (value, uid))
    conn.commit()
    conn.close()


def delete_unit(uid):
    conn = get_conn()
    conn.execute("DELETE FROM unit_task_status WHERE unit_id=?", (uid,))
    conn.execute("DELETE FROM units WHERE id=?", (uid,))
    conn.commit()
    conn.close()


def get_unit_statuses(pid):
    """{unit_id: {task_id: status}}，未记录的默认 pending。"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT s.unit_id, s.task_id, s.status FROM unit_task_status s "
        "JOIN units u ON s.unit_id = u.id WHERE u.project_id=?",
        (pid,)).fetchall()
    conn.close()
    out = {}
    for r in rows:
        out.setdefault(r["unit_id"], {})[r["task_id"]] = r["status"]
    return out


def set_unit_status(uid, tid, status):
    if status not in STATUS_NAMES:
        raise ValueError("illegal status: %s" % status)
    conn = get_conn()
    conn.execute(
        "INSERT INTO unit_task_status (unit_id, task_id, status, updated_at) "
        "VALUES (?,?,?, datetime('now','localtime')) "
        "ON CONFLICT(unit_id, task_id) DO UPDATE SET "
        "status=excluded.status, updated_at=excluded.updated_at",
        (uid, tid, status))
    conn.commit()
    conn.close()


def unit_stats(pid, tasks=None, statuses=None):
    """每个体的统计：进度、完成数、当前停在、受阻/返工清单。"""
    tasks = tasks if tasks is not None else list_tasks(pid)
    statuses = statuses if statuses is not None else get_unit_statuses(pid)
    units = list_units(pid)
    out = []
    for u in units:
        st = statuses.get(u["id"], {})
        done = sum(1 for t in tasks if st.get(t["id"]) == STATUS_DONE)
        first_undone = None
        issues = []
        for t in tasks:
            s = st.get(t["id"], STATUS_PENDING)
            if s in (STATUS_BLOCKED, STATUS_REWORK):
                issues.append("%s（%s）" % (t["name"], STATUS_NAMES[s]))
            if first_undone is None and s != STATUS_DONE:
                first_undone = t["name"]
        if first_undone is None:
            current = "全部完成" if tasks else "—"
        else:
            current = first_undone
        out.append({"unit": u, "done": done,
                    "progress": round(done * 100 / len(tasks)) if tasks else 0,
                    "current": current, "issues": issues})
    return out


def sync_task_progress(pid):
    """有个体的项目：任务进度/状态由矩阵自动计算。

    进度 = 完成该任务的个体比例；状态：全部个体完成→done，
    部分完成或有个体在做/返工/受阻→doing，否则 pending。
    无个体的项目不动（保留手填模式）。
    """
    units = list_units(pid)
    if not units:
        return
    tasks = list_tasks(pid)
    statuses = get_unit_statuses(pid)
    conn = get_conn()
    for t in tasks:
        sts = [statuses.get(u["id"], {}).get(t["id"], STATUS_PENDING)
               for u in units]
        done = sum(1 for s in sts if s == STATUS_DONE)
        if done == len(units):
            st = STATUS_DONE
        elif done > 0 or any(s in (STATUS_DOING, STATUS_REWORK, STATUS_BLOCKED)
                             for s in sts):
            st = STATUS_DOING
        else:
            st = STATUS_PENDING
        conn.execute("UPDATE tasks SET progress=?, status=? WHERE id=?",
                     (round(done * 100 / len(units)), st, t["id"]))
    conn.commit()
    conn.close()


# ---------------- 全量导出 / 导入（跨电脑迁移 · 预留同步接口） ----------------

SCHEMA_VERSION = 2


def export_all(path):
    """全量导出所有项目（任务/依赖/个体/矩阵状态）+ 自定义模板为单个 JSON。

    依赖与矩阵状态用任务 order_idx 表达，跨库导入时重新映射，
    不受两台机器自增 id 不同的影响。返回导出数据 dict。
    """
    data = {"app": "ProjectManager", "schema_version": SCHEMA_VERSION,
            "exported_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "projects": [],
            "templates": [{"name": t["name"], "note": t["note"],
                           "tasks": t["tasks"]}
                          for t in list_templates()]}
    for p in list_projects():
        tasks = list_tasks(p["id"])
        idx_of = {t["id"]: t["order_idx"] for t in tasks}
        task_export = []
        for t in tasks:
            deps = [str(idx_of[d]) for d in task_dep_ids(t) if d in idx_of]
            task_export.append({
                "stage": t["stage"], "name": t["name"], "owner": t["owner"],
                "status": t["status"], "progress": int(t["progress"] or 0),
                "plan_start": t["plan_start"], "plan_end": t["plan_end"],
                "actual_start": t["actual_start"], "actual_end": t["actual_end"],
                "depends_on": ",".join(deps), "note": t["note"],
                "file_link": t["file_link"], "order_idx": t["order_idx"]})
        statuses = get_unit_statuses(p["id"])
        unit_export = []
        for u in list_units(p["id"]):
            st = statuses.get(u["id"], {})
            st_map = {str(idx_of[tid]): s for tid, s in st.items()
                      if tid in idx_of}
            unit_export.append({"sn": u["sn"], "name": u["name"],
                                "note": u["note"], "order_idx": u["order_idx"],
                                "statuses": st_map})
        data["projects"].append({
            "name": p["name"], "code": p["code"], "template": p["template"],
            "note": p["note"], "tasks": task_export, "units": unit_export})
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return data


def import_all(path, merge=True):
    """导入 export_all 的产物。同名（名称+编号）项目跳过，不覆盖；
    自定义模板按名称去重后并入。返回 (新增项目数, 跳过项目数)。
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    existing = {(p["name"], p["code"]) for p in list_projects()}
    added = skipped = 0
    for pj in data.get("projects", []):
        key = (pj.get("name", ""), pj.get("code") or "")
        if merge and key in existing:
            skipped += 1
            continue
        _import_project(pj)
        existing.add(key)
        added += 1
    # 自定义模板（旧版导出文件无 templates 字段，跳过即可）
    have_tpl = {t["name"] for t in list_templates()}
    for tp in data.get("templates", []):
        name = (tp.get("name") or "").strip()
        if name and name not in have_tpl:
            save_template(name, tp.get("tasks", []), tp.get("note", ""))
            have_tpl.add(name)
    return added, skipped


def _import_project(pj):
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO projects (name, code, template, note) VALUES (?,?,?,?)",
        (pj.get("name", ""), pj.get("code") or "",
         pj.get("template") or "", pj.get("note") or ""))
    pid = cur.lastrowid
    # 按导入任务的出现顺序播种项目阶段列表
    stages = []
    for t in sorted(pj.get("tasks", []), key=lambda x: x.get("order_idx", 0)):
        s = (t.get("stage") or "").strip()
        if s and s not in stages:
            stages.append(s)
    for i, s in enumerate(stages):
        conn.execute("INSERT OR IGNORE INTO project_stages "
                     "(project_id, name, order_idx) VALUES (?,?,?)", (pid, s, i))
    id_by_idx = {}
    for t in sorted(pj.get("tasks", []),
                    key=lambda x: x.get("order_idx", 0)):
        c = conn.execute(
            "INSERT INTO tasks (project_id, stage, name, owner, status, progress,"
            " plan_start, plan_end, actual_start, actual_end, depends_on, note,"
            " file_link, order_idx) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, t.get("stage", ""), t.get("name", ""),
             t.get("owner", ""), t.get("status", STATUS_PENDING),
             int(t.get("progress") or 0), t.get("plan_start", ""),
             t.get("plan_end", ""), t.get("actual_start", ""),
             t.get("actual_end", ""), "", t.get("note", ""),
             t.get("file_link", ""), t.get("order_idx", 0)))
        id_by_idx[t.get("order_idx", 0)] = c.lastrowid
    # 依赖：导出文件里的 order_idx → 本库新 id
    for t in pj.get("tasks", []):
        deps = [p.strip() for p in (t.get("depends_on") or "").split(",")
                if p.strip()]
        ids = [str(id_by_idx[int(d)]) for d in deps
               if d.isdigit() and int(d) in id_by_idx]
        if ids:
            conn.execute("UPDATE tasks SET depends_on=? WHERE id=?",
                         (",".join(ids), id_by_idx[t.get("order_idx", 0)]))
    # 个体 + 矩阵状态
    for u in pj.get("units", []):
        cu = conn.execute(
            "INSERT INTO units (project_id, sn, name, note, order_idx) "
            "VALUES (?,?,?,?,?)",
            (pid, u.get("sn", ""), u.get("name", ""), u.get("note", ""),
             u.get("order_idx", 0)))
        uid = cu.lastrowid
        for idx_str, s in (u.get("statuses") or {}).items():
            try:
                tid = id_by_idx.get(int(idx_str))
            except (TypeError, ValueError):
                continue
            if tid and s in STATUS_NAMES:
                conn.execute(
                    "INSERT OR REPLACE INTO unit_task_status "
                    "(unit_id, task_id, status) VALUES (?,?,?)",
                    (uid, tid, s))
    conn.commit()
    conn.close()
    return pid
