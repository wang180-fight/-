# -*- coding: utf-8 -*-
"""生成模拟器专用图标 app_virtual.ico(与上位机的 app.ico 明显区分)。

设计: 深青底 + 白色断路器本体(手柄/液晶/指示灯) + 右下角显示器标记 + SIM 字样,
一眼可与上位机那张真机照片图标区分。
"""
import os
import struct

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SS = 1024                      # 超采样画布
TEAL_TOP = (45, 212, 191)
TEAL_BOT = (13, 148, 136)
TEAL = (15, 118, 110)
DARK = (30, 41, 59)
BODY = (248, 250, 252)
LINE = (226, 232, 240)
LCD = (8, 47, 38)
LCD_TXT = (134, 239, 172)


def vgrad(size, top, bot):
    img = Image.new("RGB", (1, size), top)
    d = ImageDraw.Draw(img)
    for y in range(size):
        k = y / max(size - 1, 1)
        d.point((0, y), fill=(int(top[0] + (bot[0] - top[0]) * k),
                              int(top[1] + (bot[1] - top[1]) * k),
                              int(top[2] + (bot[2] - top[2]) * k)))
    return img.resize((size, size))


def rounded(size, r):
    m = Image.new("L", (size, size), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, size - 1, size - 1), r, fill=255)
    return m


def draw_icon():
    base = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))

    # ---- 圆角青绿渐变底 ----
    bg = vgrad(SS, TEAL_TOP, TEAL_BOT).convert("RGBA")
    base.paste(bg, (0, 0), rounded(SS, 190))

    # ---- 断路器本体阴影 ----
    shadow = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (196, 168, 668, 888), 72, fill=(0, 0, 0, 90))
    base.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(24)))

    d = ImageDraw.Draw(base)
    # ---- 本体 ----
    d.rounded_rectangle((184, 148, 656, 868), 72, fill=BODY, outline=LINE,
                        width=8)
    # 顶部三个接线端
    for x in (250, 366, 482):
        d.rounded_rectangle((x, 104, x + 88, 168), 18, fill=(203, 213, 225))
    # ---- 手柄(左) ----
    d.rounded_rectangle((236, 240, 396, 700), 46, fill=DARK)
    d.rounded_rectangle((264, 430, 368, 660), 32, fill=(226, 232, 240))
    d.rounded_rectangle((276, 250, 356, 320), 26, fill=(51, 65, 85))
    # ---- 液晶(右) ----
    d.rounded_rectangle((432, 268, 612, 508), 28, fill=LCD)
    for i, w in enumerate((132, 132, 96)):
        y = 320 + i * 62
        d.rounded_rectangle((456, y, 456 + w, y + 26), 13, fill=LCD_TXT)
    # ---- 指示灯 ----
    d.ellipse((440, 552, 496, 608), fill=(34, 197, 94))
    d.ellipse((516, 552, 572, 608), fill=(250, 204, 21))
    d.ellipse((592, 552, 648, 608), fill=(239, 68, 68))
    # ---- 本地/远程拨钮 ----
    d.rounded_rectangle((440, 646, 620, 700), 27, fill=(203, 213, 225))
    d.rounded_rectangle((440, 646, 520, 700), 27, fill=TEAL)

    # ---- SIM 字样 ----
    font = None
    for path in (r"C:\Windows\Fonts\arialbd.ttf",
                 r"C:\Windows\Fonts\segoeuib.ttf"):
        if os.path.exists(path):
            font = ImageFont.truetype(path, 96)
            break
    if font is not None:
        d.text((238, 742), "SIM", font=font, fill=TEAL)

    # ---- 右下角显示器标记(表示"跑在电脑里的虚拟设备") ----
    monitor = Image.new("RGBA", (SS, SS), (0, 0, 0, 0))
    md = ImageDraw.Draw(monitor)
    md.rounded_rectangle((600, 636, 980, 906), 44, fill=(255, 255, 255),
                         outline=TEAL, width=18)
    md.rounded_rectangle((638, 674, 942, 830), 22, fill=TEAL)
    md.rounded_rectangle((666, 700, 848, 722), 11, fill=(204, 251, 241))
    md.rounded_rectangle((666, 740, 914, 762), 11, fill=(153, 246, 228))
    md.rounded_rectangle((666, 780, 800, 802), 11, fill=(153, 246, 228))
    md.rounded_rectangle((742, 906, 838, 948), 16, fill=(255, 255, 255))
    md.rounded_rectangle((700, 944, 880, 984), 20, fill=(255, 255, 255))
    base.alpha_composite(monitor)

    return base


def _dib_frame(img):
    """把一帧 RGBA 图编码成 ICO 内嵌的 BMP(DIB) 数据。

    Windows 资源管理器对 16~128px 的 DIB 帧兼容性最好(部分版本读 PNG 帧会回落成
    默认图标), 因此只有 256px 用 PNG, 其余全部用 32bpp BGRA + AND 掩码。
    """
    w, h = img.size
    px = img.convert("RGBA").tobytes()
    hdr = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0,
                      w * h * 4, 0, 0, 0, 0)
    rows = []
    for y in range(h - 1, -1, -1):                     # 自下而上存
        row = px[y * w * 4:(y + 1) * w * 4]
        bgra = bytearray(len(row))
        bgra[0::4], bgra[1::4], bgra[2::4], bgra[3::4] = (
            row[2::4], row[1::4], row[0::4], row[3::4])
        rows.append(bytes(bgra))
    # AND 掩码: 每行按 4 字节对齐, 全 0(透明度交给 alpha 通道)
    mask_row = ((w + 31) // 32) * 4
    mask = b"\x00" * (mask_row * h)
    return hdr + b"".join(rows) + mask


def _png_frame(img):
    from io import BytesIO
    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def write_ico(path, frames):
    """frames: [(size, PIL.Image)] -> 标准 ICO(≤128 用 DIB, 256 用 PNG)。"""
    blobs = []
    for size, img in frames:
        blobs.append(_dib_frame(img) if size < 256 else _png_frame(img))
    offset = 6 + 16 * len(frames)
    dirs, body = [], []
    for (size, _), blob in zip(frames, blobs):
        dirs.append(struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0,
                                1, 32, len(blob), offset))
        body.append(blob)
        offset += len(blob)
    with open(path, "wb") as f:
        f.write(struct.pack("<HHH", 0, 1, len(frames)))
        f.write(b"".join(dirs))
        f.write(b"".join(body))


def main():
    icon = draw_icon()
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, "app_virtual.ico")
    sizes = (16, 24, 32, 48, 64, 128, 256)
    frames = [(s, icon.resize((s, s), Image.LANCZOS)) for s in sizes]
    write_ico(out, frames)
    frames[-1][1].save(os.path.join(here, "_icon_virtual_preview.png"))
    print("saved", out, os.path.getsize(out), "bytes",
          [s for s, _ in frames])


if __name__ == "__main__":
    main()
