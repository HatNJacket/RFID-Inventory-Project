"""Build the Azure zip-deploy package.

Written after PowerShell's Compress-Archive shipped a broken package: on
Windows PowerShell 5.1 it writes nested entries with BACKSLASH separators,
so Linux reads "app\\main.py" as one oddly-named file at the zip root, the
app/ package never exists, and gunicorn dies with ModuleNotFoundError.
That has downed prod twice.

zipfile with explicit forward-slash arcnames is the fix. The asserts at the
bottom make a repeat of that failure impossible to miss.

Usage:
    py dev/mkdeploy.py
    az webapp deploy -n telcan-rfid -g shopify-automation-rg --type zip \
        --src-path dev/deploy.zip
"""
import os
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "deploy.zip")

# Exactly the layout of the last package that booted. dev/ is NOT deployed.
FILES = [
    ".env.example",
    ".github/workflows/azure-deploy.yml",
    ".gitignore",
    "README.md",
    "ROADMAP.md",
    "app/static/app.js",
    "app/static/styles.css",
    "app/static/tc-rfid-sweep.apk",
    "app/static/tc-rfid-sweep.apk.idsig",
    "app/templates/index.html",
    "inspect_db.py",
    "load_astronomik.py",
    "print_agent.py",
    "requirements.txt",
    "startup.txt",
    "test_shopify.py",
]

# Every app/*.py ships, discovered rather than listed: the hand-kept list
# silently dropped app/oneleft.py (2026-08-18) and prod crash-looped on
# the ImportError until a rebuilt zip landed. A new module can't be
# forgotten again.
FILES += sorted(
    f"app/{name}" for name in os.listdir(os.path.join(ROOT, "app"))
    if name.endswith(".py")
)

# The help-slideshow images (app/static/help/) ship the same way — a new
# slide dropped in later must never be hand-listed.
_help_dir = os.path.join(ROOT, "app", "static", "help")
if os.path.isdir(_help_dir):
    FILES += sorted(
        f"app/static/help/{name}" for name in os.listdir(_help_dir)
        if name.endswith(".svg")
    )

missing = [f for f in FILES if not os.path.isfile(os.path.join(ROOT, f))]
if missing:
    raise SystemExit(f"missing source files: {missing}")

if os.path.exists(OUT):
    os.remove(OUT)

with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for rel in FILES:
        # arcname is the POSIX path, never the OS one.
        z.write(os.path.join(ROOT, rel), arcname=rel)

with zipfile.ZipFile(OUT) as z:
    names = z.namelist()
    bad = [n for n in names if "\\" in n]
    assert not bad, f"BACKSLASH ENTRIES (would break the deploy): {bad}"
    assert "app/main.py" in names, "app/main.py missing -> cannot import app"
    assert "app/__init__.py" in names, "app/__init__.py missing -> no package"
    # Belt and braces: every module app/main.py imports from app must be
    # in the package, or gunicorn dies on ImportError at boot.
    with open(os.path.join(ROOT, "app", "main.py"), encoding="utf-8") as fh:
        main_src = fh.read()
    import re
    imported = re.findall(r"^from app import (.+)$", main_src, re.M)
    for group in imported:
        for mod in group.split(","):
            mod = mod.strip()
            if mod and f"app/{mod}.py" not in names:
                raise SystemExit(
                    f"app/{mod}.py is imported by app/main.py but missing "
                    f"from the package"
                )

print(f"OK  {OUT}")
print(f"    {len(names)} entries, all POSIX paths, app/ package intact")
print(f"    size {os.path.getsize(OUT):,} bytes")
