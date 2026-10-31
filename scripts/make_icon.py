"""ProjectManager 应用图标生成（纯 Python，无 GUI 依赖）。

生成 resources/app.ico：蓝色圆角方块 + 白色「PM」字母 + 底部进度条点缀。
- 256x256 RGBA PNG 手工编码（zlib+struct），Vista+ 的 ICO 支持 PNG 压缩。
- build.py 构建时若图标缺失会自动调用本模块补齐。

用法：
    python scripts/make_icon.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ICON_FILE = ROOT / "resources" / "app.ico"

SIZE = 256
BG = (0x25, 0x63, 0xEB, 255)    # 主题蓝
FG = (255, 255, 255, 255)       # 字母/进度条白
TRANSPARENT = (0, 0, 0, 0)

CX = CY = SIZE / 2
HALF, RADIUS = 104, 56          # 圆角方块：半宽 104、圆角半径 56


def _png_bytes(width, height, pixels):
    """极简 PNG 编码器（RGBA 8bit，无滤波）。"""
    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))

    raw = b"".join(
        b"\x00" + b"".join(struct.pack("BBBB", *px) for px in row) for row in pixels
    )
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def _ico_bytes(png):
    """把 PNG 打包进 ICO 容器（Vista+ 支持 PNG 压缩图标）。"""
    header = struct.pack("<HHH", 0, 1, 1)            # reserved, type=icon, count=1
    entry = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png), 22)
    return header + entry + png


def _in_rounded(x, y):
    dx = max(abs(x - CX) - (HALF - RADIUS), 0.0)
    dy = max(abs(y - CY) - (HALF - RADIUS), 0.0)
    return dx * dx + dy * dy <= RADIUS * RADIUS


def _in_p(x, y):
    """白色「P」：竖干 + 顶横 + 右竖（碗壁）+ 中横。"""
    if 48 <= x <= 74 and 56 <= y <= 188:        # 竖干
        return True
    if 48 <= x <= 120 and 56 <= y <= 84:        # 顶横
        return True
    if 96 <= x <= 120 and 84 <= y <= 112:       # 碗右壁
        return True
    if 48 <= x <= 120 and 112 <= y <= 140:     # 中横（封碗底）
        return True
    return False


def _in_m(x, y):
    """白色「M」：左右竖干 + 中间 V 形双斜带。"""
    if 136 <= x <= 160 and 56 <= y <= 188:     # 左竖干
        return True
    if 184 <= x <= 208 and 56 <= y <= 188:     # 右竖干
        return True
    if 56 <= y <= 124:                        # 双斜带，收于 (172,124)
        t = (y - 56) / 68.0
        if abs(x - (160 + t * 12)) <= 6:
            return True
        if abs(x - (184 - t * 12)) <= 6:
            return True
    return False


def _in_bar(x, y):
    """底部进度条：白色描边 + 62% 白色填充（呼应"进度管理"）。"""
    if not (56 <= x <= 200 and 200 <= y <= 220):
        return False
    if 62 <= x <= 194 and 206 <= y <= 214:     # 内芯：未填充部分透出蓝底
        return 62 <= x <= 148                  # 填充约 62%
    return True                                 # 描边环


def make_icon_pixels():
    pixels = []
    for y in range(SIZE):
        row = []
        for x in range(SIZE):
            if not _in_rounded(x, y):
                row.append(TRANSPARENT)
            elif _in_p(x, y) or _in_m(x, y) or _in_bar(x, y):
                row.append(FG)
            else:
                row.append(BG)
        pixels.append(row)
    return pixels


def ensure_icon() -> Path:
    """确保 resources/app.ico 存在；缺失时生成。"""
    if ICON_FILE.is_file():
        return ICON_FILE
    png = _png_bytes(SIZE, SIZE, make_icon_pixels())
    ICON_FILE.parent.mkdir(parents=True, exist_ok=True)
    ICON_FILE.write_bytes(_ico_bytes(png))
    return ICON_FILE


if __name__ == "__main__":
    path = ensure_icon()
    print(f"icon written: {path} ({path.stat().st_size} bytes)")
