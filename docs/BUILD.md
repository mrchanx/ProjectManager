# 从源码构建

## 环境

- Windows
- Python 3.10+
- Inno Setup 6（打安装包用，[下载](https://jrsoftware.org/isdl.php)）

## 依赖镜像

`run.bat` / `scripts/build.py` 用镜像回退链装依赖：阿里云 → 清华 → 官方 PyPI。国内网络下基本能装上；如需改镜像，调 `scripts/build.py` 里的 `PIP_MIRRORS`。

## 一键启动（开发）

双击 `run.bat`，首次自动建 `.venv` 并装 `requirements.txt` 里的依赖，之后直接起应用。手动：

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

## 打包绿色版（PyInstaller onedir）

双击 `build.bat`（调试版 `build.bat --debug`）。产物：

- 正式版：`dist\ProjectManager\`
- 调试版：`dist-debug\ProjectManager\`（带控制台，崩溃日志可见，标题栏标「调试版」）

## 生成安装包（Inno Setup）

双击 `release.bat`，按提示：

1. 选正式版 / 调试版；
2. 正式版输入版本号（回车 = 沿用 `version.py` 当前值），调试版只输入调试序号；
3. 脚本依次做：写版本 → PyInstaller 构建 → Inno Setup 编译 → 算 SHA256。

产物：`release\ProjectManager_Setup_<版本标识>.exe` + 同名 `.sha256`。

- 正式版：`ProjectManager_Setup_1.4.2.exe`
- 调试版：`ProjectManager_Setup_1.4.2-debug.1.exe`
- 文件名就是完整版本标识，不再叠 `_Debug` 尾缀。

也可以不交互：`release.bat --version 1.4.2` 或 `release.bat --debug`。

## 版本号规则（SemVer 2.0.0）

| | 版本号 | 出包时输入 | 产物 |
|---|---|---|---|
| 正式版 | `1.4.2` | `MAJOR.MINOR.PATCH` | `ProjectManager_Setup_1.4.2.exe` |
| 调试版 | `1.4.2-debug.1` | 只输入序号 `1` | `ProjectManager_Setup_1.4.2-debug.1.exe` |

- 调试版永远跟随正式版号：`<正式版>-debug.<序号>`，出调试包不改 `version.py`。
- `-debug.N` 是 SemVer 的预发布标识，排序低于同号正式版。
- 版本号唯一来源是 `version.py` 的 `APP_VERSION`。

## 正式版 / 调试版可同时安装

两套用不同的 AppId + 安装目录 + 数据目录 + 快捷方式名（见 `installer/ProjectManager.iss`，由 `scripts/release.py` 注入），互不覆盖、可同时打开，各有各的卸载项和数据库。两套之间搬数据走应用内「文件 → 导出全部数据 / 导入数据」。

## 数据安全（覆盖升级不丢数据）

- 程序文件：安装版在 `C:\Program Files\ProjectManager\`，调试版在 `...\ProjectManager Debug\`。
- 用户数据：`%LOCALAPPDATA%\ProjectManager\data\`（调试版 `...\ProjectManager Debug\data\`），含 `projects.db` / `settings.ini` / `reports\`。
- 安装脚本只写程序目录，从不包含、从不删除用户数据目录。
- 首次以安装版运行且新位置还没有库时，自动把旧位置（exe 旁 `data\` 或 `_internal\data\`）的数据复制过来（旧文件保留不删）。

## 图标

`resources\app.ico`（「PM」字母图标）。缺失时 `scripts/make_icon.py` 会在构建时自动生成。
