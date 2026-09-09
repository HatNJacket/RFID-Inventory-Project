"""One-off repair (Nick, 2026-09-09): before TC-Planner's 2026-09-08 fix
its Print-labels payload carried the planner's INTERNAL order id where
the SO number belonged, so receiving batch labels, receipts, held
strips and review tasks read "SO 1266" for what is really SO 943.
History derives its receiving events from those rows, so it shows the
wrong numbers too.

This walks every "SO <n>" token in the affected columns and asks the
planner what <n> is:
  - an order whose reference_number == n  -> already correct, kept
  - else an order whose INTERNAL id == n  -> replaced with its real
    reference_number (vendor cross-checked against the label when the
    label names one - ids and references overlap numerically, so a
    bare number alone is ambiguous)
  - else                                  -> left alone, reported
Duplicate tokens created by the fix ("SO 943, SO 1266" both naming the
Svbony order) collapse to one.

Dry-run by default; --apply writes. DATABASE_URL comes from
%TEMP%\\dburl.txt, planner creds are pulled from the app settings into
env (never printed).
"""
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

with open(os.path.join(os.environ["TEMP"], "dburl.txt"),
          encoding="utf-8-sig") as f:
    DB_URL = f.read().strip().replace("mssql://", "mssql+pymssql://", 1)

raw = subprocess.run(
    ["az", "webapp", "config", "appsettings", "list", "-n", "telcan-rfid",
     "-g", "shopify-automation-rg", "-o", "json"],
    capture_output=True, text=True, shell=True, check=True,
).stdout
for row in json.loads(raw):
    if row["name"] in ("PLANNER_URL", "PLANNER_TOKEN"):
        os.environ[row["name"]] = row["value"]

from sqlalchemy import create_engine, text  # noqa: E402

from app import planner  # noqa: E402

APPLY = "--apply" in sys.argv
SO_TOKEN = re.compile(r"\bSO\s+(\d{2,6})\b")

_cache: dict[int, dict | None] = {}


def order_by_id(n: int) -> dict | None:
    if n not in _cache:
        try:
            _cache[n] = planner._get(f"/api/stock-orders/{n}")
        except Exception:
            _cache[n] = None
    return _cache[n]


_ref_cache: dict[int, bool] = {}


def is_reference(n: int, vendor_hint: str | None) -> bool:
    """Does an order exist whose reference_number == n (any status)?
    Vendor-checked when the label names one."""
    if n in _ref_cache and vendor_hint is None:
        return _ref_cache[n]
    try:
        found = planner._get("/api/stock-orders",
                             params={"search": str(n)})
        for o in found.get("orders") or []:
            if str(o.get("reference_number") or "") != str(n):
                continue
            if vendor_hint and (o.get("vendor") or "").strip().lower() \
                    != vendor_hint.strip().lower():
                continue
            _ref_cache[n] = True
            return True
    except Exception:
        pass
    if vendor_hint is None:
        _ref_cache[n] = False
    return False


def vendor_of(label: str) -> str | None:
    parts = [p.strip() for p in label.split("·")]
    return parts[-1] if len(parts) >= 2 and parts[-1] else None


unmapped: set[str] = set()


def fix_label(label: str | None) -> str | None:
    """Rewrite the SO tokens inside one label; None = no change."""
    if not label or "SO" not in label:
        return None
    vendor = vendor_of(label)

    def sub(m):
        n = int(m.group(1))
        if is_reference(n, vendor) or is_reference(n, None):
            return m.group(0)  # already a real SO number
        o = order_by_id(n)
        if o is not None:
            ov = (o.get("vendor") or "").strip().lower()
            if vendor and ov and ov != vendor.strip().lower():
                unmapped.add(f"{m.group(0)} (id vendor {ov!r} != label "
                             f"{vendor!r})")
                return m.group(0)
            ref = o.get("reference_number")
            if ref and str(ref) != str(n):
                return f"SO {ref}"
        unmapped.add(m.group(0))
        return m.group(0)

    fixed = SO_TOKEN.sub(sub, label)
    # Collapse duplicates the mapping created ("SO 943, SO 943" - the
    # same order named once by reference and once by internal id), and
    # normalize to the house " · " separators while at it.
    if fixed != label:
        segments = []
        for seg in fixed.split("·"):
            toks = [t.strip() for t in seg.split(",") if t.strip()]
            seen: set = set()
            out = []
            for t in toks:
                if t.upper() in seen:
                    continue
                seen.add(t.upper())
                out.append(t)
            segments.append(", ".join(out))
        fixed = " · ".join(segments)
    return fixed if fixed != label else None


def main() -> None:
    eng = create_engine(DB_URL)
    changes = []
    with eng.connect() as c:
        for r in c.execute(text(
            "SELECT id, created_by FROM rfid_batches "
            "WHERE kind = 'receiving' AND created_by LIKE '%SO %'"
        )):
            new = fix_label(r.created_by)
            if new:
                changes.append(("rfid_batches", "created_by", r.id,
                                r.created_by, new))
        # Receipts carry the AUTHORITATIVE planner id (stock_order_id) -
        # no token guessing: the reference simply becomes that order's
        # real SO number. (The dry run proved guessing here wrong: bare
        # "SO 946" has no vendor hint, and closed orders hide from the
        # planner's search, so the id-collision path won.)
        for r in c.execute(text(
            "SELECT id, stock_order_id, reference, vendor "
            "FROM rfid_order_receipts WHERE reference LIKE '%SO %'"
        )):
            o = order_by_id(int(r.stock_order_id))
            if o is None:
                unmapped.add(f"receipt#{r.id} (planner id "
                             f"{r.stock_order_id} not found)")
                continue
            ov = (o.get("vendor") or "").strip().lower()
            rv = (r.vendor or "").strip().lower()
            if rv and ov and ov != rv:
                unmapped.add(f"receipt#{r.id} (planner id "
                             f"{r.stock_order_id} vendor {ov!r} != "
                             f"receipt {rv!r})")
                continue
            ref = o.get("reference_number")
            if ref and f"SO {ref}" != (r.reference or "").strip():
                changes.append(("rfid_order_receipts", "reference", r.id,
                                r.reference, f"SO {ref}"))
        for r in c.execute(text(
            "SELECT id, reference FROM rfid_held_lists "
            "WHERE reference LIKE '%SO %'"
        )):
            new = fix_label(r.reference)
            if new:
                changes.append(("rfid_held_lists", "reference",
                                r.id, r.reference, new))
        for r in c.execute(text(
            "SELECT id, product_title FROM rfid_review_tasks "
            "WHERE status = 'open' AND product_title LIKE '%SO %'"
        )):
            new = fix_label(r.product_title)
            if new:
                changes.append(("rfid_review_tasks", "product_title",
                                r.id, r.product_title, new))
    for t_, col, rid, old, new in changes:
        print(f"{t_}.{col} #{rid}:\n    {old!r}\n -> {new!r}")
    if unmapped:
        print("\nLEFT ALONE (no planner mapping): "
              + ", ".join(sorted(unmapped)))
    if not changes:
        print("Nothing to repair.")
        return
    if not APPLY:
        print(f"\nDRY RUN - {len(changes)} row(s) would change. "
              "Re-run with --apply to write.")
        return
    with eng.begin() as c:
        for t_, col, rid, _old, new in changes:
            c.execute(
                text(f"UPDATE {t_} SET {col} = :v WHERE id = :i"),
                {"v": new, "i": rid},
            )
    print(f"\nAPPLIED - {len(changes)} row(s) updated.")


if __name__ == "__main__":
    main()
