"""Printing a bundle MASTER prints its components (2026-09-29, S11800 =
S11800-1 + S11800-2): N bundles queue N x per-kit qty labels of each
component, each with its own product details and saved label lines;
nothing queues when a component can't be found.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_bundle_print_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
import app.main as M
from app.main import app
from app.database import get_engine
from app.models import BinMapEntry, BundleContent, LabelName, PrintJob
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

def jobs_by_sku():
    with Session(get_engine()) as s:
        return dict(s.execute(select(PrintJob.sku, func.count())
                              .group_by(PrintJob.sku)).all())

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}):
  M._maybe_refresh_bin_map = lambda *a, **k: False
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        s.add(BinMapEntry(sku="S11800-1", product_title="Scope tube",
                          bin="B3-1", qty=4, shopify_variant_id="t:S1"))
        s.add(BinMapEntry(sku="S11800-2", product_title="Scope mount",
                          bin="B3-2", qty=4, shopify_variant_id="t:S2"))
        s.add(BundleContent(bundle_sku="S11800", component_sku="S11800-1", qty=1))
        s.add(BundleContent(bundle_sku="S11800", component_sku="S11800-2", qty=2))
        s.add(LabelName(sku="S11800-2", label_name="MOUNT", placement="header"))
        s.add(BundleContent(bundle_sku="KIT-X", component_sku="GHOST-9", qty=1))
        s.commit()

    body = {"quantity": 5, "shopify_variant_id": "t:MASTER",
            "product_title": "S11800 kit", "sku": "s11800",
            "label_name": "MASTER NAME", "requested_by": "Nick",
            "print_session": "abc"}
    r = cl.post("/api/print-jobs", json=body)
    d = r.json()
    check("printing a bundle master answers", r.status_code == 201, r.text)
    check("it queues 5 x each component's per-kit qty (5 + 10)",
          d["count"] == 15 and jobs_by_sku() == {"S11800-1": 5, "S11800-2": 10},
          (d.get("count"), jobs_by_sku()))
    check("no label for the master itself", "s11800" not in jobs_by_sku()
          and "S11800" not in jobs_by_sku())
    with Session(get_engine()) as s:
        j1 = s.scalars(select(PrintJob).where(PrintJob.sku == "S11800-1")).first()
        j2 = s.scalars(select(PrintJob).where(PrintJob.sku == "S11800-2")).first()
    check("components print with their own product and bin",
          j1.shopify_variant_id == "t:S1" and j1.bin_location == "B3-1"
          and j1.product_title == "Scope tube", (j1.shopify_variant_id, j1.bin_location))
    check("  ...their own saved label lines, never the master's name",
          j2.label_name == "MOUNT" and j1.label_name in (None, ""), (j1.label_name, j2.label_name))
    check("  ...and the caller's requester + print session",
          j1.requested_by == "Nick" and j1.print_session == "abc")
    check("the answer names the split",
          d["bundle"]["components"] == [
              {"sku": "S11800-1", "qty_per_kit": 1, "labels": 5},
              {"sku": "S11800-2", "qty_per_kit": 2, "labels": 10}]
          and "5 x S11800-1" in d["message"], d.get("bundle"))

    before = sum(jobs_by_sku().values())
    r = cl.post("/api/print-jobs", json={**body, "sku": "KIT-X"})
    check("a missing component refuses the whole print",
          r.status_code == 422 and "GHOST-9" in r.json()["detail"], r.text)
    check("  ...and queues nothing", sum(jobs_by_sku().values()) == before)

    M._BUNDLE_PRINT_MAX = 20
    before = sum(jobs_by_sku().values())
    r = cl.post("/api/print-jobs", json={**body, "quantity": 7,
                                          "sku": "S11800"})
    check("a bundle print past the label cap is refused (21 > 20)",
          r.status_code == 422 and sum(jobs_by_sku().values()) == before,
          r.status_code)
    M._BUNDLE_PRINT_MAX = 500

    r = cl.post("/api/print-jobs", json={
        "quantity": 2, "shopify_variant_id": "t:S1",
        "product_title": "Scope tube", "sku": "S11800-1"})
    check("a plain product prints exactly as before",
          r.status_code == 201 and r.json()["count"] == 2
          and "bundle" not in r.json(), r.text[:200])

print()
if fails:
    print(f"{len(fails)} FAILED"); sys.exit(1)
print("ALL PASS")
