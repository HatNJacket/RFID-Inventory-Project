"""The sticker's SKU-line wrap, third field iteration (Nick,
2026-09-08): one line at font 30 while it fits; a wider text wraps to
TWO big-font lines in an INSET field (about one character narrower -
edge-to-edge wrapped lines clipped on label variance), the barcode
moving down to make room. The break point is CHOSEN: an operator "|"
wins, else the dash/space/slash nearest the middle; only separator-less
tokens fall back to ZPL auto-wrap. Fonts tier 30 -> 28 -> ... floor 20
with the field-calibrated width model (real font 0 runs ~13% wide of
the model). Every path capped at 56 chars. Plus the barcode geometry
the first test prints confirmed: 33 alphanumeric chars printable max.

No server, no DB - build_zpl is a pure function.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
import print_agent as pa

fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

def zpl(job):
    return pa.build_zpl(dict(job), encode_rfid=False)

def wrap_font(left, right):
    f = 30
    while f > 20 and not (pa._sku_line_fits(left, f, 431)
                          and pa._sku_line_fits(right, f, 431)):
        f -= 2
    return f

BASE = {"sku": "EXOS2CW", "barcode": "63864501075088",
        "bin_location": "D8-1"}
FW = 431 - 2 * pa.SKU_WRAP_MARGIN   # the inset wrapped field

# ---- short SKU line: unchanged single big line ------------------------
z = zpl(BASE)
check("short SKU prints one line at font 30",
      "^CF0,30\n^FO0,52^FB431,1,0,C^FDEXOS2CW^FS" in z, z)
check("short SKU keeps the original barcode placement",
      ",88^BY2,3,72^BCN,72" in z and "^FO0,164" in z, z)

# ---- long SKU with separators: CHOSEN break, not ZPL's ---------------
long_sku = "CRUX140COUNTERWEIGHTBAR-BLACK-EXTRA-LONG"
left, right = pa._sku_split(long_sku)
check("the split lands on a separator, balanced",
      left.endswith("-") and left + right == long_sku, (left, right))
wf = wrap_font(left, right)
z = zpl({**BASE, "sku": long_sku})
check("long SKU wraps at the chosen break in the inset field",
      f"^CF0,{wf}\n^FO{pa.SKU_WRAP_MARGIN},52^FB{FW},2,0,C"
      f"^FD{left}\\&{right}^FS" in z, z)
check("wrapped label moves the bars down and trims them",
      ",118^BY" in z and "^BCN,56" in z and "^FO0,178" in z
      and ",88^BY" not in z, z)
check("both chosen halves genuinely fit their line",
      pa._sku_line_fits(left, wf, 431)
      and pa._sku_line_fits(right, wf, 431), (left, right, wf))

# ---- the operator's own break: "|" wins outright ----------------------
z = zpl({**BASE, "sku": "ZWO ASI2600MC|DUO Camera"})
check("a typed | breaks exactly there (even though it would fit)",
      "^FDZWO ASI2600MC\\&DUO Camera^FS" in z, z)
check("the | never reaches the sticker", "|" not in z, z)

# ---- no separators at all: ZPL auto-wrap fallback ---------------------
z = zpl({**BASE, "sku": "X" * 56})
check("a solid 56-char token falls back to auto-wrap",
      f"^FO{pa.SKU_WRAP_MARGIN},52^FB{FW},2,0,C^FD" + "X" * 56 in z
      and "\\&" not in z, z)
check("...at a font its inflated width fits two reserved lines",
      any(f"^CF0,{f}\n^FO{pa.SKU_WRAP_MARGIN},52" in z
          and pa._sku_fits("X" * 56, f, 2, 431)
          for f in (20, 22, 24, 26, 28, 30))
      or "^CF0,20" in z, z)

# Nick's overprinting TEST 3 string: has dashes, so it now splits
# cleanly instead of ZPL breaking mid-token.
field3 = "SKU-LINE-MAXIMUM-56-CHARACTERS-ABCDEFGHIJKLMNOPQRSTUVWXY"
l3, r3 = pa._sku_split(field3)
z = zpl({**BASE, "sku": field3})
check("TEST 3 splits at a separator now",
      f"^FD{l3}\\&{r3}^FS" in z and l3.endswith("-"), (l3, r3))
check("TEST 3 halves fit at the chosen font",
      pa._sku_line_fits(l3, wrap_font(l3, r3), 431)
      and pa._sku_line_fits(r3, wrap_font(l3, r3), 431), (l3, r3))

# The threshold is measured width, not characters.
narrow = "1111111111111111111111111"          # 25 narrow chars - fits big
check("narrow text stays on ONE big line",
      "^FB431,1,0,C^FD" + narrow in zpl({**BASE, "sku": narrow}), narrow)
check("the width model agrees with the web preview's",
      pa._zpl_text_dots("EXOS2CW", 30) < 431
      < pa._zpl_text_dots("W" * 20, 30) * pa.SKU_WIDTH_FUDGE, "")

# ---- every path caps at 56 -------------------------------------------
z = zpl({**BASE, "sku": "X" * 80})
check("plain SKU path capped at 56",
      "^FD" + "X" * 56 + "^FS" in z and "X" * 57 not in z, z)
z = zpl({**BASE, "label_name": "My Nice Name", "label_placement": "sku",
         "sku": "EXOS2CW"})
check("label_name-as-SKU still renders (one line, short)",
      "^FDMy Nice Name^FS" in z, z)

# Case prefix rides the wrap decision like any other centre text.
z = zpl({**BASE, "case_units": 8, "sku": long_sku})
check("case prefix + long SKU wraps with a chosen break",
      "\\&" in z and "^FD8 x " in z, z)

# ---- barcode geometry: the confirmed 33-char max ----------------------
usable = pa.label_dots(pa.LABEL_WIDTH_IN, pa.LABEL_HEIGHT_IN)[0] - 24
check("33 alphanumeric chars fit at module 1",
      pa._code128_width_dots("A" * 33, 1) <= usable,
      pa._code128_width_dots("A" * 33, 1))
check("34 alphanumeric chars overflow",
      pa._code128_width_dots("A" * 34, 1) > usable,
      pa._code128_width_dots("A" * 34, 1))
check("15 chars is the last module-2 length",
      pa._code128_width_dots("A" * 15, 2) <= usable
      < pa._code128_width_dots("A" * 16, 2), usable)

print()
sys.exit(1 if fails else 0)
