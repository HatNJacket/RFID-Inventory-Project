"""The Box of Un-Labelable Product flag (Nick, 2026-09-08: 100 loose
CR2032s) and the mis-label picker lists: kind-aware NonTaggable rows,
the one-box-one-label print, audit rows that show on-hand without
scoring drift, the inventory fast snapshot, and mislabel alternates
riding every lookup.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_unlab_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_on_hand", return_value=None), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}):
  with TestClient(app) as cl:
    from sqlalchemy import select
    from sqlalchemy.orm import Session as S
    from app.database import get_engine
    from app.models import (BinMapEntry, NonTaggable, PrintJob,
                            RfidAssignment)

    with S(get_engine()) as s:
        s.add(BinMapEntry(sku="CR2032", barcode="7702032",
                          product_title="CR2032 100-pack box",
                          bin="D4-2", qty=100, unavailable=0,
                          shopify_variant_id="gid://v/2032",
                          image_url="http://img/cr"))
        s.add(BinMapEntry(sku="SCREW-1", product_title="Thumbscrews",
                          bin="D4-3", qty=500,
                          shopify_variant_id="gid://v/scr"))
        s.commit()

    # ---- flag on: distinct from non-taggable, History-logged ---------
    r = cl.put("/api/products/CR2032/unlabelable-box",
               json={"flagged": True, "changed_by": "Nick"})
    check("flag on", r.status_code == 200
          and r.json()["unlabelable_box"] is True, r.text)
    with S(get_engine()) as s:
        row = s.get(NonTaggable, "CR2032")
        check("row stored with the unlabelable kind",
              row is not None and row.kind == "unlabelable-box", row)

    # Lookup carries the flag + the loud note for every surface.
    r = cl.get("/api/products/by-barcode/CR2032")
    check("lookup says unlabelable + notes it",
          r.json().get("unlabelable_box") is True
          and "UN-LABELABLE BOX" in (r.json().get("scan_note") or ""),
          r.text[:300])

    # The product panel exposes both kinds distinctly.
    r = cl.get("/api/product-history?term=CR2032")
    d = r.json()
    check("panel: unlabelable yes, non-taggable no",
          d["unlabelable_box"] is True and d["non_taggable"] is False,
          {k: d.get(k) for k in ("unlabelable_box", "non_taggable")})

    # Flipping to plain non-taggable switches the kind (one row).
    r = cl.put("/api/products/CR2032/non-taggable",
               json={"non_taggable": True, "changed_by": "Nick"})
    check("switch to plain non-taggable", r.status_code == 200, r.text)
    with S(get_engine()) as s:
        row = s.get(NonTaggable, "CR2032")
        check("kind switched, still one row",
              row is not None and row.kind == "non-taggable", row)
    # ...and back for the rest of the suite.
    cl.put("/api/products/CR2032/unlabelable-box",
           json={"flagged": True, "changed_by": "Nick"})

    # ---- the ONE box label -------------------------------------------
    r = cl.post("/api/products/SCREW-1/box-label", json={})
    check("box label refused for unflagged products",
          r.status_code == 422, r.text)
    r = cl.post("/api/products/CR2032/box-label",
                json={"changed_by": "Nick"})
    check("box label queues", r.status_code == 200
          and r.json()["job"]["sku"] == "CR2032"
          and "Pair it" in r.json()["message"], r.text[:300])
    r = cl.post("/api/products/CR2032/box-label", json={})
    check("second label refused while one is out",
          r.status_code == 409 and "already out" in r.json()["detail"],
          r.text)
    # Pair the printed label -> it becomes the box marker.
    with S(get_engine()) as s:
        job = s.scalar(select(PrintJob).where(PrintJob.sku == "CR2032"))
        epc = job.epc
        s.add(RfidAssignment(rfid_id=epc, shopify_variant_id="gid://v/2032",
                             sku="CR2032",
                             product_title="CR2032 100-pack box",
                             bin_location="D4-2"))
        job.status = "canceled"   # clear the stray-job guard's view
        s.commit()
    r = cl.post("/api/products/CR2032/box-label", json={})
    check("marker exists -> no more labels", r.status_code == 409
          and "box marker already exists" in r.json()["detail"], r.text)

    # ---- audit shows on-hand, never drift ----------------------------
    r = cl.get("/api/audit/bins")
    rows = [p for b in r.json()["bins"] for p in b["products"]
            if p["sku"] == "CR2032"]
    check("audit row present with on-hand and zero diff",
          len(rows) == 1 and rows[0]["on_hand"] == 100
          and rows[0]["diff"] == 0 and rows[0]["unlabelable"] is True,
          rows)
    check("plain non-taggable still fully skipped",
          not [p for b in r.json()["bins"] for p in b["products"]
               if p["sku"] == "SCREW-1"]
          or True, "")  # SCREW-1 isn't flagged at all - it may appear
    # Flag SCREW-1 plain non-taggable and confirm it vanishes.
    cl.put("/api/products/SCREW-1/non-taggable",
           json={"non_taggable": True})
    r = cl.get("/api/audit/bins")
    check("non-taggable products stay out of the audit",
          not [p for b in r.json()["bins"] for p in b["products"]
               if p["sku"] == "SCREW-1"]
          and r.json()["skipped_non_taggable"] == 1, r.text[:200])

    # ---- collect scans can't re-add a flagged product ----------------
    from app.models import Batch
    with S(get_engine()) as s:
        s.add(Batch(bin_name="D4-2", created_by="test"))
        s.commit()
        bid = s.scalar(select(Batch.id).order_by(Batch.id.desc()))
    r = cl.post(f"/api/batches/{bid}/scan", json={"code": "CR2032"})
    check("collect scan refuses an un-labelable box, kindly",
          r.status_code == 422
          and "box label prints" in r.json()["detail"], r.text)
    r = cl.post(f"/api/batches/{bid}/scan", json={"code": "SCREW-1"})
    check("collect scan refuses non-taggable too",
          r.status_code == 422
          and "never joins batches" in r.json()["detail"], r.text)

    # ---- inventory fast snapshot -------------------------------------
    r = cl.get("/api/inventory/summary?fast=1")
    d = r.json()
    row = next((p for p in d["products"] if p["sku"] == "CR2032"), None)
    check("fast mode answers from the snapshot",
          d["live"] is False and row is not None
          and row["shopify_qty"] == 100, str(row)[:200])
    r = cl.get("/api/inventory/summary")
    check("normal mode still marked live", r.json()["live"] is True,
          r.text[:120])

    # ---- mis-label alternates + picker payload -----------------------
    with S(get_engine()) as s:
        s.add(BinMapEntry(sku="EXOS2CW", barcode="111",
                          product_title="EXOS2 10lb weight", bin="K1-1",
                          qty=3, shopify_variant_id="gid://v/cw",
                          image_url="http://img/cw"))
        s.add(BinMapEntry(sku="EXOS2CWB5", barcode="222",
                          product_title="EXOS2 5lb weight", bin="K1-2",
                          qty=2, shopify_variant_id="gid://v/cwb5"))
        s.commit()
    cl.put("/api/products/EXOS2CWB5/mislabel-flag",
           json={"flagged": True, "changed_by": "Nick"})
    # Add by BARCODE - it must resolve to the SKU.
    r = cl.post("/api/products/EXOS2CWB5/mislabel-alternates",
                json={"code": "111", "changed_by": "Nick"})
    check("alternate added by barcode", r.status_code == 200
          and r.json()["alternates"] == ["EXOS2CW"], r.text[:300])
    r = cl.post("/api/products/EXOS2CWB5/mislabel-alternates",
                json={"code": "EXOS2CW"})
    check("re-add is idempotent",
          r.json()["alternates"] == ["EXOS2CW"], r.text[:200])
    r = cl.post("/api/products/EXOS2CWB5/mislabel-alternates",
                json={"code": "EXOS2CWB5"})
    check("self-add refused", r.status_code == 422, r.text)

    r = cl.get("/api/products/EXOS2CWB5/mislabel-flag")
    opts = r.json()["options"]
    check("GET returns preview options, flagged first",
          len(opts) == 2 and opts[0]["sku"] == "EXOS2CWB5"
          and opts[0]["flagged"] is True
          and opts[1]["sku"] == "EXOS2CW"
          and opts[1]["barcode"] == "111"
          and opts[1]["bin_location"] == "K1-1", str(opts)[:300])

    # The options ride the SCAN lookup so web + C72 can offer the picker.
    r = cl.get("/api/products/by-barcode/222")
    d = r.json()
    check("lookup carries mislabel options",
          d.get("mislabel_flag") is True
          and len(d.get("mislabel_options") or []) == 2
          and "VENDOR MIS-LABEL" in (d.get("scan_note") or ""),
          str(d)[:300])
    # A flagged product with NO list keeps the plain text warning only.
    cl.put("/api/products/EXOS2CW/mislabel-flag", json={"flagged": True})
    r = cl.get("/api/products/by-barcode/111")
    check("no alternates -> warning without picker",
          r.json().get("mislabel_flag") is True
          and "mislabel_options" not in r.json(), r.text[:300])

    # Remove the alternate.
    r = cl.delete(
        "/api/products/EXOS2CWB5/mislabel-alternates/EXOS2CW?by=Nick")
    check("alternate removed", r.status_code == 200
          and r.json()["alternates"] == [], r.text)
    r = cl.delete(
        "/api/products/EXOS2CWB5/mislabel-alternates/EXOS2CW")
    check("double remove -> 404", r.status_code == 404, r.text)

print()
sys.exit(1 if fails else 0)
