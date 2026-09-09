"""Open-box products (Nick, 2026-09-09): SKUs ending in -O are the
open-box twins of a product. Printing a -O label migrates the twin's
barcode to barcode+-O IN SHOPIFY - written to the exact variant gid the
job carries, never re-resolved by code (a code lookup ranks the primary
twin first, which once rewrote the WRONG variant) - the label itself
prints the -O code, and the original barcode is linked to the open-box
listing so a scan of the physical box still surfaces both listings in
the Check step. Plus the pinned manual overwrite and the -O SKU-root.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ["SHOPIFY_WRITE_MODE"]="scan_station_only,openbox_barcode"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_openbox_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
import app.main as m
from app.main import app
from app import config
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

BASE_GID = "gid://shopify/ProductVariant/1"
OB_GID   = "gid://shopify/ProductVariant/2"
OB_PGID  = "gid://shopify/Product/2"

barcode_writes = []
def fake_update(pid, vid, bc):
    barcode_writes.append((pid, vid, bc))

BASE_PRODUCT = {
    "shopify_variant_id": BASE_GID,
    "shopify_product_id": "gid://shopify/Product/1",
    "product_title": "ZWO EAF Focuser", "variant_title": None,
    "sku": "ZWO-EAF", "barcode": "12345678", "bin_location": "B1-1",
}
OB_PRODUCT = {
    "shopify_variant_id": OB_GID, "shopify_product_id": OB_PGID,
    "product_title": "OPEN BOX - ZWO EAF Focuser", "variant_title": None,
    "sku": "ZWO-EAF-O", "barcode": "12345678", "bin_location": "B1-1",
}

with patch("app.shopify.lookup_barcode",
           side_effect=lambda c: BASE_PRODUCT if c == "12345678" else None), \
     patch("app.shopify.lookup_barcode_all",
           side_effect=lambda c: [BASE_PRODUCT, OB_PRODUCT]
           if c == "12345678" else []), \
     patch("app.shopify.lookup_sku", return_value=None, create=True), \
     patch("app.shopify.update_variant_barcode", side_effect=fake_update), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.main._maybe_refresh_bin_map", return_value=False):
  with TestClient(app) as cl:
    from sqlalchemy import select
    from sqlalchemy.orm import Session as S
    from app.database import get_engine
    from app.models import BarcodeAlias, BarcodeChange, BinMapEntry, PrintJob

    with S(get_engine()) as s:
        s.add(BinMapEntry(sku="ZWO-EAF", barcode="12345678",
                          product_title="ZWO EAF Focuser", bin="B1-1",
                          qty=3, shopify_variant_id=BASE_GID,
                          shopify_product_id="gid://shopify/Product/1"))
        s.add(BinMapEntry(sku="ZWO-EAF-O", barcode="12345678",
                          product_title="OPEN BOX - ZWO EAF Focuser",
                          bin="B1-1", qty=1, shopify_variant_id=OB_GID,
                          shopify_product_id=OB_PGID))
        s.add(BinMapEntry(sku="CLASH-1", barcode="55555555-O",
                          product_title="Innocent bystander", bin="C1-1",
                          qty=1,
                          shopify_variant_id="gid://shopify/ProductVariant/9"))
        s.commit()

    # ---- SKU root: -O twins share a root ------------------------------
    check("-O SKU shares the base root",
          m._sku_root("ZWO-EAF-O") == m._sku_root("ZWO-EAF"),
          (m._sku_root("ZWO-EAF-O"), m._sku_root("ZWO-EAF")))
    check("a scanned -O label barcode roots to the base code",
          m._sku_root("12345678-O") == "12345678", m._sku_root("12345678-O"))

    # ---- candidates BEFORE migration: shared barcode = both listings --
    r = cl.get("/api/products/candidates?barcode=12345678").json()
    check("shared barcode surfaces both listings, primary first",
          r["count"] == 2 and r["candidates"][0]["sku"] == "ZWO-EAF"
          and r["candidates"][1]["sku"] == "ZWO-EAF-O", r)

    # ---- printing the -O label migrates the barcode -------------------
    with S(get_engine()) as s:
        s.add(PrintJob(epc="0B0X000000000000000000A1", status="pending",
                       shopify_variant_id=OB_GID, shopify_product_id=OB_PGID,
                       product_title="OPEN BOX - ZWO EAF Focuser",
                       sku="ZWO-EAF-O", barcode="12345678",
                       bin_location="B1-1"))
        s.commit()
    r = cl.post("/api/print-jobs/claim?limit=5")
    claimed = r.json()["jobs"]
    check("claim answered", r.status_code == 200 and len(claimed) == 1,
          r.text[:200])
    check("Shopify write hit the OPEN-BOX gid, not the base twin",
          barcode_writes == [(OB_PGID, OB_GID, "12345678-O")],
          barcode_writes)
    check("the label itself prints the -O barcode",
          claimed and claimed[0]["barcode"] == "12345678-O", claimed)
    with S(get_engine()) as s:
        ob = s.scalar(select(BinMapEntry).where(
            BinMapEntry.sku == "ZWO-EAF-O"))
        base = s.scalar(select(BinMapEntry).where(
            BinMapEntry.sku == "ZWO-EAF"))
        check("bin map: open-box row migrated, base row untouched",
              ob.barcode == "12345678-O" and base.barcode == "12345678",
              (ob.barcode, base.barcode))
        al = s.scalars(select(BarcodeAlias)).all()
        check("original barcode linked to the open-box listing",
              len(al) == 1 and al[0].alias_barcode == "12345678"
              and al[0].sku == "ZWO-EAF-O" and al[0].kind == "openbox",
              [a.as_dict() if hasattr(a, "as_dict") else a.sku for a in al])
        ch = s.scalars(select(BarcodeChange).where(
            BarcodeChange.changed_by == "openbox-auto")).all()
        check("audited in History", len(ch) == 1
              and ch[0].old_barcode == "12345678"
              and ch[0].new_barcode == "12345678-O", len(ch))

    # ---- candidates AFTER migration: the alias keeps both listings ----
    r = cl.get("/api/products/candidates?barcode=12345678").json()
    check("physical barcode STILL surfaces both listings via the link",
          r["count"] == 2 and r["candidates"][0]["sku"] == "ZWO-EAF"
          and r["candidates"][1]["sku"] == "ZWO-EAF-O", r)

    # ---- idempotent: an older job snapshot re-migrates nothing --------
    with S(get_engine()) as s:
        s.add(PrintJob(epc="0B0X000000000000000000A2", status="pending",
                       shopify_variant_id=OB_GID, shopify_product_id=OB_PGID,
                       product_title="OPEN BOX - ZWO EAF Focuser",
                       sku="ZWO-EAF-O", barcode="12345678",
                       bin_location="B1-1"))
        s.commit()
    r = cl.post("/api/print-jobs/claim?limit=5")
    claimed = r.json()["jobs"]
    check("second claim: no second Shopify write", len(barcode_writes) == 1,
          barcode_writes)
    check("second label still prints the -O barcode",
          claimed and claimed[0]["barcode"] == "12345678-O", claimed)
    with S(get_engine()) as s:
        check("still exactly one alias",
              len(s.scalars(select(BarcodeAlias)).all()) == 1, "")

    # ---- collision: the -O code already belongs to someone ------------
    with S(get_engine()) as s:
        s.add(PrintJob(epc="0B0X000000000000000000A3", status="pending",
                       shopify_variant_id="gid://shopify/ProductVariant/7",
                       shopify_product_id="gid://shopify/Product/7",
                       product_title="OPEN BOX - Foo",
                       sku="FOO-BAR-O", barcode="55555555"))
        s.commit()
    r = cl.post("/api/print-jobs/claim?limit=5")
    claimed = r.json()["jobs"]
    check("colliding -O code refused: no write, label keeps old barcode",
          len(barcode_writes) == 1
          and claimed and claimed[0]["barcode"] == "55555555",
          (barcode_writes, claimed and claimed[0]["barcode"]))

    # ---- gate: feature off = nothing written, label prints as before --
    with patch.object(config, "SHOPIFY_WRITE_MODE", "scan_station_only"):
        with S(get_engine()) as s:
            s.add(PrintJob(epc="0B0X000000000000000000A4", status="pending",
                           shopify_variant_id="gid://shopify/ProductVariant/8",
                           shopify_product_id="gid://shopify/Product/8",
                           product_title="OPEN BOX - Bar",
                           sku="BAR-BAZ-O", barcode="77777777"))
            s.commit()
        r = cl.post("/api/print-jobs/claim?limit=5")
        claimed = r.json()["jobs"]
        check("write feature off: barcode untouched, printing unaffected",
              len(barcode_writes) == 1
              and claimed and claimed[0]["barcode"] == "77777777",
              (barcode_writes, claimed))

    # ---- manual overwrite pins to the variant on screen ---------------
    barcode_writes.clear()
    r = cl.post("/api/barcode-overwrites", json={
        "target": "12345678", "new_barcode": "12345678-OB",
        "confirmed": True, "changed_by": "Nick",
        "variant_gid": OB_GID})
    check("pinned overwrite writes the OPEN-BOX variant",
          r.status_code == 201
          and barcode_writes == [(OB_PGID, OB_GID, "12345678-OB")],
          (r.status_code, r.text[:200], barcode_writes))
    barcode_writes.clear()
    r = cl.post("/api/barcode-overwrites", json={
        "target": "12345678", "new_barcode": "0000000",
        "confirmed": True, "changed_by": "Nick",
        "variant_gid": "gid://shopify/ProductVariant/404"})
    check("a pin the code can't reach is refused, nothing written",
          r.status_code == 409 and barcode_writes == [], (r.status_code,
          barcode_writes))

print()
if fails:
    print(f"{len(fails)} FAILURE(S):"); [print("  -", f) for f in fails]
    sys.exit(1)
print("ALL PASS")
