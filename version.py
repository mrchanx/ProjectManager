"""ProjectManager 统一版本源（唯一版本号定义处）。

所有需要版本号的地方（界面显示、PyInstaller 版本资源、Inno Setup 安装包）
都必须从这里读取，禁止在其它文件硬编码版本号。

版本命名遵循语义化版本 SemVer 2.0.0：

    正式版   MAJOR.MINOR.PATCH            1.3.5
    调试版   MAJOR.MINOR.PATCH-debug.N    1.3.5-debug.2

    · 前缀完全跟随正式版：调试版不自己演进版本号，永远挂在当前正式版之下
    · N 是调试序号，同一正式版内递增；出包时**只输入这一个数字**，
      版本号由程序自动拼成 <正式版>-debug.<N>
    · "-debug.N" 是 SemVer 的预发布标识（pre-release），排序上低于同号正式版
      （1.3.5-debug.2 < 1.3.5），与"预览版/内部测试版"的行业惯例一致
    · 调试序号以 release\\ 目录里已出的安装包为台账（文件名即记录），
      因此不需要额外的计数器文件，也不会因换机器/删文件而产生错号

Windows 版本资源要求 4 段纯数字，映射规则：

    1.3.5         → 1.3.5.0
    1.3.5-debug.2 → 1.3.5.2     调试序号放第 4 段（构建号位），
                                让 exe 属性里的"文件版本"能区分每次构建
"""

from __future__ import annotations

import re

APP_NAME = "ProjectManager"

# 正式版版本号（唯一版本源）。
# 出正式版时 release.bat 只改这一行；出调试版时**不改动它**（调试版跟随此号）。
APP_VERSION = "1.4.3"

# 调试版尾缀标记（SemVer 预发布标识）：1.3.5-debug.2
DEBUG_TAG = "debug"

_RELEASE_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
_DEBUG_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)-%s\.(\d+)$" % DEBUG_TAG)


def is_release_version(v: str) -> bool:
    """是否是规范的三段正式版版本号（1.3.5）。"""
    return bool(_RELEASE_RE.match((v or "").strip()))


def is_debug_version(v: str) -> bool:
    """是否是调试版版本号（1.3.5-debug.2）。"""
    return bool(_DEBUG_RE.match((v or "").strip()))


def make_debug_version(base_version: str, number: int) -> str:
    """正式版版本号 + debug 尾缀 + 调试序号 → 调试版版本号。

    >>> make_debug_version("1.3.5", 2)
    '1.3.5-debug.2'
    """
    base = (base_version or "").strip()
    if not is_release_version(base):
        raise ValueError("正式版版本号格式应为 MAJOR.MINOR.PATCH：%r" % base_version)
    try:
        n = int(number)
    except (TypeError, ValueError):
        raise ValueError("调试序号必须是正整数，收到 %r" % (number,)) from None
    if n < 1:
        raise ValueError("调试序号必须 ≥ 1，收到 %r" % (number,))
    return "%s-%s.%d" % (base, DEBUG_TAG, n)


def debug_number_of(v: str) -> int | None:
    """取调试版版本号里的调试序号；正式版或非法值返回 None。"""
    m = _DEBUG_RE.match((v or "").strip())
    return int(m.group(4)) if m else None


def base_version_of(v: str) -> str:
    """去掉 debug 尾缀，得到所跟随的正式版版本号（正式版原样返回）。"""
    s = (v or "").strip()
    m = _DEBUG_RE.match(s)
    if m:
        return "%s.%s.%s" % (m.group(1), m.group(2), m.group(3))
    return s


def numeric_version(v: str) -> tuple[int, int, int, int]:
    """版本字符串 → Windows 版本资源要求的 4 段数字版本。

    调试版的调试序号落在第 4 段（构建号位）：
        1.3.5 → (1, 3, 5, 0)      1.3.5-debug.2 → (1, 3, 5, 2)
    非规范字符串（如 1.0）按数字顺序尽量解析，缺位补 0，绝不抛异常
    （版本资源生成失败会直接让打包挂掉，宁可退化也不能中断）。
    """
    s = (v or "").strip()
    m = _DEBUG_RE.match(s)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)))
    m = _RELEASE_RE.match(s)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)), 0)
    parts = [int(p) for p in re.findall(r"\d+", s)][:4]
    while len(parts) < 4:
        parts.append(0)
    return (parts[0], parts[1], parts[2], parts[3])


def numeric_version_string(v: str) -> str:
    """4 段数字版本的字符串形式（给 Inno Setup 的 VersionInfoVersion）。

    Inno Setup 的 VersionInfoVersion 只接受纯数字（1.3.5.2），
    不接受带 -debug.N 的语义化版本字符串，故单独换算。
    """
    return ".".join(str(x) for x in numeric_version(v))


# 兼容历史引用：正式版版本号对应的 4 段数字版本
VERSION_TUPLE = numeric_version(APP_VERSION)
