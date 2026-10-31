"""ProjectManager 正式构建脚本（PyInstaller onedir，对齐 ForceStudio 打包方式）。

流程：
1. 从 version.py 读取 APP_NAME / APP_VERSION（唯一版本源）
2. 确定本次构建的**版本标识**：正式版 1.3.5 / 调试版 1.3.5-debug.2
3. 生成 Windows 版本信息资源文件（build/version_info_<标识>.txt）
4. 确保 resources/app.ico 存在（缺失时纯 Python 生成 PM 图标）
5. PyInstaller --onedir 构建 → dist/ProjectManager/（调试版 → dist-debug/）

用法：
    .venv/Scripts/python.exe scripts/build.py                 # 正式版
    .venv/Scripts/python.exe scripts/build.py --debug         # 调试版（自动取下一个调试号）
    .venv/Scripts/python.exe scripts/build.py --debug --debug-number 3
    （或双击 build.bat / build.bat --debug）

调试序号从哪来：release\\ 目录里已出的 ProjectManager_Setup_<版本>-debug.<N>.exe
就是台账，下一个可用的号 = 已出的最大号 + 1（没有则从 1 开始）。
不需要额外的计数器文件，也不会因为换机器而错号。

产物：dist/ProjectManager/（ProjectManager.exe + _internal/）
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent      # scripts/
ROOT = HERE.parent                           # 项目根
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from version import (  # noqa: E402
    APP_NAME,
    APP_VERSION,
    is_debug_version,
    make_debug_version,
    numeric_version,
)
from make_icon import ensure_icon  # noqa: E402

ICON_FILE = ROOT / "resources" / "app.ico"
RELEASE_DIR = ROOT / "release"

# 安装包文件名台账：ProjectManager_Setup_<正式版>-debug.<序号>.exe
# （正则故意不锚定结尾：历史上带 _Debug 后缀的包也能被正确识别为台账记录）
_DEBUG_PKG_RE = re.compile(
    r"^%s_Setup_(\d+\.\d+\.\d+)-debug\.(\d+)" % APP_NAME)

# 镜像回退链：阿里云优先（实测兼容所有 pip 版本），
# 清华源在 pip>=26.1.2 + 部分网络下会返回空索引（from versions: none），仅作回退。
PIP_MIRRORS = [
    "https://mirrors.aliyun.com/pypi/simple/",
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://pypi.org/simple",
]


# ---------------- 版本标识（正式版 / 调试版）----------------

def debug_numbers_in_release(base_version: str) -> list[int]:
    """release/ 里该正式版已经出过的调试序号（升序）。

    以安装包文件名为台账：既不用维护额外状态文件，也天然按正式版分组
    （换正式版号后调试号自动从 1 重新开始）。
    """
    base = (base_version or "").strip()
    nums: list[int] = []
    if not base or not Path(RELEASE_DIR).is_dir():
        return nums
    for p in Path(RELEASE_DIR).iterdir():
        m = _DEBUG_PKG_RE.match(p.name)
        if m and m.group(1) == base:
            nums.append(int(m.group(2)))
    return sorted(set(nums))


def suggest_debug_number(base_version: str) -> int:
    """下一个可用的调试序号 = 已出的最大号 + 1（一次都没出过 → 1）。"""
    nums = debug_numbers_in_release(base_version)
    return (max(nums) + 1) if nums else 1


def default_version_label(debug: bool) -> str:
    """没显式指定版本标识时的缺省值。

    正式版 = version.py 当前版本号；
    调试版 = 当前正式版号 + 下一个可用调试号（release/ 目录里没出过的号）。
    """
    if not debug:
        return APP_VERSION
    return make_debug_version(APP_VERSION, suggest_debug_number(APP_VERSION))


def ensure_pyinstaller() -> None:
    """构建前确保 pyinstaller 可用，缺失时经镜像回退链安装。"""
    try:
        import PyInstaller  # noqa: F401
        return
    except ImportError:
        pass
    import subprocess
    req = ROOT / "requirements-build.txt"
    for mirror in PIP_MIRRORS:
        print(f">>> 安装构建依赖（{mirror}）...")
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(req),
             "--disable-pip-version-check", "-i", mirror],
            cwd=str(ROOT),
        )
        if r.returncode == 0:
            return
    raise SystemExit("ERROR: pyinstaller 安装失败，请检查网络。")


def make_version_file(debug: bool = False,
                      version_label: str | None = None) -> Path:
    """生成 Windows 版本信息资源文件。

    version_label 是本次构建的完整版本标识（正式版 1.3.5 / 调试版 1.3.5-debug.2），
    缺省按 debug 开关现算。版本资源里两套写法各司其职：
      · FileVersion / ProductVersion → 完整标识（人看的，带 -debug.N）
      · filevers / prodvers          → 4 段纯数字（Windows 要求的），
                                       调试序号落在第 4 段，供"文件版本"区分钟
    """
    label = version_label or default_version_label(debug)
    version_file = version_file_path(label)
    num = numeric_version(label)
    display = (APP_NAME + " Debug") if debug else APP_NAME
    from PyInstaller.utils.win32.versioninfo import (
        VSVersionInfo,
        FixedFileInfo,
        StringFileInfo,
        StringTable,
        StringStruct,
        VarFileInfo,
        VarStruct,
    )

    info = VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=num,
            prodvers=num,
            mask=0x3F,
            flags=0x0,
            OS=0x40004,          # VOS__WINDOWS32
            fileType=0x1,        # VFT_APP
            subtype=0x0,
            date=(0, 0),
        ),
        kids=[
            StringFileInfo([
                StringTable(
                    "040904B0",  # en-US + UTF-16
                    [
                        StringStruct("CompanyName", "SensorTools"),
                        StringStruct("FileDescription", display),
                        StringStruct("FileVersion", label),
                        StringStruct("InternalName", APP_NAME),
                        StringStruct("LegalCopyright", "Copyright (C) 2026"),
                        StringStruct("OriginalFilename", APP_NAME + ".exe"),
                        StringStruct("ProductName", display),
                        StringStruct("ProductVersion", label),
                    ],
                )
            ]),
            VarFileInfo([VarStruct("Translation", [0x0409, 0x04B0])]),
        ],
    )
    version_file.parent.mkdir(parents=True, exist_ok=True)
    version_file.write_text(str(info), encoding="utf-8")
    return version_file


def version_file_path(version_label: str | None = None) -> Path:
    """版本资源文件路径，按版本标识命名。

    必须在调用时现算：release.py 会先把 version.py 改成新版本号再构建，
    若在模块导入时就把路径写死，文件名会停留在旧版本（内容却是新的）。
    """
    return ROOT / "build" / f"version_info_{version_label or APP_VERSION}.txt"


def make_variant_file(debug: bool = False,
                      version_label: str | None = None) -> Path:
    """生成构建变体标记与版本标识文件（随包带到 _internal/resources/）。

    返回 variant.txt 路径（与它同目录的 build_version.txt 一并被 --add-data 带上）。
        variant.txt       release / debug    → app/paths.py 判定身份，数据目录随之分开
        build_version.txt 本次完整版本标识   → 界面标题/关于对话框显示
                                              （如 1.3.5-debug.2）
    """
    label = version_label or default_version_label(debug)
    vdir = ROOT / "build" / "variant"
    vdir.mkdir(parents=True, exist_ok=True)
    f = vdir / "variant.txt"
    f.write_text("debug\n" if debug else "release\n", encoding="utf-8")
    (vdir / "build_version.txt").write_text(label + "\n", encoding="utf-8")
    return f


def build(version_file: Path | None = None, icon_file: Path | None = None,
          dist_path: Path | None = None, work_path: Path | None = None,
          debug: bool = False, version_label: str | None = None) -> int:
    """执行 PyInstaller onedir 构建，返回退出码。

    dist_path / work_path 缺省为 <项目根>/dist、<项目根>/build；
    debug=True 时为调试版：带控制台窗口（报错/崩溃日志直接可见），
    输出目录缺省改为 <项目根>/dist-debug，避免覆盖正式版产物。
    """
    from PyInstaller.__main__ import run

    label = version_label or default_version_label(debug)
    version_file = version_file or make_version_file(debug, label)
    icon_file = icon_file or ensure_icon()
    variant_file = make_variant_file(debug, label)
    dist_path = dist_path or (ROOT / ("dist-debug" if debug else "dist"))
    work_path = work_path or (ROOT / "build")
    argv = [
        str(ROOT / "run.py"),         # 入口（绝对路径，任意 cwd 下可构建）
        "--name", APP_NAME,
        "--onedir",                    # 目录结构（更新/安装友好，不用 onefile）
        # 调试版带控制台（异常/崩溃日志可见），正式版无控制台
        "--console" if debug else "--windowed",
        "--noupx",                     # 不压缩，规避杀软误报
        "--clean",
        "--noconfirm",                 # dist 已有产物时直接覆盖，不询问
        "--version-file", str(version_file),
        "--icon", str(icon_file),
        # 随包带上图标：安装版运行时从 sys._MEIPASS/resources 取（app/paths.py）
        "--add-data", "%s;resources" % (ROOT / "resources"),
        # 构建变体标记（调试版 → 独立数据目录，见 app/paths.py）
        "--add-data", "%s;resources" % variant_file,
        # 本次构建的完整版本标识（界面显示 v1.3.5-debug.2）
        "--add-data", "%s;resources" % (variant_file.parent / "build_version.txt"),
        "--paths", str(ROOT),          # 确保 app 包可被分析到
        "--specpath", str(ROOT / "build"),   # spec 生成物放 build/，不动仓库根
        "--distpath", str(dist_path),
        "--workpath", str(work_path),
    ]
    print(">>> 版本标识：v%s" % label)
    print(">>> PyInstaller argv:", argv)
    run(argv)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ProjectManager 构建（PyInstaller onedir）")
    parser.add_argument("--distpath", help="输出目录（缺省 <项目根>/dist）")
    parser.add_argument("--workpath", help="中间目录（缺省 <项目根>/build）")
    parser.add_argument("--debug", action="store_true",
                        help="调试版：带控制台窗口（报错/崩溃日志可见），"
                             "输出到 dist-debug/ProjectManager/，"
                             "版本标识为 <正式版>-debug.<序号>")
    parser.add_argument("--debug-number", type=int, metavar="N",
                        help="调试序号（仅调试版有效；缺省 = release/ 里未出过的下一个号）")
    parser.add_argument("--version-label", metavar="LABEL",
                        help="显式指定完整版本标识（如 1.3.5-debug.2），需与 --debug 匹配")
    parser.add_argument("--skip-deps", action="store_true",
                        help="跳过 pyinstaller 自检安装")
    args = parser.parse_args()

    label = args.version_label
    if label:
        if is_debug_version(label) != bool(args.debug):
            print("!! --version-label 与 --debug 不匹配：%s / debug=%s"
                  % (label, args.debug), file=sys.stderr)
            return 2
    elif args.debug:
        label = make_debug_version(
            APP_VERSION,
            args.debug_number or suggest_debug_number(APP_VERSION))
    else:
        if args.debug_number:
            print("!! --debug-number 只对调试版有效（请同时加 --debug）", file=sys.stderr)
            return 2
        label = APP_VERSION

    if not args.skip_deps:
        ensure_pyinstaller()
    make_version_file(args.debug, label)
    ensure_icon()
    dist_path = Path(args.distpath) if args.distpath else None
    work_path = Path(args.workpath) if args.workpath else None
    kind = "调试版（带控制台）" if args.debug else "正式版"
    print(f">>> 构建 {APP_NAME} v{label} (onedir, {kind})...")
    build(dist_path=dist_path, work_path=work_path, debug=args.debug,
          version_label=label)
    out = (dist_path or (ROOT / ("dist-debug" if args.debug else "dist"))) / APP_NAME
    print(f">>> 构建完成：{out}")
    if not (out / f"{APP_NAME}.exe").is_file():
        print("!!! 未找到输出 exe，构建可能失败", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
