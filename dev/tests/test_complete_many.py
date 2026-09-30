"""POST /api/print-jobs/complete-many (agent v9, 2026-09-30): every label
one odometer poll passed confirms in one call, through the same per-job
logic; a repeated report counts as done, unknown ids are skipped.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_complete_many_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
import app.main as M
from app.main import app
from app.database import get_engine
from app.models import PrintJob, RfidAssignment
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]):
  M._maybe_refresh_bin_map = lambda *a, **k: False
  with TestClient(app) as cl:
    r = cl.post("/api/print-jobs", json={
        "quantity": 4, "shopify_variant_id": "t:1", "product_title": "T",
        "sku": "SKU-1"})
    ids = [j["id"] for j in r.json()["jobs"]]
    cl.post("/api/print-jobs/claim", params={"limit": 10})

    r = cl.post("/api/print-jobs/complete-many",
                json={"ids": ids[:3], "create_assignment": False})
    check("three labels confirm in one call",
          r.status_code == 200 and r.json()["done"] == ids[:3], r.text)
    with Session(get_engine()) as s:
        st = {j.id: j.status for j in s.scalars(select(PrintJob))}
        tags = s.scalars(select(RfidAssignment)).all()
    check("  ...each is done, the fourth still printing",
          [st[i] for i in ids] == ["done", "done", "done", "printing"], st)
    check("  ...barcode-only printers create no tag records", tags == [])

    r = cl.post("/api/print-jobs/complete-many",
                json={"ids": [ids[2], ids[3], 99999], "create_assignment": True})
    d = r.json()
    check("a repeated report counts as done, unknown ids are skipped",
          d["done"] == [ids[2], ids[3]] and d["skipped"] == [99999], d)
    with Session(get_engine()) as s:
        tags = s.scalars(select(RfidAssignment)).all()
    check("RFID printers still get the tag record per new label",
          len(tags) == 1, len(tags))

    r = cl.post("/api/print-jobs/complete-many", json={"ids": []})
    check("an empty list is refused", r.status_code == 422)

print()
if fails:
    print(f"{len(fails)} FAILED"); sys.exit(1)
print("ALL PASS")
