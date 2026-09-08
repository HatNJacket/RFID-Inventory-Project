"""The sticker's SKU-line wrap (Nick, 2026-09-08): one line at font 30
while it fits the 431-dot width; TWO wrapped lines at the SAME big
font when it doesn't - the barcode block moves down and trims to make
room (his field verdict: the first cut's font 16 was unreadable).
Text too wide even for two font-30 lines steps down just far enough
(30 -> 28 -> ..., floor 20). Every path capped at 56 chars. Plus the
barcode geometry his test prints confirmed: 33 alphanumeric chars is
the printable max, 34 overflows.

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

BASE = {"sku": "EXOS2CW", "barcode": "63864501075088",
        "bin_location": "D8-1"}

# ---- short SKU line: unchanged single big line ------------------------
z = zpl(BASE)
check("short SKU prints one line at font 30",
      "^CF0,30\n^FO0,52^FB431,1,0,C^FDEXOS2CW^FS" in z, z)
check("short SKU keeps the original barcode placement",
      ",88^BY2,3,72^BCN,72" in z and "^FO0,164" in z, z)

# ---- long SKU line: two wrapped lines at the SAME big font ------------
long_sku = "CRUX140COUNTERWEIGHTBAR-BLACK-EXTRA-LONG"
z = zpl({**BASE, "sku": long_sku})
check("long SKU wraps to two lines at font 30",
      f"^CF0,30\n^FO0,52^FB431,2,0,C^FD{long_sku}^FS" in z, z)
check("wrapped label drops the single-line form",
      "^FB431,1,0,C^FD" + long_sku not in z, z)
check("wrapped label moves the bars down and trims them",
      ",118^BY" in z and "^BCN,56" in z and "^FO0,178" in z
      and ",88^BY" not in z, z)

# Text too wide even for two font-30 lines steps the font down only as
# far as needed (56 average chars land at 26 - still readable).
z = zpl({**BASE, "sku": "X" * 56})
check("56-char worst case steps down, modestly",
      "^CF0,26\n^FO0,52^FB431,2,0,C^FD" + "X" * 56 in z, z)

# The threshold is the measured width, not a character count: narrow
# characters pack tighter than wide ones.
narrow = "1111111111111111111111111"          # 25 narrow chars - fits big
wide = "WWWWWWWWWWWWWWWWWWWW"                 # 20 wide chars - too wide
check("narrow text stays on ONE big line",
      "^FB431,1,0,C^FD" + narrow in zpl({**BASE, "sku": narrow}), narrow)
check("wide text wraps sooner (still font 30)",
      "^CF0,30\n^FO0,52^FB431,2,0,C^FD" + wide
      in zpl({**BASE, "sku": wide}), wide)
check("the width model agrees with the web preview's",
      pa._zpl_text_dots("EXOS2CW", 30) < 431
      < pa._zpl_text_dots(wide, 30), pa._zpl_text_dots(wide, 30))

# ---- every path caps at 56 -------------------------------------------
huge = "X" * 80
z = zpl({**BASE, "sku": huge})
check("plain SKU path now capped at 56 (was uncapped)",
      "^FD" + "X" * 56 + "^FS" in z and "X" * 57 not in z, z)
z = zpl({**BASE, "label_name": "My Nice Name", "label_placement": "sku",
         "sku": "EXOS2CW"})
check("label_name-as-SKU still renders (one line, short)",
      "^FDMy Nice Name^FS" in z, z)

# Case prefix rides the wrap decision like any other centre text.
z = zpl({**BASE, "case_units": 8, "sku": long_sku})
check("case prefix + long SKU wraps at the big font",
      "^FB431,2,0,C" in z and f"^FD8 x {long_sku[:52]}" in z
      and "^CF0,30\n^FO0,52" in z, z)

# ---- barcode geometry: Nick's confirmed 33-char max -------------------
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
