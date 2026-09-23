"""Dev-mirror snapshot (Nick, 2026-09-23): prod exports every table,
dev imports them - so the dev site's sqlite DUPLICATES production.
Export is an ordinary station read; import is double-guarded
(ALLOW_SNAPSHOT_IMPORT=1 env AND a sqlite engine) so prod can never be
wiped by it."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
os.environ.pop("ALLOW_SNAPSHOT_IMPORT", None)
db = os.path.join(tempfile.gettempdir(), "rfid_snapshot_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_engine
from app.models import BinMapEntry, RfidAssignment
from sqlalchemy.orm import Session
from sqlalchemy import select
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

with patch("app.main._maybe_refresh_bin_map", return_value=False):
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        s.add(BinMapEntry(sku="SNAP-1", barcode="801",
                          product_title="Snapshot Demo", bin="A1-1",
                          qty=3, shopify_variant_id="t:S1"))
        s.add(RfidAssignment(rfid_id="5A000000000000000000000A",
                             shopify_variant_id="t:S1", sku="SNAP-1",
                             product_title="Snapshot Demo",
                             bin_location="A1-1"))
        s.commit()

    r = cl.get("/api/admin/snapshot/tables")
    tables = r.json().get("tables", [])
    check("table listing includes the inventory tables",
          "rfid_bin_map" in tables and "rfid_assignments" in tables
          and len(tables) > 40, len(tables))

    r = cl.get("/api/admin/snapshot/export?table=rfid_assignments")
    exp = r.json()
    check("export returns the rows with JSON-safe values",
          r.status_code == 200 and exp["count"] == 1
          and exp["rows"][0]["rfid_id"] == "5A000000000000000000000A"
          and isinstance(exp["rows"][0].get("assigned_at"), (str, type(None))),
          r.text[:250])
    r = cl.get("/api/admin/snapshot/export?table=dbo_evil")
    check("unknown tables 404", r.status_code == 404, r.status_code)

    # Import guard 1: not enabled -> refused, data untouched.
    r = cl.post("/api/admin/snapshot/import",
                json={"table": "rfid_assignments", "rows": []})
    check("import refused without ALLOW_SNAPSHOT_IMPORT",
          r.status_code == 403 and "not enabled" in r.text, r.text[:150])
    with Session(get_engine()) as s:
        n = len(s.scalars(select(RfidAssignment)).all())
        check("refused import wiped nothing", n == 1, n)

    # Enabled (the dev site's setting): wipe + replace, datetimes parse.
    os.environ["ALLOW_SNAPSHOT_IMPORT"] = "1"
    try:
        rows = [
            {"id": 41, "rfid_id": "5B000000000000000000000B",
             "shopify_variant_id": "t:S1", "sku": "SNAP-1",
             "product_title": "Snapshot Demo", "bin_location": "B2-2",
             "assigned_at": "2026-09-20T10:00:00+00:00"},
            {"id": 42, "rfid_id": "5C000000000000000000000C",
             "shopify_variant_id": "t:S1", "sku": "SNAP-1",
             "product_title": "Snapshot Demo", "bin_location": "B2-2",
             "assigned_at": "2026-09-21T10:00:00+00:00"},
        ]
        r = cl.post("/api/admin/snapshot/import",
                    json={"table": "rfid_assignments", "rows": rows})
        check("enabled import replaces the table",
              r.status_code == 200 and r.json()["imported"] == 2,
              r.text[:200])
        with Session(get_engine()) as s:
            tags = s.scalars(select(RfidAssignment)).all()
            check("old rows gone, prod rows in, ids preserved",
                  sorted(t.id for t in tags) == [41, 42]
                  and all(t.bin_location == "B2-2" for t in tags),
                  [(t.id, t.bin_location) for t in tags])
            t41 = next(t for t in tags if t.id == 41)
            check("ISO datetimes round-trip",
                  t41.assigned_at is not None
                  and t41.assigned_at.year == 2026, t41.assigned_at)
        # Round trip: export what we imported.
        exp = cl.get(
            "/api/admin/snapshot/export?table=rfid_assignments").json()
        check("export sees the imported rows", exp["count"] == 2, exp)
    finally:
        os.environ.pop("ALLOW_SNAPSHOT_IMPORT", None)

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
