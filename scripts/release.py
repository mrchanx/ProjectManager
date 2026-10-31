"""ProjectManager 安装包生成脚本（release.bat 调用，不涉及 git）。

一条龙：
    1. 确定本次要出的版本标识
         正式版  1.3.5        （会写入 version.py，唯一版本源）
         调试版  1.3.5-debug.2（跟随正式版 1.3.5，**不改动 version.py**）
    2. PyInstaller onedir 构建 → dist/ProjectManager/（调试版 → dist-debug/）
    3. Inno Setup 编译 → release/ProjectManager_Setup_<版本标识>.exe
    4. 计算 SHA256 → 同名 .sha256

安装包文件名直接用完整版本标识，不再叠一个 _Debug 尾缀：
    正式版  ProjectManager_Setup_1.3.5.exe
    调试版  ProjectManager_Setup_1.3.5-debug.2.exe
标识本身已经含 "debug"，再加尾缀是冗余；而且文件名的排序/比对只认版本标识这一处，
少一层拼接就少一处拼错的机会（历史遗留的 _Debug 后缀仍能被台账正确识别）。

用法：
    .venv/Scripts/python.exe scripts/release.py                    # 交互：选类型→输入版本/调试号
    .venv/Scripts/python.exe scripts/release.py --version 1.3.5     # 正式版指定版本号
    .venv/Scripts/python.exe scripts/release.py --debug             # 调试版（自动取下一个调试号）
    .venv/Scripts/python.exe scripts/release.py --debug --debug-number 3
    .venv/Scripts/python.exe scripts/release.py --skip-build        # 复用现有 dist，只打安装包
    .venv/Scripts/python.exe scripts/release.py --distpath D:\\tmp\\dist
    （或双击 release.bat）

版本命名（SemVer 2.0.0，见 version.py）：
    正式版  MAJOR.MINOR.PATCH            → 1.3.5
    调试版  MAJOR.MINOR.PATCH-debug.N    → 1.3.5-debug.2
    调试版出包时**只问调试序号 N**，版本号自动拼成 <正式版>-debug.<N>；
    N 的缺省值 = release/ 里该正式版已出的最大号 + 1（安装包文件名即台账）。

关于"覆盖安装数据不会丢"（安装包本身保证）：
    · 程序文件装到 {autopf}\\ProjectManager（Program Files）
    · 用户数据在 %LOCALAPPDATA%\\ProjectManager\\data\\
        projects.db（项目/任务/个体矩阵）、settings.ini、reports\\ 都在这里
    · installer/ProjectManager.iss 只写 {app}，从不包含也不删除用户数据目录
    · 同 AppId 覆盖安装 + ignoreversion → 程序文件全量更新，数据原样保留

正式版 / 调试版并存（--debug）：
    · 调试版用另一套 AppId + 安装目录（{autopf}\\ProjectManager Debug）
    · 数据目录也是 %LOCALAPPDATA%\\ProjectManager Debug\\data
      （app/paths.py 读构建时塞进 resources\\variant.txt 的标记判定）
    · 因此两套可以同时安装、同时打开，互不覆盖、各用各的数据
    · 两套之间搬数据：程序内 文件 → 导出全部数据 / 导入数据
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent      # scripts/
ROOT = HERE.parent                           # 项目根
sys.path.insert(0, str(ROOT))

import scripts.build as build_mod  # noqa: E402
from version import (  # noqa: E402
    APP_NAME,
    base_version_of,
    is_release_version,
    make_debug_version,
    numeric_version_string,
)

VERSION_PY = ROOT / "version.py"
ISS_FILE = ROOT / "installer" / "ProjectManager.iss"
RELEASE_DIR = ROOT / "release"

ISCC_CANDIDATES = [
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
]

# 正式版 / 调试版各用一套身份 → 可同时安装、互不覆盖、各有卸载项。
# 数据目录也随之分开：%LOCALAPPDATA%\ProjectManager 与 ...\ProjectManager Debug
# （由 app/paths.py 读 resources\variant.txt 判定，跟安装目录保持一致）。
RELEASE_APP_ID = "{8F3A6C51-2D74-4B9E-A1C8-7E5B90D42F63}"
DEBUG_APP_ID = "{B7C41E92-5A38-4F0D-9C6E-2D8F13A7E504}"
DEBUG_APP_TITLE = "ProjectManager Debug"

_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def current_version() -> str:
    """始终从 version.py 现场读取（唯一版本源）。"""
    mod = importlib.import_module("version")
    importlib.reload(mod)
    return mod.APP_VERSION


def find_iscc() -> Path:
    for p in ISCC_CANDIDATES:
        if p.is_file():
            return p
    raise RuntimeError(
        "未找到 Inno Setup（ISCC.exe）。请先安装 Inno Setup 6："
        "https://jrsoftware.org/isdl.php"
    )


def setup_output_name(version_label: str, debug: bool = False) -> str:
    """安装包文件名：ProjectManager_Setup_<版本标识>.exe。

    debug 参数只为兼容旧调用保留，不再参与命名 —— 调试标识 1.3.5-debug.2
    本身已经能和正式版 1.3.5 区分开，无需再加 _Debug 尾缀。

    必须与 installer/ProjectManager.iss 的
    OutputBaseFilename=ProjectManager_Setup_{#MyAppVersion}
    保持一致（iss 里版本标识由 /DMyAppVersion 注入）。
    """
    return "%s_Setup_%s.exe" % (APP_NAME, version_label)


def setup_path(version_label: str, debug: bool = False) -> Path:
    return RELEASE_DIR / setup_output_name(version_label, debug)


def ask_version(cur: str) -> str:
    """交互输入版本号；直接回车 = 沿用当前版本（重新打包）。"""
    print("当前版本：%s" % cur)
    while True:
        try:
            raw = input("请输入新版本号（MAJOR.MINOR.PATCH，如 1.3.5；回车=沿用当前版本）：").strip()
        except EOFError:
            return cur
        if not raw:
            return cur
        if _VERSION_RE.match(raw):
            return raw
        print("!! 版本号格式应为 1.3.0 / 1.3.5 等（仅数字与点）。")


def ask_debug() -> bool:
    """交互选择构建类型：1=正式版（默认，无控制台） 2=调试版（带控制台）。"""
    try:
        raw = input("构建类型：1=正式版（默认）  2=调试版（带控制台，报错/崩溃日志可见）[1]：").strip()
    except EOFError:
        return False
    return raw == "2"


def ask_debug_number(base: str, default: int) -> int:
    """调试版：**只**问调试序号，完整版本号由程序自己拼。

    正式版号不动，调试版永远挂在它下面，所以这里不该再让用户输一遍 1.3.5。
    """
    print("-" * 62)
    print("调试版版本号 = 正式版 %s + debug 尾缀 + 调试序号" % base)
    print("  例：序号 2 → %s" % make_debug_version(base, 2))
    print("  （调试版不修改 version.py，始终跟随正式版 %s）" % base)
    while True:
        try:
            raw = input("调试序号 [%d]：" % default).strip()
        except EOFError:
            return default
        if not raw:
            n = default
        elif raw.isdigit() and int(raw) >= 1:
            n = int(raw)
        else:
            print("!! 这里只填序号（正整数），如 1 / 2 / 3。")
            continue
        label = make_debug_version(base, n)
        pkg = setup_path(label, True)
        if pkg.is_file():
            print("!! 已存在同号安装包：%s" % pkg.name)
            try:
                ans = input("   覆盖它吗？(y/N)：").strip().lower()
            except EOFError:
                ans = ""
            if ans != "y":
                continue
        return n


def update_version_py(new_version: str) -> bool:
    """把 version.py 的 APP_VERSION 替换为新版本，返回是否真的改了。"""
    text = VERSION_PY.read_text(encoding="utf-8")
    new_text = re.sub(r'(APP_VERSION\s*=\s*")[^"]*(")',
                      rf"\g<1>{new_version}\g<2>", text, count=1)
    if new_text == text:
        raise RuntimeError("version.py 中未找到 APP_VERSION 定义，无法更新")
    VERSION_PY.write_text(new_text, encoding="utf-8")
    # scripts/build.py 在导入时就把常量取走了，这里要同步刷新，否则 exe 版本资源还是旧的
    mod = importlib.import_module("version")
    importlib.reload(mod)
    build_mod.APP_VERSION = mod.APP_VERSION
    print(">>> version.py 已更新为 %s" % new_version)
    return True


def run_cmd(cmd: list[str]) -> None:
    print(">>>", " ".join(str(c) for c in cmd))
    result = subprocess.run(cmd, cwd=str(ROOT), check=False)
    if result.returncode != 0:
        raise RuntimeError("命令失败（exit=%d）：%s"
                           % (result.returncode, " ".join(map(str, cmd))))


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 ProjectManager 安装包（可覆盖升级）")
    parser.add_argument("--version", help="正式版版本号（如 1.3.5）；"
                                          "调试版下表示「跟随的正式版号」，缺省交互输入")
    parser.add_argument("--base-version",
                        help="调试版跟随的正式版版本号（缺省 = version.py 当前值）")
    parser.add_argument("--debug", action="store_true",
                        help="调试版安装包（带控制台，报错/崩溃日志可见）；"
                             "构建产物在 dist-debug/，版本号为 <正式版>-debug.<序号>")
    parser.add_argument("--debug-number", type=int, metavar="N",
                        help="调试序号（隐含 --debug）；缺省 = release/ 里未出过的下一个号")
    parser.add_argument("--skip-build", action="store_true",
                        help="跳过 PyInstaller 构建（复用现有构建产物）")
    parser.add_argument("--distpath",
                        help="构建输出目录（缺省：正式 dist/，调试 dist-debug/）；"
                             "目录被正在运行的程序占用时可换目录出包")
    args = parser.parse_args()

    interactive = sys.stdin is not None and sys.stdin.isatty()
    debug = args.debug or bool(args.debug_number)
    if not args.debug and not args.debug_number and interactive:
        debug = ask_debug()

    cur = current_version()          # version.py 里的正式版版本号

    if debug:
        # ---- 调试版：只问序号，版本号 = <正式版>-debug.<N>，不动 version.py ----
        base = (args.base_version or args.version or cur).strip()
        if not is_release_version(base):
            sys.exit("!! 调试版跟随的正式版版本号格式错误（应为 MAJOR.MINOR.PATCH）：%s" % base)
        if args.version and args.version.strip() != cur:
            print(">>> 调试版跟随正式版 %s（调试版不修改 version.py）" % base)
        default_n = build_mod.suggest_debug_number(base)
        done = build_mod.debug_numbers_in_release(base)
        if done:
            print(">>> release/ 里 %s 已出过的调试号：%s"
                  % (base, ", ".join(map(str, done))))
        if args.debug_number:
            n = args.debug_number
            if n < 1:
                sys.exit("!! 调试序号必须 ≥ 1")
            if setup_path(make_debug_version(base, n), True).is_file():
                print(">>> 注意：将覆盖已存在的同号安装包 %s"
                      % setup_output_name(make_debug_version(base, n), True))
        elif interactive:
            n = ask_debug_number(base, default_n)
        else:
            n = default_n
            print(">>> 非交互环境，调试序号取 %d" % n)
        label = make_debug_version(base, n)
        new_version = base            # 调试版不写 version.py，只用于提示
    else:
        # ---- 正式版 ----
        if args.debug_number:
            sys.exit("!! --debug-number 只对调试版有效")
        if args.version:
            new_version = args.version.strip()
        elif interactive:
            new_version = ask_version(cur)
        else:
            new_version = cur
            print(">>> 非交互环境，正式版版本号取当前值 %s" % new_version)
        if not _VERSION_RE.match(new_version):
            sys.exit("版本号格式错误（应为 MAJOR.MINOR.PATCH）")
        label = new_version

    kind = "调试版（带控制台）" if debug else "正式版"
    print("=" * 62)
    print("  %s 安装包生成  v%s  [%s]" % (APP_NAME, label, kind))
    if debug:
        print("  版本标识：%s = 正式版 %s + debug.%d"
              % (label, base_version_of(label), n))
    print("=" * 62)

    if not debug and new_version != cur:
        update_version_py(new_version)
    if debug:
        print(">>> 调试版不修改 version.py（始终跟随正式版 %s，当前 %s）" % (base, cur))

    dist_dir = Path(args.distpath) if args.distpath else \
        ((ROOT / "dist-debug") if debug else (ROOT / "dist"))
    work_dir = (dist_dir.parent / "build") if args.distpath else (ROOT / "build")

    if args.skip_build:
        print(">>> [1/4] 跳过构建（复用 %s）..." % (dist_dir / APP_NAME))
    else:
        print(">>> [1/4] PyInstaller onedir 构建 v%s [%s] ..." % (label, kind))
        build_mod.ensure_pyinstaller()
        build_mod.build(dist_path=dist_dir, work_path=work_dir, debug=debug,
                        version_label=label)

    exe = dist_dir / APP_NAME / f"{APP_NAME}.exe"
    if not exe.is_file():
        sys.exit("!! 未找到构建产物：%s\n   请先运行构建（去掉 --skip-build）。" % exe)

    iscc = find_iscc()
    print(">>> [2/4] Inno Setup 编译安装包（%s）..." % iscc.name)
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [str(iscc),
           "/DMyAppVersion=%s" % label,
           # Inno 的 VersionInfoVersion 只吃 4 段纯数字：1.3.5.2（调试序号在第 4 段）
           "/DMyVersionInfo=%s" % numeric_version_string(label),
           "/DDistDir=%s" % dist_dir.resolve()]
    if debug:
        # 调试版换一套身份：独立 AppId / 标题 / 安装目录，
        # 与正式版并存安装、互不覆盖（数据目录也各自独立）。
        # 安装包名不再加 _Debug 尾缀：版本标识里的 -debug.N 已经足够区分。
        cmd += ["/DMyAppId=%s" % DEBUG_APP_ID,
                "/DMyAppTitle=%s" % DEBUG_APP_TITLE,
                "/DMyAppDir=%s" % DEBUG_APP_TITLE]
    else:
        cmd.append("/DMyAppId=%s" % RELEASE_APP_ID)
    cmd.append(str(ISS_FILE))
    run_cmd(cmd)

    setup = setup_path(label, debug)
    if not setup.is_file():
        sys.exit("!! 未生成安装包：%s" % setup)

    print(">>> [3/4] 计算 SHA256 ...")
    digest = sha256_of(setup)
    sha_file = RELEASE_DIR / (setup.name + ".sha256")
    sha_file.write_text("%s  %s\n" % (digest, setup.name), encoding="utf-8")

    print(">>> [4/4] 完成")
    print("-" * 62)
    print("  安装包：%s" % setup)
    print("  大小：  %.1f MB" % (setup.stat().st_size / 1024 / 1024))
    print("  SHA256：%s" % digest)
    print("  校验：  %s" % sha_file)
    print("-" * 62)
    app_dir = DEBUG_APP_TITLE if debug else APP_NAME
    print("数据安全说明（可直接双击新安装包覆盖升级本版本）：")
    print("  · 程序文件 → {app}\\%s（Program Files），升级时全量替换" % app_dir)
    print("  · 用户数据 → %%LOCALAPPDATA%%\\%s\\data（projects.db 等）" % app_dir)
    print("  · 安装 / 升级 / 卸载均不触碰用户数据目录，项目数据不会丢")
    print("-" * 62)
    if debug:
        print("调试版与正式版并存：两套 AppId / 安装目录 / 数据目录都独立，")
        print("  可同时安装、同时打开，互不覆盖；两套之间同步数据用")
        print("  程序内 文件 → 导出全部数据 / 导入数据。")
        print("  下次调试版序号：%d" % build_mod.suggest_debug_number(base))
    else:
        print("提示：再次运行 release.bat 选「调试版」，只输入调试序号即可，")
        print("      版本号自动为 %s-debug.<序号>（与正式版并存安装）。" % label)


if __name__ == "__main__":
    main()
