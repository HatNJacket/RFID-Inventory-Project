"""No em dashes in user-facing copy (Nick: the "AI dash" - hyphen or
reword, 2026-08-24; app-wide sweep 2026-09-29). Scans only the text
INSIDE string literals (and HTML outside comments): comments and
docstrings may use them, and a lone "—" placeholder (an empty
table cell) is allowed - code compares against it.
"""
import io, io as _io, os, re, sys, tokenize
ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
DASH = "—"
fails = []
def check(l, c, x=""):
    print(("PASS  " if c else "FAIL  ") + l + ("" if c else f"  <- {x}"))
    if not c: fails.append(l)

# ------------------------------------------------------------- JS lexer ----
REGEX_PREV = set("(,=:[!&|?{};+-*%<>~^")
REGEX_WORDS = {"return", "typeof", "case", "in", "of", "new", "delete",
               "void", "throw", "instanceof", "yield", "await"}


def js_spans(src):
    spans = []
    n = len(src)

    def lex(i, stop_brace):
        prev_char = ";"
        prev_word = ""
        depth = 0
        while i < n:
            c = src[i]
            if c in " \t\r\n":
                i += 1
                continue
            if src.startswith("//", i):
                j = src.find("\n", i)
                i = n if j < 0 else j
                continue
            if src.startswith("/*", i):
                j = src.find("*/", i + 2)
                i = n if j < 0 else j + 2
                continue
            if c in "\"'":
                j = i + 1
                while j < n and src[j] != c and src[j] != "\n":
                    j += 2 if src[j] == "\\" else 1
                spans.append((i + 1, j))
                i = j + 1
                prev_char, prev_word = "a", ""
                continue
            if c == "`":
                i = template(i + 1)
                prev_char, prev_word = "a", ""
                continue
            if c == "/":
                if prev_char in REGEX_PREV or prev_word in REGEX_WORDS:
                    j = i + 1
                    in_class = False
                    while j < n and src[j] != "\n":
                        ch = src[j]
                        if ch == "\\":
                            j += 2
                            continue
                        if ch == "[":
                            in_class = True
                        elif ch == "]":
                            in_class = False
                        elif ch == "/" and not in_class:
                            break
                        j += 1
                    j += 1
                    while j < n and src[j].isalpha():
                        j += 1
                    i = j
                    prev_char, prev_word = "a", ""
                    continue
                prev_char, prev_word = "/", ""
                i += 1
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                if stop_brace and depth == 0:
                    return i + 1
                depth -= 1
            if c.isalnum() or c in "_$":
                j = i
                while j < n and (src[j].isalnum() or src[j] in "_$"):
                    j += 1
                prev_word = src[i:j]
                prev_char = "a"
                i = j
                continue
            prev_char = c
            prev_word = ""
            i += 1
        return i

    def template(i):
        seg_start = i
        while i < n:
            c = src[i]
            if c == "\\":
                i += 2
                continue
            if c == "`":
                spans.append((seg_start, i))
                return i + 1
            if src.startswith("${", i):
                spans.append((seg_start, i))
                i = lex(i + 2, True)
                seg_start = i
                continue
            i += 1
        return i

    lex(0, False)
    return spans


# ----------------------------------------------------------- Java lexer ----
def java_spans(src):
    spans = []
    i, n = 0, len(src)
    while i < n:
        if src.startswith("//", i):
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        c = src[i]
        if c in "\"'":
            j = i + 1
            while j < n and src[j] != c and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            if c == '"':
                spans.append((i + 1, j))
            i = j + 1
            continue
        i += 1
    return spans


# --------------------------------------------------------- Python pass ----
def py_spans(src):
    lines = src.splitlines(keepends=True)
    starts = [0]
    for ln in lines:
        starts.append(starts[-1] + len(ln))

    def off(pos):
        r, c = pos
        return starts[r - 1] + c

    spans = []
    triple_f = []
    toks = tokenize.generate_tokens(_io.StringIO(src).readline)
    fmid = getattr(tokenize, "FSTRING_MIDDLE", None)
    fstart = getattr(tokenize, "FSTRING_START", None)
    fend = getattr(tokenize, "FSTRING_END", None)
    tmid = getattr(tokenize, "TSTRING_MIDDLE", None)
    for tok in toks:
        if tok.type == tokenize.STRING:
            body = tok.string.lstrip("rRbBuUfF")
            if body.startswith('"""') or body.startswith("'''"):
                continue  # docstrings and big notes stay as written
            spans.append((off(tok.start), off(tok.end)))
        elif fstart is not None and tok.type == fstart:
            body = tok.string.lstrip("rRbBuUfF")
            triple_f.append(body.startswith('"""') or body.startswith("'''"))
        elif fend is not None and tok.type == fend:
            if triple_f:
                triple_f.pop()
        elif tok.type in (fmid, tmid) and tok.type is not None:
            if triple_f and triple_f[-1]:
                continue
            spans.append((off(tok.start), off(tok.end)))
    return spans


# ----------------------------------------------------------- HTML pass ----
def html_spans(src):
    spans, i = [], 0
    for m in re.finditer(r"<!--.*?-->", src, re.S):
        spans.append((i, m.start()))
        i = m.end()
    spans.append((i, len(src)))
    return spans



def hits(src, spans):
    out = []
    for s, e in spans:
        seg = src[s:e]
        if (DASH in seg or "\u2014" in seg) and                 seg.strip() not in (DASH, "\u2014"):
            out.append(f"{src.count(chr(10), 0, s) + 1}: {seg.strip()[:70]}")
    return out


def html_hits(src):
    out = []
    for s, e in html_spans(src):
        seg = re.sub(r">\s*" + DASH + r"\s*<", "><", src[s:e])
        if DASH in seg:
            out.append(f"{src.count(chr(10), 0, s) + 1}+: visible text")
    return out


FILES = [
    ("app/static/app.js", js_spans), ("app/main.py", py_spans),
    ("app/oneleft.py", py_spans), ("app/orders_sync.py", py_spans),
    ("app/planner.py", py_spans), ("app/shopify.py", py_spans),
    ("app/shipstation.py", py_spans), ("print_agent.py", py_spans),
    ("c72-app/src/com/telcan/rfidsweep/MainActivity.java", java_spans),
]
for rel, finder in FILES:
    p = os.path.join(ROOT, rel)
    if not os.path.exists(p):
        continue
    src = io.open(p, encoding="utf-8").read()
    h = hits(src, finder(src))
    check(f"no em dash in {rel} copy", not h, h[:5])
src = io.open(os.path.join(ROOT, "app/templates/index.html"),
              encoding="utf-8").read()
h = html_hits(src)
check("no em dash in index.html visible text", not h, h[:5])

print()
if fails:
    print(f"{len(fails)} FAILED"); sys.exit(1)
print("ALL PASS")
