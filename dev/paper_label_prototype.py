"""Paper-label prototype for the Munbyn ITPP941 (2026-10-06, tuned with Steve
at the printer). Renders the label as ONE image and sends it as a TSPL
BITMAP over direct USB.

The printer FREEZES (needs a power cycle) on TSPL's BOX command and/or its
small built-in fonts (TEXT "1"/"2"); a single big TEXT line, FORMFEED and
BITMAP all print fine. So the agent should send bitmaps only.

Layout (203 dpi; 2.25 x 1.125 in label; 2.0 x 1.0 in content area plus
1 mm more at top and bottom): header 29 px bold, SKU line 27 px bold
(shrinks to 20 px, refuses below that: 8 of 3,526 live SKUs), Code 128
barcode 82 dots tall with digits, BIN line 28 px bold, no-scan mark top
right.

    py dev/paper_label_prototype.py preview.png          # render only
    py dev/paper_label_prototype.py preview.png --send   # print on a Munbyn on THIS PC
"""
import os
import sys
import time

from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import print_agent as pa  # noqa: E402

DPI = 203
W, H = int(2.25 * DPI), int(1.125 * DPI)  # 456 x 228 dots, the whole label
SAFE_X, SAFE_Y = (W - 2 * DPI) // 2, (H - 1 * DPI) // 2  # 2.0 x 1.0 in printable box

C128 = [
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312", "132212", "221213",
    "221312", "231212", "112232", "122132", "122231", "113222", "123122", "123221", "223211", "221132",
    "221231", "213212", "223112", "312131", "311222", "321122", "321221", "312212", "322112", "322211",
    "212123", "212321", "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121", "313121", "211331",
    "231131", "213113", "213311", "213131", "311123", "311321", "331121", "312113", "312311", "332111",
    "314111", "221411", "431111", "111224", "111422", "121124", "121421", "141122", "141221", "112214",
    "112412", "122114", "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112", "421211", "212141",
    "214121", "412121", "111143", "111341", "131141", "114113", "114311", "411113", "411311", "113141",
    "114131", "311141", "411131", "211412", "211214", "211232", "2331112",
]


def code128b(text: str) -> list[int]:
    codes = [104] + [ord(c) - 32 for c in text]
    check = (104 + sum(i * c for i, c in enumerate(codes[1:], 1))) % 103
    codes += [check, 106]
    widths: list[int] = []
    for c in codes:
        widths += [int(w) for w in C128[c]]
    return widths


def font(size: int, bold: bool = False):
    return ImageFont.truetype("arialbd.ttf" if bold else "arial.ttf", size)


def centered(d, y, text, f):
    w = d.textlength(text, font=f)
    d.text(((W - w) / 2, y), text, font=f, fill=0)


SKU_MAX_PX, SKU_MIN_PX, LINE_WIDTH = 27, 20, 2 * DPI  # 406 dots


class SkuTooLong(ValueError):
    """The SKU line will not fit a paper label even at the smallest size."""


def sku_line_size(text: str):
    """Largest font size (27 down to 20 px) at which the line fits the 2.0 in
    width, or None when even 20 px is too wide."""
    for size in range(SKU_MAX_PX, SKU_MIN_PX - 1, -1):
        if font(size, True).getlength(text) <= LINE_WIDTH:
            return size
    return None


def render(header, sku, barcode_value, bin_text, noscan=True):
    img = Image.new("1", (W, H), 1)
    d = ImageDraw.Draw(img)
    top_y = SAFE_Y - 8  # 1 mm more at the top (Steve, 2026-10-06)
    centered(d, top_y + 1, header, font(29, True))
    size = sku_line_size(sku)
    if size is None:
        raise SkuTooLong(sku)
    # A shrunk line keeps its vertical centre where the 27 px line sits.
    centered(d, top_y + 36 + (27 - size) // 2, sku, font(size, True))
    widths = code128b(barcode_value)
    module = 2
    total = sum(widths) * module
    x = (W - total) // 2
    top, bh = top_y + 76, 82
    black = True
    for w in widths:
        if black:
            d.rectangle([x, top, x + w * module - 1, top + bh], fill=0)
        x += w * module
        black = not black
    centered(d, top + bh + 3, barcode_value, font(16))
    bottom_y = SAFE_Y + DPI + 8  # 1 mm more at the bottom
    bin_font = font(28, True)
    centered(d, bottom_y - 26, bin_text, bin_font)
    if noscan:
        cx, cy, r = SAFE_X + 2 * DPI - 22, top_y + 20, 16
        for rad in (7, 13):
            d.arc([cx - rad - 4, cy - rad, cx - 4 + rad, cy + rad], 120, 240, fill=0, width=3)
            d.arc([cx + 4 - rad, cy - rad, cx + 4 + rad, cy + rad], -60, 60, fill=0, width=3)
        d.line([cx - r, cy + r, cx + r, cy - r], fill=0, width=3)
    return img


def tspl_bitmap(img) -> bytes:
    width_bytes = (W + 7) // 8
    raw = bytearray()
    px = img.load()
    for y in range(H):
        for xb in range(width_bytes):
            byte = 0
            for bit in range(8):
                x = xb * 8 + bit
                white = 1 if x >= W else (1 if px[x, y] else 0)
                byte = (byte << 1) | white  # TSPL bitmap: 0 = black dot
            raw.append(byte)
    head = (
        "SIZE 2.25,1.125\r\nGAP 0.08,0\r\nCLS\r\n"
        f"BITMAP 0,0,{width_bytes},{H},0,"
    ).encode("ascii")
    return head + bytes(raw) + b"\r\nPRINT 1\r\n"


if __name__ == "__main__":
    img = render("Telescopes Canada", "ZWO AMH", "6977641321679", "BIN: RSB")
    img.save(sys.argv[1] if len(sys.argv) > 1 else "munbyn_preview.png")
    if "--send" in sys.argv:
        t = pa.UsbTransport("vid_09c6")
        try:
            for _ in range(4):
                s = t.query(b"\x1b!?", 1500)
                print("before", repr(s))
                if s == b"\x00":
                    break
                time.sleep(1)
            t.write(tspl_bitmap(img), timeout_ms=20000)
            print("bitmap label sent")
            for i in range(8):
                time.sleep(1)
                print(f"{i + 1}s", repr(t.query(b"\x1b!?", 1500)))
        finally:
            t.close()
