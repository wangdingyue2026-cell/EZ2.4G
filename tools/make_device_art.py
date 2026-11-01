#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设备图标生成工具（开发用，运行时不需要）。

从微软 Fluent Emoji（MIT 许可，https://github.com/microsoft/fluentui-emoji）下载
3D 风格图标，缩成 ART_SIZE 像素后以 PNG base64 写进 my_devices.py 的 DEVICE_ART 段。
程序运行时再按 DPI 缩放到实际尺寸。纯标准库。

用法：python tools/make_device_art.py [my_devices.py]
"""
from __future__ import annotations

import base64
import os
import re
import struct
import sys
import urllib.parse
import urllib.request
import zlib

ART_SIZE = 88
BASE = "https://raw.githubusercontent.com/microsoft/fluentui-emoji/main/assets/"
ART = {
    "mouse": "Computer mouse/3D/computer_mouse_3d.png",
    "keyboard": "Keyboard/3D/keyboard_3d.png",
    "headset": "Headphone/3D/headphone_3d.png",
    "gamepad": "Video game/3D/video_game_3d.png",
    "device": "Satellite antenna/3D/satellite_antenna_3d.png",
}


def png_decode(data: bytes):
    """8 位 RGBA、非隔行 PNG → (宽, 高, RGBA 字节)。"""
    pos, idat, ihdr = 8, b"", None
    while pos < len(data):
        n, = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + n]
        pos += 12 + n
        if tag == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif tag == b"IDAT":
            idat += body
    w, h, depth, ctype, _c, _f, inter = ihdr
    if depth != 8 or ctype != 6 or inter:
        raise ValueError(f"不支持的 PNG：{ihdr}")
    raw = zlib.decompress(idat)
    stride = w * 4
    out = bytearray()
    prev = bytearray(stride)
    i = 0
    for _ in range(h):
        f = raw[i]
        line = bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        for x in range(stride):
            a = line[x - 4] if x >= 4 else 0
            b = prev[x]
            c = prev[x - 4] if x >= 4 else 0
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc
                                      else b if pb <= pc else c)) & 255
        out += line
        prev = line
    return w, h, out


def resize(w, h, px, nw, nh):
    """面积平均缩小（预乘 alpha，边缘不发黑）。"""
    out = bytearray(nw * nh * 4)
    for y in range(nh):
        y0, y1 = y * h / nh, (y + 1) * h / nh
        for x in range(nw):
            x0, x1 = x * w / nw, (x + 1) * w / nw
            r = g = b = a = tot = 0.0
            for sy in range(int(y0), min(h, int(y1 + 0.999))):
                wy = min(y1, sy + 1) - max(y0, sy)
                if wy <= 0:
                    continue
                for sx in range(int(x0), min(w, int(x1 + 0.999))):
                    wx = min(x1, sx + 1) - max(x0, sx)
                    if wx <= 0:
                        continue
                    k = (sy * w + sx) * 4
                    wt = wx * wy
                    al = px[k + 3] / 255.0 * wt
                    r += px[k] * al
                    g += px[k + 1] * al
                    b += px[k + 2] * al
                    a += al
                    tot += wt
            k = (y * nw + x) * 4
            if a > 0:
                out[k] = min(255, int(r / a + 0.5))
                out[k + 1] = min(255, int(g / a + 0.5))
                out[k + 2] = min(255, int(b / a + 0.5))
                out[k + 3] = min(255, int(a / tot * 255 + 0.5))
    return out


def png_encode(w, h, px) -> bytes:
    """逐行挑最省的滤波，压缩率比全用 None 高不少。"""
    stride = w * 4
    raw = bytearray()
    prev = bytes(stride)
    for y in range(h):
        line = px[y * stride:(y + 1) * stride]
        best = None
        for f in (0, 1, 2, 4):
            row = bytearray(stride)
            for x in range(stride):
                a = line[x - 4] if x >= 4 else 0
                b = prev[x]
                c = prev[x - 4] if x >= 4 else 0
                if f == 0:
                    p = 0
                elif f == 1:
                    p = a
                elif f == 2:
                    p = b
                else:
                    q = a + b - c
                    pa, pb, pc = abs(q - a), abs(q - b), abs(q - c)
                    p = a if pa <= pb and pa <= pc else b if pb <= pc else c
                row[x] = (line[x] - p) & 255
            score = sum(v if v < 128 else 256 - v for v in row)
            if best is None or score < best[0]:
                best = (score, f, row)
        raw.append(best[1])
        raw += best[2]
        prev = line

    def chunk(t, d):
        return (struct.pack(">I", len(d)) + t + d
                + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def build() -> dict:
    out = {}
    for kind, path in ART.items():
        url = BASE + urllib.parse.quote(path)
        with urllib.request.urlopen(url, timeout=30) as resp:
            w, h, px = png_decode(resp.read())
        small = resize(w, h, px, ART_SIZE, ART_SIZE)
        out[kind] = base64.b64encode(png_encode(ART_SIZE, ART_SIZE,
                                                small)).decode("ascii")
        print(f"{kind}: {len(out[kind])} 字符")
    return out


def write_into(path: str, art: dict) -> None:
    src = open(path, encoding="utf-8").read()
    lines = ["# >>> DEVICE_ART（tools/make_device_art.py 生成，勿手改）",
             "# 图标来自微软 Fluent Emoji（MIT 许可）：",
             "# https://github.com/microsoft/fluentui-emoji",
             f"DEVICE_ART_SIZE = {ART_SIZE}",
             "DEVICE_ART = {"]
    for kind, b64 in art.items():
        lines.append(f'    "{kind}": (')
        for i in range(0, len(b64), 72):
            lines.append(f'        "{b64[i:i + 72]}"')
        lines.append("    ),")
    lines += ["}", "# <<< DEVICE_ART"]
    block = "\n".join(lines)
    pat = re.compile(r"# >>> DEVICE_ART.*?# <<< DEVICE_ART", re.S)
    if not pat.search(src):
        raise SystemExit("my_devices.py 里没有 DEVICE_ART 标记段")
    open(path, "w", encoding="utf-8").write(pat.sub(lambda m: block, src))


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(__file__), "..", "my_devices.py")
    write_into(target, build())
