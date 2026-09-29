"""No "(s)" plurals in user-facing copy (Nick, 2026-09-29): counts read
"1 tag" / "2 tags", with the verb agreeing where one follows. Checks
the server helper, then scans the web terminal, server messages and
the C72 source for any quoted "word(s)" that crept back in.
"""
import io, os, re, sys
ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
os.environ.setdefault("SHOPIFY_STORE", "t.myshopify.com")
os.environ.setdefault("SHOPIFY_CLIENT_ID", "x")
os.environ.setdefault("SHOPIFY_CLIENT_SECRET", "x")
fails = []
def check(l, c, x=""):
    print(("PASS  " if c else "FAIL  ") + l + ("" if c else f"  <- {x}"))
    if not c: fails.append(l)

from app.main import _count
check("one reads singular", _count(1, "tag", "tags") == "1 tag")
check("many reads plural", _count(3, "tag", "tags") == "3 tags")
check("zero reads plural", _count(0, "box", "boxes") == "0 boxes")
check("default plural adds s", _count(2, "label") == "2 labels")
check("verb pairs agree",
      _count(1, "bin is", "bins are") == "1 bin is"
      and _count(4, "bin is", "bins are") == "4 bins are")
check("a count arriving as text still picks singular",
      _count("1", "unit", "units") == "1 unit")
check("a non-number count falls back to plural without raising",
      _count("?", "unit", "units") == "? units")

# The scan: a word(s)/(es)/(ies) inside a quoted string, outside
# comments. The helpers' own docstrings quote the banned form on
# purpose ('never "tag(s)"') and are allowed.
# Case-blind: "LABEL(S)" on a C72 banner slipped the first sweep.
QUOTED = re.compile(r"""["'`][^"'`\n]*(?<![.\w])[a-z]+\((s|es|ies)\)""",
                    re.IGNORECASE)
ALLOW = re.compile(r'never "?[a-z]+\((s|es|ies)\)')
FILES = [
    ("app/static/app.js", ("//", "*", "/*")),
    ("app/templates/index.html", ("<!--",)),
    ("app/main.py", ("#",)),
    ("app/oneleft.py", ("#",)),
    ("print_agent.py", ("#",)),
    ("c72-app/src/com/telcan/rfidsweep/MainActivity.java",
     ("//", "*", "/*")),
]
for rel, comment in FILES:
    hits = []
    in_html_comment = False
    with io.open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            s = line.strip()
            if rel.endswith(".html"):
                if "<!--" in s:
                    in_html_comment = True
                if in_html_comment:
                    if "-->" in s:
                        in_html_comment = False
                    continue
            if s.startswith(comment) or ALLOW.search(line):
                continue
            if QUOTED.search(line):
                hits.append(f"{n}: {s[:90]}")
    check(f"no (s) copy in {rel}", not hits, hits[:5])

print()
if fails:
    print(f"{len(fails)} FAILED"); sys.exit(1)
print("ALL PASS")
