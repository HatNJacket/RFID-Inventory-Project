"""Zebra RFID print agent — runs ONLY on the PC connected to the printer.

Polls the app for queued label jobs, drives the Zebra, then reports back.
On success the server records the tag<->product assignment automatically.
Every other device just uses the web app in a browser; this script is the
one piece that must live next to the printer.

v6 (2026-09-23) is a ground-up rewrite of everything below the label
builders, after a week of labels silently vanishing between the Windows
spooler and the paper:

- DIRECT USB: by default the agent now opens the Zebra's USB printer
  interface itself (usbprint.sys) and holds it EXCLUSIVELY - no Windows
  spooler, no driver bidi polling, and no other program (ShipStation
  Connect) can touch the printer while the agent runs. The spooler path
  survives as --transport spooler and as the automatic fallback.
- TRUTHFUL DONE: with a readable transport the agent asks the PRINTER
  what happened (~HS status + the odometer's label count) and completes
  a job only once the printer's own counter moved. Media out / head
  open / pause HOLD the queue and show on the web terminal's Queue tab
  instead of masquerading as printed; a swallowed label is retried.
- CLOUD CONTROL: every poll doubles as a heartbeat carrying printer
  status; the server can queue commands (shell, get-log, raw ZPL,
  printer query, test label, restart, self-update) whose output is
  posted back. print_agent.py updates ITSELF from the server - nobody
  remote-desktops into the warehouse PC to maintain this anymore.

Usage (PowerShell on the printer PC):

    # USB printer, direct transport with automatic spooler fallback:
    py print_agent.py --app https://YOUR-APP.azurewebsites.net --printer-name "ZDesigner ZD220-203dpi ZPL" --no-rfid --log-file C:\\rfid\\print_agent.log

    # Printer shared on the network (has its own IP):
    py print_agent.py --app https://YOUR-APP.azurewebsites.net --printer-host 192.168.1.50

    # See the ZPL without touching a printer (testing):
    py print_agent.py --app http://127.0.0.1:8000 --dry-run --once

Options: --poll N (seconds between checks, default 3), --once (single
pass), --agent-key KEY (must match the app's PRINT_AGENT_KEY env var, if
set), --transport auto|usb|spooler|network, --log-file PATH (agent-owned
log with rotation - don't ALSO shell-redirect), --no-auto-update.

--no-rfid: for printers WITHOUT an RFID encoder (e.g. the ZD220t). Prints
the barcode label only — no EPC is written to the sticker and no assignment
is auto-created; after applying the label, link the tag with the normal
two-scan flow (scan barcode, scan tag). Omit this flag only on R-series
printers (ZD621R etc.) that can actually encode.

The agent expects to run under a LOOPING runner (run_agent.cmd with a
goto loop): a clean exit is the restart/self-update mechanism, not a
crash. A one-shot runner leaves the printer dead after the first
restart command.
"""
import argparse
import ctypes
import io
import os
import re
import socket
import struct
import subprocess
import sys
import time

import requests

# v6: direct-USB transport + printer-confirmed dones + heartbeat/command
# cloud control + self-update. Bump on behavior changes - the server
# compares this against its own copy to drive auto-update.
AGENT_VERSION = "6"

# ---------------------------------------------------------------------------
# Label geometry. Defaults match the warehouse RFID stickers (measured
# 2.125 x 1.25 inch) at the ZD220's 203 dpi; override with --label-width /
# --label-height (inches) for other media. ^PW/^LL tell the printer the
# exact canvas so nothing lands off the sticker; ^FB word-wraps the title.
LABEL_WIDTH_IN = 2.125
LABEL_HEIGHT_IN = 1.25
DPI = 203

# Fine alignment, dialed in against this printer's calibration by test
# prints (2026-07-21). Tune with --shift-down / --shift-right if the media
# or printer changes.
SHIFT_DOWN_DOTS = 31   # ^LT: + moves the whole image down (max 120)
SHIFT_RIGHT_DOTS = 2   # ^LH x: + moves the whole image right

# Layout (all centered): header / SKU / barcode / BIN. The header is the
# store name ("Telescopes Canada") unless the job carries an explicit
# label_name — the Scan Station serial flow sets that for Astronomik filters
# so their item name prints at the top. Batch labels never set it, so they
# print the plain store header + SKU.
LABEL_ZPL = """^XA
{rfid_setup}^PW{pw}
^LL{ll}
^LH{sr},0
^LT{sd}
{header}{sku_line}{barcode_line}{bin_line}^XZ
"""

# The centre (SKU) line: one line at font 30 while it fits the width.
# A wider text WRAPS to two lines (2026-09-08, Nick) - the old
# single-line ^FB overprinted itself instead of clipping, which is how
# long SKU lines printed wrong. Field verdicts, in order: font 16 was
# too small (the wrap keeps the big font, the barcode moves down);
# edge-to-edge wrapped lines clip on label variance (wrapped lines run
# in a field inset SKU_WRAP_MARGIN per side - about one character
# narrower); and ZPL's own break lands mid-token, so the break point
# is now CHOSEN: a "|" typed in the label editor wins, else the
# dash/space/slash nearest the middle, else ZPL auto-wrap as a last
# resort. Fonts step down just far enough (30 -> 28 -> ... floor 20).
SKU_LINE_ONE = "^CF0,30\n^FO0,52^FB{pw},1,0,C^FD{sku}^FS\n"
SKU_LINE_WRAP = "^CF0,{f}\n^FO{m},52^FB{fw},2,0,C^FD{sku}^FS\n"
SKU_WRAP_MARGIN = 10
SKU_BREAK_CHARS = "-/ _."

HEADER_STORE = "^CF0,34\n^FO0,10^FB{pw},1,0,C^FDTelescopes Canada^FS\n"

# Mode A (automatic) makes the printer pick the densest Code 128 encoding,
# which is what _code128_width_dots models — required for true centering.
# The printer's own interpretation line shrinks with the module width, so
# it's disabled; a separate normal-sized centered caption is printed below.
BARCODE_LINE = (
    "^FO{bx},{by}^BY{module},3,{bh}^BCN,{bh},N,N,N,A^FD{barcode}^FS\n"
    "^CF0,{cf}\n"
    "^FO0,{cy}^FB{pw},1,0,C^FD{barcode}^FS\n"
)

# Barcode block geometry. Normal labels keep the original placement; a
# WRAPPED SKU line pushes the bars down 30 dots and trims their height
# so the caption still clears the BIN line (Nick, 2026-09-08 - room for
# the second big-font line comes from the bars, not from a tiny font).
BARCODE_GEO_NORMAL = {"by": 88, "bh": 72, "cy": 164, "cf": 20}
BARCODE_GEO_WRAPPED = {"by": 118, "bh": 56, "cy": 178, "cf": 18}

# Prepended only for RFID-encoding printers: auto tag setup + write the EPC.
RFID_ZPL = "^RS8\n^RFW,H^FD{epc}^FS\n"

# Re-align (operator button in the web terminal's Print queue): ~PH slews
# the media to the NEXT label's home position using the media sensor.
# After a rip pulled the liner forward, this re-registers before any
# printing, consuming only the already-disturbed label instead of two
# misprints plus a blank. Prints nothing and encodes nothing.
FEED_ZPL = "~PH\n"

# Backfeed sequence. ~JSB (backfeed BEFORE printing) was tried
# 2026-08-25 against tear drift; the 2026-08-26 verdict was it does NOT
# fix drift, and the 2026-09-09 verdict was it is actively harmful: the
# retraction runs at NEXT-print time, a fixed dead-reckoned distance
# from wherever the operator's tear left the media, and on these short
# labels it pulled the leading edge back BEHIND the platen roller - the
# printer couldn't grab the media and someone had to pull it through by
# hand before re-aligning. ~JSA (the factory default, backfeed right
# AFTER printing) retracts while registration is still known and before
# anyone touches the media, so the torn edge is never blindly reversed.
# Sent at startup to overwrite the ~JSB any earlier agent left saved.
# Tear drift's real remedy remains the ~PH re-align (gap-sensor seek).
BACKFEED_BEFORE_ZPL = "~JSA\n"

# Alignment test: a border box + corner ticks, no job needed. If the box
# edges don't sit just inside the sticker edges, the size flags (or the
# printer's media calibration) are off.
TEST_ZPL = """^XA
^PW{pw}
^LL{ll}
^LH{sr},0
^LT{sd}
^FO2,2^GB{bw},{bh},2^FS
^CF0,24
^FO20,{mid}^FDTEST {win} x {hin} in ({pw} x {ll} dots)^FS
^XZ
"""

def label_dots(width_in: float, height_in: float) -> tuple[int, int]:
    return int(width_in * DPI), int(height_in * DPI)


def _zpl_text_dots(text: str, size: int) -> float:
    """Approximate ZPL font-0 text width - the SAME model the web
    terminal's previews use (zplTextDots in app.js), so the sticker and
    every preview agree on when the SKU line wraps."""
    narrow = set("iIl1jft.,:;'|!()[] -")
    wide = set("MWmw@")
    return sum(
        (0.35 if c in narrow else 0.78 if c in wide else 0.55) * size
        for c in text
    )


# Field calibration (Nick's SKU TEST 3, 2026-09-08): the printer's real
# font 0 runs ~13% wider than the model (a line the model rated 355 of
# 431 dots filled the width), and ^FB loses a little capacity at every
# break. Inflate the model and reserve break room per wrapped line
# before trusting a fit. Keep IDENTICAL to the app.js constants.
SKU_WIDTH_FUDGE = 1.13
SKU_WRAP_LINE_RESERVE = 20


def _sku_fits(text: str, size: int, lines: int, pw: int) -> bool:
    if lines == 1:
        cap = pw
    else:
        cap = lines * (pw - 2 * SKU_WRAP_MARGIN - SKU_WRAP_LINE_RESERVE)
    return _zpl_text_dots(text, size) * SKU_WIDTH_FUDGE <= cap


def _sku_split(text: str) -> tuple[str, str] | None:
    """The chosen two-line break for a too-wide centre line: an
    operator "|" wins outright; otherwise the dash/space/slash nearest
    the middle (breaking AFTER the separator, like the printed test
    labels did). None when the text has no separator to use."""
    if "|" in text:
        left, _, right = text.partition("|")
        left, right = left.strip(), right.strip()
        if left and right:
            return left, right.replace("|", " ").strip()
    cuts = [i + 1 for i, c in enumerate(text[:-1])
            if c in SKU_BREAK_CHARS]
    if not cuts:
        return None
    best = min(cuts, key=lambda i: abs(
        _zpl_text_dots(text[:i], 30) - _zpl_text_dots(text[i:], 30)
    ))
    left, right = text[:best].rstrip(), text[best:].lstrip()
    return (left, right) if left and right else None


def _sku_line_fits(text: str, size: int, pw: int) -> bool:
    """One WRAPPED line's capacity (the inset field, calibrated)."""
    cap = pw - 2 * SKU_WRAP_MARGIN
    return _zpl_text_dots(text, size) * SKU_WIDTH_FUDGE <= cap


def _code128_symbols(data: str) -> int:
    """Data symbols ZPL's automatic mode spends, subset switches
    included. The old model charged MIXED codes one symbol per char,
    but the encoder still packs digit RUNS into subset-C pairs - so
    "12345678-O" (an open-box barcode, Nick 2026-09-09) really costs
    4 pair symbols + a switch + 2 chars, not 10 symbols. Overstating
    the width computed a centering x too far LEFT by half the error.

    Rules mirrored from the encoder: start in C when the code opens
    with 4+ digits; mid-stream, hop to C for a run of 6+ digits (or
    4+ that finish the code); an odd trailing digit rides subset B."""
    n = len(data)
    i = 0
    symbols = 0
    subset = None
    while i < n:
        run = 0
        while i + run < n and data[i + run].isdigit():
            run += 1
        use_c = (
            (subset is None and run >= 4)
            or (subset == "C" and run >= 2)
            or (subset == "B" and (run >= 6 or (run >= 4
                                                and i + run == n)))
        )
        if use_c:
            if subset == "B":
                symbols += 1  # subset switch
            subset = "C"
            pairs = run // 2
            symbols += pairs
            i += pairs * 2
            # An odd digit left over falls through to subset B below.
        else:
            if subset == "C":
                symbols += 1  # subset switch
            subset = "B"
            symbols += 1
            i += 1
    return symbols


def _code128_width_dots(data: str, module: int = 2) -> int:
    """Printed width of a Code 128 barcode (mode A auto-encoding), so it
    can be centered on the sticker."""
    symbols = _code128_symbols(data)
    return (11 * (symbols + 2) + 13) * module  # start+data+check, then stop


def build_test_zpl(width_in: float, height_in: float,
                   shift_down: int = SHIFT_DOWN_DOTS,
                   shift_right: int = SHIFT_RIGHT_DOTS) -> str:
    pw, ll = label_dots(width_in, height_in)
    return TEST_ZPL.format(
        pw=pw, ll=ll, bw=pw - 4, bh=ll - 4, mid=ll // 2 - 12,
        win=width_in, hin=height_in, sd=shift_down, sr=shift_right,
    )


def build_zpl(job: dict, encode_rfid: bool,
              width_in: float = LABEL_WIDTH_IN,
              height_in: float = LABEL_HEIGHT_IN,
              shift_down: int = SHIFT_DOWN_DOTS,
              shift_right: int = SHIFT_RIGHT_DOTS) -> str:
    def clean(value, fallback="-"):
        # ZPL control characters would break the label format.
        text = str(value or fallback)
        return text.replace("^", " ").replace("~", " ").strip()

    pw, ll = label_dots(width_in, height_in)

    label = clean(job.get("label_name"), fallback="")
    placement = (job.get("label_placement") or "header").strip().lower()
    sku_text = clean(job.get("sku"))
    if label and placement == "sku":
        # Preferred name replaces the SKU line; the store header stays.
        header = HEADER_STORE.format(pw=pw)
        sku_text = label[:56]
    elif label and placement == "both":
        # Same name top and centre — for products whose SKU means nothing
        # to a picker (short numeric SKUs).
        size = 28 if len(label) <= 26 else 20 if len(label) <= 56 else 16
        header = f"^CF0,{size}\n^FO0,4^FB{pw},2,0,C^FD{label[:76]}^FS\n"
        sku_text = label[:56]
    elif label:
        # Preferred name replaces the store header. Font steps DOWN with
        # length: ZPL's ^FB does NOT clip text past its max line count —
        # it overprints the last line — so the name must genuinely fit in
        # two lines at the chosen size.
        if len(label) <= 26:
            size = 28
        elif len(label) <= 56:
            size = 20
        else:
            size = 16
        header = f"^CF0,{size}\n^FO0,4^FB{pw},2,0,C^FD{label[:76]}^FS\n"
    else:
        header = HEADER_STORE.format(pw=pw)

    # Both lines customized with DIFFERENT text: label_name (placement
    # header) drew the top above, and this explicit centre line wins over
    # the SKU. Jobs without label_sku behave exactly as before.
    label_sku = clean(job.get("label_sku"), fallback="")
    if label_sku:
        sku_text = label_sku[:56]

    # A sealed case carries ONE tag but holds several units, so the centre
    # line has to say so ("8 x 93581") — otherwise the sticker reads as a
    # single item. Applied last, after any preferred name has had its say.
    case_units = job.get("case_units")
    if case_units:
        sku_text = f"{case_units} x {sku_text}"[:56]

    # Every path caps at 56 (the plain-SKU path was uncapped and could
    # overrun even two wrapped lines); then pick one big line or the
    # two-line wrap by measured width. The wrap keeps font 30 whenever
    # two lines hold the text, stepping down only as far as needed.
    sku_text = sku_text[:56]
    manual_break = "|" in sku_text
    plain = " ".join(sku_text.replace("|", " ").split())
    sku_wrapped = manual_break or not _sku_fits(plain, 30, 1, pw)
    if not sku_wrapped:
        sku_line = SKU_LINE_ONE.format(pw=pw, sku=plain)
    else:
        fw = pw - 2 * SKU_WRAP_MARGIN
        split = _sku_split(sku_text)
        if split:
            left, right = split
            wf = 30
            while wf > 20 and not (_sku_line_fits(left, wf, pw)
                                   and _sku_line_fits(right, wf, pw)):
                wf -= 2
            sku_line = SKU_LINE_WRAP.format(
                pw=pw, f=wf, m=SKU_WRAP_MARGIN, fw=fw,
                sku=f"{left}\\&{right}",
            )
        else:
            # No separator anywhere (one solid token): ZPL auto-wrap.
            wf = 30
            while wf > 20 and not _sku_fits(plain, wf, 2, pw):
                wf -= 2
            sku_line = SKU_LINE_WRAP.format(
                pw=pw, f=wf, m=SKU_WRAP_MARGIN, fw=fw, sku=plain,
            )

    barcode = clean(job.get("barcode"), fallback="")
    if not barcode:
        # No barcode on file: encode the SKU instead — the app's scan field
        # accepts SKUs, so scanning this label still resolves the product.
        barcode = clean(job.get("sku"), fallback="")
    barcode_line = ""
    if barcode:
        # Prefer 2-dot modules; long codes (like alphanumeric SKUs) drop to
        # 1-dot so they still fit with quiet zones inside the label width.
        module = 2
        width = _code128_width_dots(barcode, module)
        if width > pw - 24:
            module = 1
            width = _code128_width_dots(barcode, module)
        bx = max(2, (pw - width) // 2)
        geo = BARCODE_GEO_WRAPPED if sku_wrapped else BARCODE_GEO_NORMAL
        barcode_line = BARCODE_LINE.format(
            bx=bx, barcode=barcode, module=module, pw=pw, **geo
        )
    # Some products are one item split across shelves. The label leads with
    # the bin these boxes are on and names the others, so a picker chasing
    # the rest of the item isn't left guessing.
    bin_text = clean(job.get("bin_location"))
    others = clean(job.get("other_bins"), fallback="")
    if others:
        bin_line = (
            f"^CF0,22\n^FO0,{ll - 52}^FB{pw},2,0,C"
            f"^FDBIN: {bin_text}. Other: {others[:60]}^FS\n"
        )
    else:
        bin_line = (
            f"^CF0,30\n^FO0,{ll - 45}^FB{pw},1,0,C^FDBIN: {bin_text}^FS\n"
        )

    return LABEL_ZPL.format(
        rfid_setup=RFID_ZPL.format(epc=job["epc"]) if encode_rfid else "",
        pw=pw,
        ll=ll,
        sd=shift_down,
        sr=shift_right,
        header=header,
        sku_line=sku_line,
        barcode_line=barcode_line,
        bin_line=bin_line,
    )


# ------------------------------------------------------------- logging ------
# The agent OWNS its log now (v6): the old runner's `>> log 2>&1` shell
# redirect could never rotate (cmd holds the handle) and grew without
# bound. Every print tees to the console AND the file; the file rotates
# to .old at 3MB. The get-log cloud command reads the same file.
LOG_ROTATE_BYTES = 3 * 1024 * 1024
_log_path: str | None = None


class _Tee(io.TextIOBase):
    def __init__(self, *streams):
        self.streams = streams

    def write(self, s):
        for stream in self.streams:
            try:
                stream.write(s)
            except Exception:  # noqa: BLE001 - a dead console never kills us
                pass
        return len(s)

    def flush(self):
        for stream in self.streams:
            try:
                stream.flush()
            except Exception:  # noqa: BLE001
                pass


def setup_logging(path: str | None) -> None:
    global _log_path
    if not path:
        try:
            sys.stdout.reconfigure(line_buffering=True)
        except Exception:  # noqa: BLE001
            pass
        return
    _log_path = path
    _rotate_log_if_big()
    handle = open(path, "a", encoding="utf-8", buffering=1)
    tee = _Tee(sys.stdout, handle)
    sys.stdout = tee
    sys.stderr = tee


def _rotate_log_if_big() -> None:
    if not _log_path:
        return
    try:
        if os.path.getsize(_log_path) > LOG_ROTATE_BYTES:
            os.replace(_log_path, _log_path + ".old")
    except OSError:
        pass


def log(msg: str) -> None:
    print(f"{time.strftime('%m-%d %H:%M:%S')} {msg}")


def tail_log(lines: int = 100) -> str:
    if not _log_path:
        return "(no --log-file configured)"
    try:
        with open(_log_path, "r", encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-max(1, min(lines, 400)):])
    except OSError as error:
        return f"(could not read log: {error})"


# ------------------------------------------------------ printer status ------
def parse_hs(raw: bytes) -> dict | None:
    """Parse a ZPL ~HS host-status reply (three <STX>csv<ETX> strings).

    String 1 carries paper-out, pause, formats-in-buffer and buffer-full;
    string 2 carries head-open, ribbon-out and thermal-transfer mode.
    None when the reply doesn't look like a host status at all."""
    if not raw:
        return None
    text = raw.decode("ascii", "replace")
    rows = []
    for piece in text.replace("\x02", "").split("\x03"):
        piece = piece.strip()
        if piece and "," in piece:
            rows.append(piece.split(","))
    if not rows or len(rows[0]) < 6:
        return None

    def num(row, i):
        try:
            return int(row[i])
        except (IndexError, ValueError):
            return None

    s1 = rows[0]
    st = {
        "paper_out": num(s1, 1) == 1,
        "paused": num(s1, 2) == 1,
        "formats": num(s1, 4),
        "buffer_full": num(s1, 5) == 1,
        "corrupt_ram": num(s1, 9) == 1,
        "under_temp": num(s1, 10) == 1,
        "over_temp": num(s1, 11) == 1,
    }
    if len(rows) > 1 and len(rows[1]) >= 9:
        s2 = rows[1]
        st["head_open"] = num(s2, 2) == 1
        st["ribbon_out"] = num(s2, 3) == 1
        st["thermal_transfer"] = num(s2, 4) == 1
        st["labels_remaining"] = num(s2, 8)
    return st


def active_faults(st: dict | None) -> list[str]:
    """The status flags worth HOLDING the queue for. under_temp is
    excluded on purpose: a cold-morning printhead still prints (it heats
    as it goes) - holding on it would deadlock winter mornings."""
    if not st:
        return []
    faults = []
    if st.get("paper_out"):
        faults.append("media out")
    if st.get("head_open"):
        faults.append("head open")
    if st.get("paused"):
        faults.append("paused")
    if st.get("ribbon_out") and st.get("thermal_transfer"):
        faults.append("ribbon out")
    if st.get("buffer_full"):
        faults.append("buffer full")
    if st.get("over_temp"):
        faults.append("printhead too hot")
    if st.get("corrupt_ram"):
        faults.append("corrupt RAM")
    return faults


# ---------------------------------------------------------- transports ------
# Every transport: send(zpl str). Readable transports also implement
# query(bytes)->bytes, status()->dict|None, label_count()->int|None and
# carry .readback ("counter" > "status" > "none") after probe_readback.

SGD_LABEL_COUNT = b'! U1 getvar "odometer.total_label_count"\r\n'


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("InterfaceClassGuid", _GUID),
        ("Flags", ctypes.c_uint32),
        ("Reserved", ctypes.c_void_p),
    ]


class _OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ("Internal", ctypes.c_void_p),
        ("InternalHigh", ctypes.c_void_p),
        ("Offset", ctypes.c_uint32),
        ("OffsetHigh", ctypes.c_uint32),
        ("hEvent", ctypes.c_void_p),
    ]


_k32 = None
_setupapi = None


def _bind_win32() -> None:
    """Load kernel32/setupapi with use_last_error=True (GetLastError
    read through plain windll can be clobbered by ctypes housekeeping -
    fatal here, where ERROR_IO_PENDING is the NORMAL write path) and set
    argtypes/restype on every call. Without those, 64-bit HANDLEs
    round-trip through 32-bit C ints and get silently truncated - the
    classic ctypes-on-x64 heisenbug."""
    global _k32, _setupapi
    if _k32 is not None:
        return
    p, u32, i32 = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    sd = ctypes.WinDLL("setupapi", use_last_error=True)
    sd.SetupDiGetClassDevsW.restype = p
    sd.SetupDiGetClassDevsW.argtypes = [p, ctypes.c_wchar_p, p, u32]
    sd.SetupDiEnumDeviceInterfaces.restype = i32
    sd.SetupDiEnumDeviceInterfaces.argtypes = [p, p, p, u32, p]
    sd.SetupDiGetDeviceInterfaceDetailW.restype = i32
    sd.SetupDiGetDeviceInterfaceDetailW.argtypes = [p, p, p, u32, p, p]
    sd.SetupDiDestroyDeviceInfoList.argtypes = [p]
    k32.CreateFileW.restype = p
    k32.CreateFileW.argtypes = [ctypes.c_wchar_p, u32, u32, p, u32, u32, p]
    k32.WriteFile.restype = i32
    k32.WriteFile.argtypes = [p, p, u32, p, p]
    k32.ReadFile.restype = i32
    k32.ReadFile.argtypes = [p, p, u32, p, p]
    k32.CreateEventW.restype = p
    k32.CreateEventW.argtypes = [p, i32, i32, ctypes.c_wchar_p]
    k32.WaitForSingleObject.restype = u32
    k32.WaitForSingleObject.argtypes = [p, u32]
    k32.GetOverlappedResult.restype = i32
    k32.GetOverlappedResult.argtypes = [p, p, p, i32]
    k32.CancelIoEx.restype = i32
    k32.CancelIoEx.argtypes = [p, p]
    k32.CloseHandle.argtypes = [p]
    _k32, _setupapi = k32, sd


def list_usb_print_devices() -> list[str]:
    """Device paths of every present usbprint.sys interface (USB
    printers), via SetupDi. The Zebra shows up here whether or not the
    Windows spooler knows anything about it."""
    _bind_win32()
    sd = _setupapi
    guid = _GUID(
        0x28D78FAD, 0x5A12, 0x11D1,
        (ctypes.c_ubyte * 8)(0xAE, 0x5B, 0x00, 0x00, 0xF8, 0x03, 0xA8, 0xC2),
    )
    DIGCF_PRESENT, DIGCF_DEVICEINTERFACE = 0x2, 0x10
    hdev = sd.SetupDiGetClassDevsW(
        ctypes.byref(guid), None, None,
        DIGCF_PRESENT | DIGCF_DEVICEINTERFACE,
    )
    if not hdev or hdev == ctypes.c_void_p(-1).value:
        return []
    paths = []
    try:
        index = 0
        while True:
            did = _SP_DEVICE_INTERFACE_DATA()
            did.cbSize = ctypes.sizeof(_SP_DEVICE_INTERFACE_DATA)
            if not sd.SetupDiEnumDeviceInterfaces(
                hdev, None, ctypes.byref(guid), index, ctypes.byref(did)
            ):
                break
            index += 1
            needed = ctypes.c_uint32(0)
            sd.SetupDiGetDeviceInterfaceDetailW(
                hdev, ctypes.byref(did), None, 0,
                ctypes.byref(needed), None,
            )
            if not needed.value:
                continue
            buf = ctypes.create_string_buffer(needed.value + 16)
            # cbSize is the STRUCT's size (DWORD + one WCHAR, padded):
            # 8 on 64-bit Python, 6 on 32-bit. Not the buffer's length.
            struct.pack_into(
                "<I", buf, 0, 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            )
            if sd.SetupDiGetDeviceInterfaceDetailW(
                hdev, ctypes.byref(did), buf, needed.value + 16, None, None
            ):
                paths.append(ctypes.wstring_at(ctypes.addressof(buf) + 4))
    finally:
        sd.SetupDiDestroyDeviceInfoList(hdev)
    return paths


class UsbTransport:
    """Direct, EXCLUSIVE I/O with the Zebra's USB printer interface.

    Why this exists (2026-09-23): with the spooler path, whole ZPL
    documents kept vanishing between "spooler says printed" and the
    paper - 9 of 24, then 5 of 15, then 2 of 10 paced singles - with
    the printer healthy. Prime suspects were the driver's bidi status
    polling, ShipStation Connect grabbing the device, and USB selective
    suspend; all three share one fix: hold the device handle ourselves,
    exclusively, and never hand bytes to the spooler stack at all.
    A side benefit is a READ channel: ~HS status + the odometer make
    "printed" a fact reported by the printer instead of a guess."""

    ERROR_IO_PENDING = 997
    ERROR_OPERATION_ABORTED = 995

    def __init__(self, match: str = "vid_0a5f"):
        _bind_win32()
        self.match = (match or "vid_0a5f").lower()
        self.handle = None
        self.path = None
        self.readback = "none"
        self.supports_count = False
        self._open()

    # ---- lifecycle ----
    def _open(self) -> None:
        devices = list_usb_print_devices()
        wanted = [d for d in devices if self.match in d.lower()]
        if not wanted:
            raise OSError(
                f"no USB printer matching '{self.match}' "
                f"({len(devices)} usbprint device(s) present)"
            )
        self.path = wanted[0]
        GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
        OPEN_EXISTING, FILE_FLAG_OVERLAPPED = 3, 0x40000000
        handle = _k32.CreateFileW(
            self.path, GENERIC_READ | GENERIC_WRITE, 0, None,
            OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None,
        )
        if not handle or handle == ctypes.c_void_p(-1).value:
            err = ctypes.get_last_error()
            hint = (
                " - another program holds the printer open "
                "(ShipStation Connect? the spooler mid-job?)"
                if err in (5, 32) else ""
            )
            raise OSError(f"CreateFile on the Zebra failed (error {err}){hint}")
        self.handle = handle

    def close(self) -> None:
        if self.handle:
            try:
                _k32.CloseHandle(self.handle)
            except Exception:  # noqa: BLE001
                pass
            self.handle = None

    def reconnect(self) -> None:
        self.close()
        time.sleep(1.0)
        self._open()

    # ---- raw I/O (all overlapped: usbprint has no timeouts of its own,
    # and a synchronous ReadFile with no data would hang forever) ----
    def _io(self, is_read: bool, payload, timeout_ms: int):
        ov = _OVERLAPPED()
        ov.hEvent = _k32.CreateEventW(None, 1, 0, None)
        if not ov.hEvent:
            raise OSError("CreateEvent failed")
        try:
            done = ctypes.c_uint32(0)
            if is_read:
                buf = ctypes.create_string_buffer(payload)
                ok = _k32.ReadFile(
                    self.handle, buf, payload,
                    ctypes.byref(done), ctypes.byref(ov),
                )
            else:
                buf = payload
                ok = _k32.WriteFile(
                    self.handle, payload, len(payload),
                    ctypes.byref(done), ctypes.byref(ov),
                )
            if not ok:
                err = ctypes.get_last_error()
                if err != self.ERROR_IO_PENDING:
                    raise OSError(f"USB {'read' if is_read else 'write'} "
                                  f"error {err}")
                if _k32.WaitForSingleObject(ov.hEvent, timeout_ms) != 0:
                    _k32.CancelIoEx(self.handle, ctypes.byref(ov))
                    _k32.WaitForSingleObject(ov.hEvent, 2000)
                    return None  # timed out
                if not _k32.GetOverlappedResult(
                    self.handle, ctypes.byref(ov), ctypes.byref(done), 0
                ):
                    err = ctypes.get_last_error()
                    if err == self.ERROR_OPERATION_ABORTED:
                        return None
                    raise OSError(f"USB {'read' if is_read else 'write'} "
                                  f"completion error {err}")
            if is_read:
                return buf.raw[: done.value]
            return done.value
        finally:
            _k32.CloseHandle(ov.hEvent)

    def write(self, data: bytes, timeout_ms: int = 10000) -> None:
        offset = 0
        while offset < len(data):
            chunk = data[offset: offset + 16384]
            written = self._io(False, chunk, timeout_ms)
            if written is None:
                # THE core failure mode this rewrite exists to expose:
                # the device stopped taking data. Loud, never silent.
                raise OSError("USB write stalled - the printer stopped "
                              "accepting data")
            offset += written or 0
            if not written:
                raise OSError("USB write wrote 0 bytes")

    def _read(self, timeout_ms: int) -> bytes:
        return self._io(True, 1024, timeout_ms) or b""

    def _drain_input(self) -> None:
        for _ in range(5):
            if not self._read(30):
                break

    def query(self, cmd: bytes, first_timeout_ms: int = 1500) -> bytes:
        self._drain_input()
        self.write(cmd)
        out = b""
        timeout = first_timeout_ms
        while len(out) < 8192:
            chunk = self._read(timeout)
            if not chunk:
                break
            out += chunk
            timeout = 300  # already talking; stop at the first quiet gap
        return out

    # ---- printer facts ----
    def status(self) -> dict | None:
        return parse_hs(self.query(b"~HS"))

    def label_count(self) -> int | None:
        raw = self.query(SGD_LABEL_COUNT)
        m = re.search(rb'"?\s*(\d+)\s*"?', raw)
        return int(m.group(1)) if m else None

    def send(self, zpl: str) -> None:
        self.write(zpl.encode("utf-8"))

    def describe(self) -> str:
        tail = (self.path or "").split("#")[1] if self.path and "#" in (
            self.path or "") else self.path
        return f"usb-direct ({tail})"


class NetworkTransport:
    """Raw ZPL over TCP 9100, one connection per operation. Every
    network-capable Zebra supports this, and most answer ~HS on the
    same port - so network printers get truthful dones too."""

    def __init__(self, host: str, port: int = 9100):
        self.host, self.port = host, port
        self.readback = "none"
        self.supports_count = False

    def send(self, zpl: str) -> None:
        with socket.create_connection((self.host, self.port),
                                      timeout=10) as conn:
            conn.sendall(zpl.encode("utf-8"))

    def query(self, cmd: bytes, first_timeout_ms: int = 2000) -> bytes:
        try:
            with socket.create_connection((self.host, self.port),
                                          timeout=5) as conn:
                conn.sendall(cmd)
                conn.settimeout(first_timeout_ms / 1000)
                out = b""
                while len(out) < 8192:
                    try:
                        chunk = conn.recv(1024)
                    except socket.timeout:
                        break
                    if not chunk:
                        break
                    out += chunk
                    conn.settimeout(0.4)
                return out
        except OSError:
            return b""

    def status(self) -> dict | None:
        return parse_hs(self.query(b"~HS"))

    def label_count(self) -> int | None:
        raw = self.query(SGD_LABEL_COUNT)
        m = re.search(rb'"?\s*(\d+)\s*"?', raw)
        return int(m.group(1)) if m else None

    def describe(self) -> str:
        return f"network ({self.host}:{self.port})"


class SpoolerTransport:
    """The v5 path: raw ZPL through the installed Windows driver. Write-
    only - "done" means the spooler took it, which the 2026-09 drops
    proved can be a lie. Kept as the fallback when the direct USB open
    fails (device busy, exotic setups)."""

    def __init__(self, printer_name: str):
        self.printer_name = printer_name
        self.readback = "none"
        self.supports_count = False

    def send(self, zpl: str) -> None:
        send_windows(zpl, self.printer_name)

    def status(self) -> dict | None:
        return None

    def label_count(self) -> int | None:
        return None

    def query(self, cmd: bytes, first_timeout_ms: int = 0) -> bytes:
        raise OSError("the Windows spooler path has no read channel")

    def describe(self) -> str:
        return f"spooler ({self.printer_name})"


class DryRunTransport:
    def __init__(self):
        self.readback = "none"
        self.supports_count = False

    def send(self, zpl: str) -> None:
        print(zpl)

    def status(self) -> dict | None:
        return None

    def label_count(self) -> int | None:
        return None

    def query(self, cmd: bytes, first_timeout_ms: int = 0) -> bytes:
        raise OSError("dry-run has no printer to query")

    def describe(self) -> str:
        return "dry-run"


def probe_readback(tr) -> None:
    """Ask the transport's printer what it can tell us: 'counter' (the
    odometer answers - physical prints are countable), 'status' (~HS
    answers - faults and buffer are visible), or 'none' (fire and
    hope, the v5 behavior)."""
    tr.readback = "none"
    tr.supports_count = False
    try:
        st = tr.status()
    except Exception:  # noqa: BLE001
        st = None
    if not st:
        return
    tr.readback = "status"
    try:
        count = tr.label_count()
    except Exception:  # noqa: BLE001
        count = None
    if count is not None:
        tr.readback = "counter"
        tr.supports_count = True


# --------------------------------------------- Windows spooler utilities ----
def windows_queue_health(printer_name: str):
    """(jobs waiting, oldest job's age in seconds) for the WINDOWS queue
    of this printer - the wedge detector for SPOOLER mode. Direct-USB
    mode never puts jobs there, so it skips this. (None, None) when it
    can't be read."""
    try:
        import win32print
        handle = win32print.OpenPrinter(printer_name)
        try:
            jobs = win32print.EnumJobs(handle, 0, 999, 1)
        finally:
            win32print.ClosePrinter(handle)
        if not jobs:
            return 0, 0
        oldest = None
        for j in jobs:
            sub = j.get("Submitted")
            if sub is None:
                continue
            ts = sub.timestamp()
            if oldest is None or ts < oldest:
                oldest = ts
        age = 0 if oldest is None else max(0, int(time.time() - oldest))
        return len(jobs), age
    except Exception:  # noqa: BLE001 - health is decoration, never fatal
        return None, None


def purge_windows_queue(printer_name: str) -> int:
    """Delete every job in this printer's WINDOWS queue (the 'Clear
    stuck jobs' button). Server-side those jobs are long marked done -
    anything missing reprints from the Queue tab in seconds."""
    import win32print
    deleted = 0
    handle = win32print.OpenPrinter(printer_name)
    try:
        for j in win32print.EnumJobs(handle, 0, 999, 1):
            try:
                win32print.SetJob(handle, j["JobId"], 0, None,
                                  win32print.JOB_CONTROL_DELETE)
                deleted += 1
            except Exception as error:  # noqa: BLE001 - best effort
                log(f"! could not delete Windows job {j['JobId']}: {error}")
    finally:
        win32print.ClosePrinter(handle)
    return deleted


def send_windows(zpl: str, printer_name: str) -> None:
    """Raw ZPL through the installed Windows driver (USB printers)."""
    try:
        import win32print
    except ImportError:
        sys.exit("Spooler mode needs pywin32:  py -m pip install pywin32")
    handle = win32print.OpenPrinter(printer_name)
    try:
        win32print.StartDocPrinter(handle, 1, ("RFID label", None, "RAW"))
        win32print.StartPagePrinter(handle)
        win32print.WritePrinter(handle, zpl.encode("utf-8"))
        win32print.EndPagePrinter(handle)
        win32print.EndDocPrinter(handle)
    finally:
        win32print.ClosePrinter(handle)


# --------------------------------------------------------------- app I/O ----
class AppClient:
    def __init__(self, base_url: str, agent_key: str | None,
                 printer_id: str | None = None,
                 printer_kind: str | None = None):
        self.base = base_url.rstrip("/")
        self.headers = {"X-Agent-Key": agent_key} if agent_key else {}
        # Identifies this printer to the app: claims register it in the
        # Scan Station's printer picker and only take jobs aimed at it
        # (or at no printer). Without an id the agent claims everything —
        # the pre-picker behavior.
        self.printer_id = printer_id
        self.printer_kind = printer_kind

    def claim(self, limit: int = 5) -> list[dict]:
        params: dict = {"limit": limit}
        if self.printer_id:
            params["printer"] = self.printer_id
            if self.printer_kind:
                params["kind"] = self.printer_kind
        r = requests.post(
            f"{self.base}/api/print-jobs/claim",
            params=params,
            headers=self.headers,
            timeout=30,
        )
        r.raise_for_status()
        return r.json()["jobs"]

    def heartbeat(self, payload: dict) -> dict:
        """v6's combined pulse: posts printer status, receives commands
        + the server's current agent version (the self-update signal).
        Falls back to the v5 commands endpoint against an older app."""
        try:
            r = requests.post(
                f"{self.base}/api/print-agent/heartbeat",
                json=payload,
                headers=self.headers,
                timeout=30,
            )
            if r.status_code == 404:
                return {
                    "commands": self.claim_commands(
                        payload.get("win_jobs"), payload.get("win_oldest_s")
                    ),
                    "latest_version": None,
                }
            r.raise_for_status()
            return r.json()
        except requests.RequestException as error:
            log(f"! heartbeat failed: {error}")
            return {"commands": [], "latest_version": None}

    def claim_commands(self, win_jobs=None, win_oldest_s=None) -> list[dict]:
        """The v5 command poll - only used against a server too old for
        /heartbeat."""
        try:
            params: dict = {"agent_version": AGENT_VERSION}
            if self.printer_id:
                params["printer"] = self.printer_id
            if win_jobs is not None:
                params["win_jobs"] = win_jobs
            if win_oldest_s is not None:
                params["win_oldest_s"] = win_oldest_s
            r = requests.post(
                f"{self.base}/api/printer-commands/claim",
                params=params,
                headers=self.headers,
                timeout=30,
            )
            if r.status_code == 404:
                return []
            r.raise_for_status()
            return r.json().get("commands", [])
        except requests.RequestException:
            return []

    def post_result(self, command_id: str, ok: bool, output: str) -> None:
        """Best-effort: a lost result never blocks printing."""
        try:
            requests.post(
                f"{self.base}/api/print-agent/command-result",
                json={"id": command_id, "ok": ok, "output": output[:8000]},
                headers=self.headers,
                timeout=30,
            )
        except requests.RequestException as error:
            log(f"! could not post command result: {error}")

    def download_script(self) -> str:
        r = requests.get(
            f"{self.base}/api/print-agent/script",
            headers=self.headers,
            timeout=60,
        )
        r.raise_for_status()
        return r.text

    def complete(self, job_id: int, create_assignment: bool) -> None:
        requests.post(
            f"{self.base}/api/print-jobs/{job_id}/complete",
            params={"create_assignment": str(create_assignment).lower()},
            headers=self.headers,
            timeout=30,
        ).raise_for_status()

    def fail(self, job_id: int, error: str) -> None:
        requests.post(
            f"{self.base}/api/print-jobs/{job_id}/fail",
            json={"error": error[:500]},
            headers=self.headers,
            timeout=30,
        ).raise_for_status()


# ------------------------------------------------------------- the agent ----
LABEL_SETTLE_TIMEOUT = 45  # s a healthy printer gets to drain one format
UPDATE_COOLDOWN = 300      # s between self-update attempts


class Agent:
    def __init__(self, args, client: AppClient, transport):
        self.args = args
        self.client = client
        self.tr = transport
        self.encode_rfid = not args.no_rfid
        self.counters = {"done": 0, "failed": 0, "retried": 0,
                        "vanished": 0}
        self.fault: str | None = None
        self.holding = 0
        self.last_error: str | None = None
        self.last_print_at: float | None = None
        self._last_update_try = 0.0
        self._usb_retry_at = 0.0
        self._cycles = 0

    # ---- printing --------------------------------------------------------
    def print_raw(self, zpl: str) -> None:
        self.tr.send(zpl)

    def _wait_ready(self) -> None:
        """Block while the PRINTER reports a fault, keeping the server
        informed (and commands flowing) the whole time. This is what
        turns 'media out' from 24 falsely-done labels into a Queue tab
        that says so and a run that resumes by itself."""
        if self.tr.readback == "none":
            return
        misses = 0
        while True:
            try:
                st = self.tr.status()
            except OSError:
                raise
            if st is None:
                misses += 1
                if misses >= 6:
                    raise OSError("printer stopped answering ~HS")
                time.sleep(1)
                continue
            misses = 0
            faults = active_faults(st)
            if not faults:
                if self.fault:
                    log(f"  printer recovered ({self.fault} cleared)")
                self.fault = None
                return
            fault = ", ".join(faults)
            if fault != self.fault:
                log(f"! printer FAULTED: {fault} - holding "
                    f"{self.holding} label(s) until it clears")
                self.fault = fault
            self._pulse(status=st)
            time.sleep(3)

    def _deliver_confirmed(self, zpl: str) -> str:
        """Send one label and watch the printer take it. Returns
        'confirmed' (odometer moved), 'drained' (buffer emptied, no
        counter on this printer), 'vanished' (buffer empty but the
        counter never moved - the label was eaten), or 'stalled'."""
        base = self.tr.label_count() if self.tr.supports_count else None
        self.tr.send(zpl)
        deadline = time.time() + LABEL_SETTLE_TIMEOUT
        saw_format = False
        misses = 0
        while time.time() < deadline:
            time.sleep(0.4)
            st = self.tr.status()
            if st is None:
                misses += 1
                if misses >= 6:
                    raise OSError("printer stopped answering ~HS mid-print")
                continue
            misses = 0
            faults = active_faults(st)
            if faults:
                # Faulted mid-label (media ran out under the burst):
                # hold right here - the format is still buffered and
                # prints the moment the fault clears.
                fault = ", ".join(faults)
                if fault != self.fault:
                    log(f"! printer FAULTED mid-label: {fault} - waiting")
                    self.fault = fault
                self._pulse(status=st)
                deadline = time.time() + LABEL_SETTLE_TIMEOUT
                time.sleep(2.5)
                continue
            if self.fault:
                log(f"  printer recovered ({self.fault} cleared)")
                self.fault = None
            if (st.get("formats") or 0) > 0:
                saw_format = True
                deadline = time.time() + LABEL_SETTLE_TIMEOUT
                continue
            # Buffer drained. On a counter printer, believe the counter.
            if base is None:
                return "drained"
            count = self.tr.label_count()
            if count is not None and count > base:
                return "confirmed"
            # Give a just-finished label a beat to hit the odometer
            # before calling it eaten.
            time.sleep(1.2)
            count = self.tr.label_count()
            if count is not None and count > base:
                return "confirmed"
            if count is None:
                return "drained"
            return "vanished"
        return "stalled" if saw_format else "vanished"

    def _print_job(self, job: dict) -> None:
        desc = f"job {job['id']} ({job.get('sku') or job.get('barcode')})"
        zpl = build_zpl(
            job, self.encode_rfid, self.args.label_width,
            self.args.label_height, self.args.shift_down,
            self.args.shift_right,
        )
        try:
            if self.tr.readback == "none":
                # v5 semantics: done = the transport took the bytes.
                self.tr.send(zpl)
                self.client.complete(job["id"],
                                     create_assignment=self.encode_rfid)
                self._mark_done(desc)
                return
            for attempt in range(1, 4):
                self._wait_ready()
                outcome = self._deliver_confirmed(zpl)
                if outcome in ("confirmed", "drained"):
                    self.client.complete(job["id"],
                                         create_assignment=self.encode_rfid)
                    self._mark_done(desc, outcome)
                    return
                if outcome == "vanished":
                    self.counters["vanished"] += 1
                    self.counters["retried"] += 1
                    log(f"! {desc}: the printer swallowed the label "
                        f"(buffer drained, odometer never moved) - "
                        f"attempt {attempt} of 3")
                    continue
                # stalled: a format sat in the buffer past the timeout
                # with no fault flag. Don't blind-retry (it may still
                # print) - fail loud so the queue shows it.
                self._mark_failed(job, desc,
                                  "printer stalled mid-label (format stuck "
                                  "in buffer, no fault reported) - check "
                                  "the printer, then reprint")
                return
            self._mark_failed(job, desc,
                              "printer swallowed this label 3 times "
                              "(status healthy, odometer never moved) - "
                              "recalibrate media, then reprint")
        except OSError as error:
            self.last_error = str(error)
            log(f"! {desc}: {error}")
            self._transport_trouble()
            try:
                self.client.fail(job["id"], str(error))
            except requests.RequestException:
                pass
            self.counters["failed"] += 1
        except requests.RequestException as error:
            # The label may be on paper but the app unreachable - do NOT
            # fail the job (that voids a printed label); leave it
            # claimed, the operator reprints if it truly vanished.
            self.last_error = str(error)
            log(f"! {desc}: printed but couldn't report back: {error}")

    def _mark_done(self, desc: str, outcome: str = "sent") -> None:
        self.counters["done"] += 1
        self.last_print_at = time.time()
        suffix = {"confirmed": " [printer-confirmed]",
                  "drained": " [buffer drained]", "sent": ""}[outcome]
        log(f"  printed {desc}{suffix}" + (
            "" if self.encode_rfid else " (barcode only - scan tag to link)"
        ))

    def _mark_failed(self, job: dict, desc: str, why: str) -> None:
        self.counters["failed"] += 1
        self.last_error = why
        log(f"! failed {desc}: {why}")
        try:
            self.client.fail(job["id"], why)
        except requests.RequestException:
            pass

    # ---- transport resilience -------------------------------------------
    def _transport_trouble(self) -> None:
        """USB died mid-run: reconnect; if the device is gone, fall back
        to the spooler (when we know the Windows name) and keep trying
        to win the USB handle back every couple of minutes."""
        if not isinstance(self.tr, UsbTransport):
            return
        try:
            self.tr.reconnect()
            probe_readback(self.tr)
            log(f"  USB reconnected ({self.tr.describe()}, "
                f"readback: {self.tr.readback})")
            return
        except OSError as error:
            log(f"! USB reconnect failed: {error}")
        if self.args.printer_name:
            log("  falling back to the Windows spooler; will retry USB")
            self.tr = SpoolerTransport(self.args.printer_name)
            self._usb_retry_at = time.time() + 120

    def _maybe_retry_usb(self) -> None:
        if (isinstance(self.tr, SpoolerTransport)
                and self.args.transport in ("auto", "usb")
                and self._usb_retry_at
                and time.time() >= self._usb_retry_at):
            try:
                tr = UsbTransport(self.args.usb_device)
                probe_readback(tr)
                self.tr = tr
                self._usb_retry_at = 0.0
                log(f"  USB transport recovered ({tr.describe()}, "
                    f"readback: {tr.readback})")
            except OSError:
                self._usb_retry_at = time.time() + 120

    # ---- heartbeat + commands -------------------------------------------
    def _heartbeat_payload(self, status: dict | None) -> dict:
        payload = {
            "printer": self.client.printer_id,
            "version": AGENT_VERSION,
            "transport": self.tr.describe(),
            "readback": self.tr.readback,
            "fault": self.fault,
            "holding": self.holding,
            "counters": dict(self.counters),
            "last_error": self.last_error,
        }
        if status is not None:
            payload["status"] = {
                k: status.get(k)
                for k in ("formats", "paused", "paper_out", "head_open",
                          "labels_remaining")
            }
        if (isinstance(self.tr, SpoolerTransport)
                and not self.args.dry_run):
            jobs, oldest = windows_queue_health(self.tr.printer_name)
            payload["win_jobs"] = jobs
            payload["win_oldest_s"] = oldest
        return payload

    def _pulse(self, status: dict | None = None) -> dict:
        """One heartbeat: post state, run whatever commands came back.
        Called every cycle AND from inside fault-holds, so a restart or
        get-log always reaches the agent within seconds."""
        hb = self.client.heartbeat(self._heartbeat_payload(status))
        for cmd in hb.get("commands") or []:
            self._handle_command(cmd)
        latest = hb.get("latest_version")
        if (latest and latest != AGENT_VERSION
                and not self.args.no_auto_update):
            self._self_update(force=False)
        return hb

    def _handle_command(self, cmd: dict) -> None:
        kind = cmd.get("kind")
        cmd_id = cmd.get("id")
        payload = cmd.get("payload") or {}
        who = cmd.get("requested_by") or "?"
        log(f"  command '{kind}' (from {who})")
        ok, output = True, ""
        try:
            if kind == "feed":
                zpl = ("" if self.args.no_backfeed_fix
                       else BACKFEED_BEFORE_ZPL) + FEED_ZPL
                self.print_raw(zpl)
                output = "re-align feed sent (~PH)"
            elif kind == "purge":
                if self.args.printer_name and not self.args.dry_run:
                    n = purge_windows_queue(self.args.printer_name)
                    output = f"deleted {n} Windows job(s)"
                else:
                    output = ("no Windows queue here (direct/dry-run "
                              "mode prints nothing through the spooler)")
            elif kind == "shell":
                res = subprocess.run(
                    ["powershell", "-NoProfile", "-ExecutionPolicy",
                     "Bypass", "-Command", str(payload.get("cmd") or "")],
                    capture_output=True, text=True, errors="replace",
                    timeout=int(payload.get("timeout") or 180),
                )
                ok = res.returncode == 0
                output = (f"exit {res.returncode}\n"
                          + (res.stdout or "") + (res.stderr or ""))
            elif kind == "getlog":
                output = tail_log(int(payload.get("lines") or 100))
            elif kind == "query":
                raw = self.tr.query(
                    str(payload.get("cmd") or "~HS").encode("utf-8"),
                    first_timeout_ms=int(payload.get("timeout_ms") or 2000),
                )
                output = raw.decode("ascii", "replace") or "(no reply)"
            elif kind == "zpl":
                self.print_raw(str(payload.get("zpl") or ""))
                output = "ZPL sent"
            elif kind == "testlabel":
                self.print_raw(build_test_zpl(
                    self.args.label_width, self.args.label_height,
                    self.args.shift_down, self.args.shift_right,
                ))
                output = "alignment test label sent"
            elif kind == "update":
                if cmd_id:
                    self.client.post_result(cmd_id, True, "updating now")
                self._self_update(force=True)
                return  # only reached when the update was a no-op
            elif kind == "restart":
                if cmd_id:
                    self.client.post_result(cmd_id, True, "restarting")
                log("  restart command - exiting for the runner to relaunch")
                sys.exit(0)
            else:
                ok, output = False, f"unknown command kind '{kind}'"
        except SystemExit:
            raise
        except Exception as error:  # noqa: BLE001 - report, don't die
            ok, output = False, f"{type(error).__name__}: {error}"
        if cmd_id:
            self.client.post_result(cmd_id, ok, output)
        elif not ok:
            log(f"! command '{kind}' failed: {output}")

    # ---- self-update -----------------------------------------------------
    def _self_update(self, force: bool) -> None:
        """Download the server's print_agent.py, verify it compiles,
        swap it in, exit 0 - the looping runner relaunches the new
        code. THIS is what ends the remote-desktop era."""
        if not force and time.time() - self._last_update_try < UPDATE_COOLDOWN:
            return
        self._last_update_try = time.time()
        try:
            text = self.client.download_script()
        except requests.RequestException as error:
            log(f"! self-update: download failed: {error}")
            return
        m = re.search(r'^AGENT_VERSION\s*=\s*"([^"]*)"', text, re.M)
        new_version = m.group(1) if m else None
        if not force and new_version == AGENT_VERSION:
            return  # server serves what we already run
        path = os.path.abspath(__file__)
        staging = path + ".new"
        try:
            with open(staging, "w", encoding="utf-8", newline="") as f:
                f.write(text)
            import py_compile
            py_compile.compile(staging, doraise=True)
            os.replace(staging, path)
        except Exception as error:  # noqa: BLE001 - a bad download must
            # never brick the agent; the running copy stays in place.
            log(f"! self-update: rejected new script: {error}")
            try:
                os.remove(staging)
            except OSError:
                pass
            return
        log(f"self-update: {AGENT_VERSION} -> {new_version or '?'} - "
            f"exiting for the runner to relaunch")
        sys.exit(0)

    # ---- the loop --------------------------------------------------------
    def run(self) -> None:
        while True:
            self._cycles += 1
            if self._cycles % 200 == 0:
                _rotate_log_if_big()
            self._maybe_retry_usb()

            # Status first: it feeds the heartbeat AND lights the Queue
            # tab's FAULTED banner even when nothing is printing.
            status = None
            if self.tr.readback != "none":
                try:
                    status = self.tr.status()
                    self.fault = ", ".join(active_faults(status)) or None
                except OSError as error:
                    self.last_error = str(error)
                    log(f"! status query failed: {error}")
                    self._transport_trouble()

            self._pulse(status=status)

            try:
                jobs = self.client.claim()
            except requests.RequestException as error:
                log(f"! can't reach app: {error}")
                jobs = []

            if jobs and not self.args.no_backfeed_fix and not self.args.dry_run:
                # Re-assert backfeed-after at the head of every burst: it
                # does not survive a printer power cycle. Five bytes, no
                # motion, no waste.
                try:
                    self.print_raw(BACKFEED_BEFORE_ZPL)
                except Exception:  # noqa: BLE001 - jobs still get their shot
                    pass

            if jobs and self.args.realign_after_idle > 0 and not self.args.dry_run:
                idle = (time.time() - self.last_print_at
                        if self.last_print_at else None)
                if idle is None or idle >= self.args.realign_after_idle * 60:
                    try:
                        self.print_raw(FEED_ZPL)
                        log("  idle re-align: fed to the next label's home "
                            + ("(first burst since startup)" if idle is None
                               else f"({idle / 60:.0f} min idle)"))
                    except Exception as error:  # noqa: BLE001
                        log(f"! idle re-align failed: {error}")

            for i, job in enumerate(jobs):
                self.holding = len(jobs) - i
                self._print_job(job)
            self.holding = 0

            if self.args.once:
                break
            time.sleep(self.args.poll)


# ------------------------------------------------------------------ CLI -----
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--app",
        help="App base URL (defaults to PRINT_AGENT_APP_URL or APP_URL)",
    )
    parser.add_argument("--printer-host", help="Printer IP (network mode)")
    parser.add_argument("--printer-port", type=int, default=9100)
    parser.add_argument("--printer-name",
                        help="Windows printer name (spooler fallback/purge)")
    parser.add_argument("--agent-key", help="Matches app PRINT_AGENT_KEY")
    parser.add_argument(
        "--transport", choices=["auto", "usb", "spooler", "network"],
        default="auto",
        help="auto (default) = direct USB when a Zebra is on the bus, "
             "else the Windows spooler; usb/spooler force one; network "
             "follows --printer-host",
    )
    parser.add_argument(
        "--usb-device", default="vid_0a5f",
        help="Substring matched against usbprint device paths to pick "
             "the printer (default %(default)s = any Zebra)",
    )
    parser.add_argument(
        "--log-file",
        help="Agent-owned log with 3MB rotation (don't ALSO shell-"
             "redirect); the get-log cloud command reads this file",
    )
    parser.add_argument(
        "--no-auto-update", action="store_true",
        help="Never self-update, even when the server serves a newer "
             "print_agent.py",
    )
    parser.add_argument(
        "--printer-id",
        help="Name this printer registers under in the Scan Station's "
             "printer picker (e.g. warehouse-zebra). With an id set, this "
             "agent only claims jobs aimed at it or at no printer; "
             "without one it claims everything (old behavior).",
    )
    parser.add_argument(
        "--printer-kind",
        help="Descriptor shown on the picker card, e.g. "
             '"ZD621R · RFID encoder"',
    )
    parser.add_argument("--poll", type=float, default=3.0)
    parser.add_argument("--once", action="store_true", help="Single pass")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print ZPL to the terminal instead of a printer (still marks "
             "jobs done — use for wiring tests, not real stock)",
    )
    parser.add_argument(
        "--no-rfid", action="store_true",
        help="Printer has no RFID encoder (e.g. the ZD220t): print the "
             "barcode label only; link tags afterwards with the two-scan "
             "flow",
    )
    parser.add_argument("--label-width", type=float, default=LABEL_WIDTH_IN,
                        help="Sticker width in inches (default %(default)s)")
    parser.add_argument("--label-height", type=float, default=LABEL_HEIGHT_IN,
                        help="Sticker height in inches (default %(default)s)")
    parser.add_argument(
        "--test-label", action="store_true",
        help="Print one alignment box (no job needed) and exit",
    )
    parser.add_argument(
        "--no-backfeed-fix", action="store_true",
        help="Skip the startup ~JSA (backfeed after printing, the "
             "factory default). By default the agent re-asserts it so "
             "an old ~JSB never lingers in the printer.",
    )
    parser.add_argument(
        "--realign-after-idle", type=float, default=0, metavar="MIN",
        help="When the first print burst after MIN minutes of idle "
             "starts, feed to the next label's home first (~PH, gap-"
             "sensor re-registration). Costs ONE blank label per idle "
             "start, which is why it is OFF by default (0).",
    )
    parser.add_argument("--shift-down", type=int, default=SHIFT_DOWN_DOTS,
                        help="Move the whole image down N dots (203/inch; "
                             "default %(default)s)")
    parser.add_argument("--shift-right", type=int, default=SHIFT_RIGHT_DOTS,
                        help="Move the whole image right N dots "
                             "(default %(default)s)")
    return parser


def make_transport(args):
    """Pick the wire to the printer. auto: direct USB when a matching
    device is on the bus (the drop-proof path), else the spooler."""
    if args.dry_run:
        return DryRunTransport()
    if args.printer_host or args.transport == "network":
        if not args.printer_host:
            sys.exit("--transport network needs --printer-host")
        tr = NetworkTransport(args.printer_host, args.printer_port)
        probe_readback(tr)
        return tr
    if args.transport == "spooler":
        if not args.printer_name:
            sys.exit("--transport spooler needs --printer-name")
        return SpoolerTransport(args.printer_name)
    # auto / usb
    try:
        tr = UsbTransport(args.usb_device)
        probe_readback(tr)
        return tr
    except OSError as error:
        if args.transport == "usb" or not args.printer_name:
            sys.exit(f"direct USB unavailable: {error}")
        log(f"! direct USB unavailable ({error}) - falling back to the "
            f"Windows spooler")
        return SpoolerTransport(args.printer_name)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    setup_logging(args.log_file)

    if not args.app:
        args.app = os.getenv("PRINT_AGENT_APP_URL") or os.getenv("APP_URL")
    if not args.app:
        parser.error("need --app, PRINT_AGENT_APP_URL, or APP_URL")

    if not args.dry_run and not (args.printer_host or args.printer_name
                                 or args.transport in ("auto", "usb")):
        parser.error("need --printer-host, --printer-name, or --dry-run")

    transport = make_transport(args)

    if args.test_label:
        transport.send(build_test_zpl(
            args.label_width, args.label_height,
            args.shift_down, args.shift_right,
        ))
        print("Alignment test sent. The box should sit just inside the "
              "sticker edges; if not, adjust --label-width/--label-height "
              "or recalibrate the printer.")
        return

    encode_rfid = not args.no_rfid
    kind = args.printer_kind
    if not kind and args.printer_id:
        kind = ((args.printer_name or args.printer_host or "Zebra")
                + (" · RFID encoder" if encode_rfid else " · barcode only"))
    client = AppClient(args.app, args.agent_key, args.printer_id, kind)
    log(f"print agent v{AGENT_VERSION} watching {args.app} "
        f"({'DRY RUN' if args.dry_run else 'live'}, "
        f"{'RFID encode' if encode_rfid else 'barcode-only'}, "
        f"transport {transport.describe()}, "
        f"readback {transport.readback}). Ctrl+C to stop.")

    if not args.no_backfeed_fix and not args.dry_run:
        try:
            transport.send(BACKFEED_BEFORE_ZPL)
            log("  ~JSA sent (backfeed-after-print, the factory default)")
        except Exception as error:  # noqa: BLE001 — printing still works
            log(f"! could not send the backfeed setting: {error}")

    Agent(args, client, transport).run()


if __name__ == "__main__":
    main()
