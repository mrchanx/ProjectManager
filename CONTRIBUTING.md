# 贡献指南

欢迎提 Issue 和 Pull Request。

## 提 Issue

- 提 bug 请说明：操作步骤、期望行为、实际行为、软件版本（帮助 → 关于）、Windows 版本。
- 功能建议直接讲清楚场景和想要的行为。

## 提 PR

1. 从 `main` 切一个分支改。
2. 桌面应用基于 PySide6，跑 `run.bat`（首次自建 venv）启动；改完跑 `smoke_test.py` 确认回归通过。
3. 提交信息写清楚改了什么、为什么。
4. 涉及行为变动请顺手更新 README 或使用说明（`app/help_text.py`）。

## 代码约定

- `.bat` 脚本必须 GBK 编码 + CRLF 换行（否则中文在 cmd 里乱码并破坏解析），脚本内提示尽量用英文、中文文案放 Python。
- 安装脚本 `installer/ProjectManager.iss` 含中文，必须保存为 UTF-8 BOM，否则 Inno Setup 按 ANSI 读会乱码。
- 版本号统一在 `version.py`，不要散落在别处。
- 不要提交 `data/`、`release/`、`.venv/`、`dist/`、`build/` 等（已在 `.gitignore`）。

## 构建

从源码打安装包见 [docs/BUILD.md](docs/BUILD.md)，需要先装 Inno Setup 6。
