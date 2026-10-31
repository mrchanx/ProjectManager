# -*- coding: utf-8 -*-
"""路径解析：**程序文件**与**用户数据**彻底分离。

这是"覆盖安装数据不会丢"的关键：

    开发模式（run.bat / python run.py）
        程序 = 项目根          数据 = 项目根\\data\\        （与旧版本完全一致）

    安装版（ProjectManager_Setup_*.exe 装好后）
        程序 = {app}\\         即 C:\\Program Files\\ProjectManager\\  ← 升级时整目录被覆盖
        数据 = %LOCALAPPDATA%\\ProjectManager\\data\\              ← 安装/升级/卸载都不触碰

安装包只往 {app}\\ 写程序文件，从不包含也不删除数据目录，
所以双击新版安装包 = 覆盖升级，projects.db / settings.ini / reports 原样保留。

**调试版**（scripts/build.py --debug，带控制台）读 resources\\variant.txt 得知
自己的身份，数据目录换成 %LOCALAPPDATA%\\ProjectManager Debug\\data\\，
安装目录/AppId/快捷方式也另起一套（installer/ProjectManager.iss），
因此正式版与调试版可以**同时安装、同时打开**，互不覆盖、各用各的数据；
两套之间要搬数据时走 文件 → 导出全部数据 / 导入数据。

首次以安装版运行、且新位置还没有数据库时，会自动把旧位置
（exe 旁的 data\\ 或 _internal\\data\\）数据整体复制过来，旧文件保留不删。

本模块只依赖标准库，可被任何层安全导入（包括无 Qt 的测试环境）。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

APP_NAME = "ProjectManager"

IS_FROZEN = bool(getattr(sys, "frozen", False))


def _app_dir() -> Path:
    """程序所在目录：打包后 = exe 所在目录；开发 = 项目根。"""
    if IS_FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


APP_DIR = _app_dir()

if IS_FROZEN:
    # PyInstaller onedir：只读资源解包在 _MEIPASS，打包时用 --add-data 带进来
    _meipass = getattr(sys, "_MEIPASS", None)
    RESOURCE_DIR = (Path(_meipass) / "resources") if _meipass else (APP_DIR / "resources")
else:
    RESOURCE_DIR = APP_DIR / "resources"


def resource_path(*parts: str) -> str:
    """随程序分发的静态资源路径（图标等）。"""
    return str(RESOURCE_DIR.joinpath(*parts))


# ---------------- 构建变体：正式版 / 调试版（两套可同时安装，数据互不干扰）----------------

def _read_variant() -> str:
    """当前是哪个变体：release（默认）/ debug。

    打包时 scripts/build.py 会把 resources/variant.txt 一起塞进产物
    （调试版内容为 debug）。开发模式下该文件不存在 → release；
    调试开发环境可用环境变量 PM_VARIANT=debug 模拟。
    """
    env = os.environ.get("PM_VARIANT", "").strip().lower()
    if env in ("debug", "release"):
        return env
    try:
        f = RESOURCE_DIR / "variant.txt"
        if f.is_file():
            v = f.read_text(encoding="utf-8").strip().lower()
            if v in ("debug", "release"):
                return v
    except OSError:
        pass
    return "release"


VARIANT = _read_variant()
IS_DEBUG_BUILD = VARIANT == "debug"

# 数据目录归属名：调试版独立命名，装在同一台机器上也各用各的数据库
# （两套数据要合并时走 文件 → 导出全部数据 / 导入数据）。
DATA_OWNER = "%s Debug" % APP_NAME if IS_DEBUG_BUILD else APP_NAME
DISPLAY_NAME = DATA_OWNER
VARIANT_LABEL = "调试版" if IS_DEBUG_BUILD else "正式版"


# ---------------- 本次构建的版本标识（含调试尾缀）----------------

def _read_build_version() -> str:
    """本次构建的完整版本标识：正式版 1.3.5 / 调试版 1.3.5-debug.2。

    打包时 scripts/build.py 会把 resources\\build_version.txt 一起塞进产物
    （内容 = 本次构建的完整标识）；开发模式下**故意不读该文件**，
    直接回退 version.py 的 APP_VERSION —— 否则 dev 期改了版本号，
    界面还会显示上一次构建残留的旧标识。
    """
    if IS_FROZEN:
        try:
            f = RESOURCE_DIR / "build_version.txt"
            if f.is_file():
                v = f.read_text(encoding="utf-8").strip()
                if v:
                    return v
        except OSError:
            pass
    try:
        from version import APP_VERSION      # 项目根在 sys.path（run.py / 打包 --paths）
        return APP_VERSION
    except Exception:                         # noqa: BLE001 - 缺文件不影响启动
        return ""


# 界面标题 / 关于对话框用：v1.3.5 或 v1.3.5-debug.2
BUILD_VERSION = _read_build_version()


def _read_build_version_number() -> int | None:
    """调试版版本号里的调试序号（正式版返回 None）。"""
    try:
        from version import debug_number_of
    except Exception:                         # noqa: BLE001
        return None
    return debug_number_of(BUILD_VERSION)


DEBUG_NUMBER = _read_build_version_number()


def _user_data_root() -> Path:
    """用户数据根目录。

    安装版：%LOCALAPPDATA%\\<DATA_OWNER>（无该环境变量时退回用户主目录，
            保证一定可写——绝不能落进 Program Files，否则写入会被拒绝）。
    开发模式：项目根，保证既有数据与测试脚本照旧可用。
    """
    if IS_FROZEN:
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return Path(base) / DATA_OWNER
        return Path.home() / (".%s" % DATA_OWNER.lower().replace(" ", "-"))
    return APP_DIR


USER_DATA_ROOT = _user_data_root()
DATA_DIR = USER_DATA_ROOT / "data"
DB_PATH = str(DATA_DIR / "projects.db")
SETTINGS_PATH = str(DATA_DIR / "settings.ini")
REPORT_DIR = DATA_DIR / "reports"


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)


def _legacy_data_dirs() -> list[Path]:
    """安装版可能遗留旧数据的位置（exe 旁 / PyInstaller 的 _internal 里）。"""
    return [APP_DIR / "data", APP_DIR / "_internal" / "data"]


def migrate_legacy_data() -> str | None:
    """首次以安装版运行时，把旧位置的数据搬到用户数据目录。

    保守策略，绝不丢数据：
    - 仅打包后执行（开发模式不动，避免自搬自）。
    - 新位置已有 projects.db → 认为早就迁移过，直接跳过。
    - 旧位置有 projects.db → 整体目录复制（备份式，旧文件**保留不删**）。
    - 复制失败不抛异常（程序照常在空的新目录里启动），仅返回信息供界面提示。

    返回迁移结果描述；无迁移动作时返回 None。
    """
    if not IS_FROZEN:
        return None
    if (DATA_DIR / "projects.db").is_file():
        return None
    for old in _legacy_data_dirs():
        if not (old / "projects.db").is_file():
            continue
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            shutil.copytree(old, DATA_DIR, dirs_exist_ok=True)
        except Exception:  # noqa: BLE001 - 迁移失败不能阻塞启动
            return "旧数据迁移失败（已跳过，程序使用新数据目录）：%s" % old
        return "已从旧目录迁移数据：%s → %s" % (old, DATA_DIR)
    return None


MIGRATION_MESSAGE = migrate_legacy_data()
ensure_dirs()
