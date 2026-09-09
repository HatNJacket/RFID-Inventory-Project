"""Multi-box SETS on the collect screens (Nick, 2026-09-09): the batch
payload lumps a set's boxes into one visual group - the set is never a
scannable row (its boxes ARE the parts), parts inherit the set's
expected unit count, and a box shelved in a DIFFERENT bin still shows
under the set with the bin it belongs in and its known tag count.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_boxset_collect_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

STOCK = {
    "S20000": {"on_hand": 3, "bin": "F1-1"},
    "S30000": {"on_hand": 4, "bin": "H1-1"},
}
def fake_stock_info(skus):
    return {s: {"on_hand": STOCK[s]["on_hand"], "unavailable": 0,
                "bin": STOCK[s]["bin"]}
            for s in skus if s in STOCK}

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_on_hand", return_value=None), \
     patch("app.shopify.get_stock_info_by_skus",
           side_effect=fake_stock_info), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.main._maybe_refresh_bin_map", return_value=False), \
     patch("app.main._kick_orders_sync_soon", create=True):
  with TestClient(app) as cl:
    from sqlalchemy import select
    from sqlalchemy.orm import Session as S
    from app.database import get_engine
    from app.models import (Batch, BatchItem, BinMapEntry, BoxSetPart,
                            RfidAssignment)

    with S(get_engine()) as s:
        # Set S20000 lives in F1-1 with box 1; box 2 lives in F2-9.
        s.add(BinMapEntry(sku="S20000", barcode="7420000",
                          product_title="Big Scope Set", bin="F1-1",
                          qty=3, unavailable=0,
                          shopify_variant_id="gid://v/20000",
                          image_url="http://img/set"))
        s.add(BinMapEntry(sku="S20000-1", barcode="7420001",
                          product_title="Big Scope Set Box 1",
                          bin="F1-1", qty=0,
                          shopify_variant_id="gid://v/200001"))
        s.add(BinMapEntry(sku="S20000-2", barcode="7420002",
                          product_title="Big Scope Set Box 2",
                          bin="F2-9", qty=0,
                          shopify_variant_id="gid://v/200002"))
        s.add(BoxSetPart(set_sku="S20000", set_title="Big Scope Set",
                         set_variant_id="gid://v/20000",
                         part_sku="S20000-1", part_barcode="7420001",
                         box_no=1))
        s.add(BoxSetPart(set_sku="S20000", set_title="Big Scope Set",
                         set_variant_id="gid://v/20000",
                         part_sku="S20000-2", part_barcode="7420002",
                         box_no=2))
        # Box 2's known stock: two tags already in the system.
        s.add(RfidAssignment(rfid_id="C00000000000000000000001",
                             shopify_variant_id="gid://v/200002",
                             product_title="Big Scope Set Box 2",
                             sku="S20000-2", bin_location="F2-9"))
        s.add(RfidAssignment(rfid_id="C00000000000000000000002",
                             shopify_variant_id="gid://v/200002",
                             product_title="Big Scope Set Box 2",
                             sku="S20000-2", bin_location="F2-9"))
        # Set S30000 lives in H1-1; only its box 1 is in G5-5.
        s.add(BinMapEntry(sku="S30000", barcode="7430000",
                          product_title="Mount Set", bin="H1-1",
                          qty=4, shopify_variant_id="gid://v/30000"))
        s.add(BinMapEntry(sku="S30000-1", barcode="7430001",
                          product_title="Mount Set Box 1",
                          bin="G5-5", qty=0,
                          shopify_variant_id="gid://v/300001"))
        s.add(BoxSetPart(set_sku="S30000", set_title="Mount Set",
                         part_sku="S30000-1", part_barcode="7430001",
                         box_no=1))
        s.add(BoxSetPart(set_sku="S30000", set_title="Mount Set",
                         part_sku="S30000-2", part_barcode="7430002",
                         box_no=2))
        s.commit()

    # ---- batch in the SET's own bin -----------------------------------
    r = cl.post("/api/batches", json={"bin": "F1-1", "created_by": "t"})
    d = r.json()
    check("batch created", r.status_code in (200, 201), r.text[:300])
    skus = [i.get("sku") for i in d["items"]]
    check("the set itself is NOT a scannable row", "S20000" not in skus,
          skus)
    part1 = next((i for i in d["items"] if i.get("sku") == "S20000-1"),
                 None)
    check("box 1 seeded in its bin", part1 is not None, skus)
    check("box 1 inherits the SET's expected units (3, not its own 0)",
          part1 and part1["expected_qty"] == 3, part1)
    check("box 1 stamped with its set",
          part1 and part1.get("boxset_of") == "S20000"
          and part1.get("boxset_box_no") == 1, part1)
    sets = d.get("box_sets") or []
    check("create answer carries ONE set group", len(sets) == 1, sets)
    g = sets[0] if sets else {}
    check("group identity + expected units",
          g.get("set_sku") == "S20000"
          and g.get("set_title") == "Big Scope Set"
          and g.get("expected_units") == 3 and g.get("boxes") == 2, g)
    parts = {p["box_no"]: p for p in g.get("parts", [])}
    check("box 1 marked in-bin with a live item",
          parts.get(1, {}).get("in_bin") is True
          and parts.get(1, {}).get("item_id") is not None, parts)
    check("box 2 marked in ANOTHER bin (F2-9) with known count 2",
          parts.get(2, {}).get("in_bin") is False
          and parts.get(2, {}).get("bin") == "F2-9"
          and parts.get(2, {}).get("known_units") == 2, parts)
    bid = d["id"]

    rg = cl.get(f"/api/batches/{bid}")
    gd = rg.json()
    check("GET carries box_sets on the batch object",
          len(gd["batch"].get("box_sets") or []) == 1, gd["batch"].keys())
    gp1 = next((i for i in gd["items"] if i.get("sku") == "S20000-1"), {})
    check("GET items keep the part stamp",
          gp1.get("boxset_of") == "S20000", gp1)

    # ---- batch in a bin holding ONE box of a set shelved elsewhere ----
    r2 = cl.post("/api/batches", json={"bin": "G5-5", "created_by": "t"})
    d2 = r2.json()
    p31 = next((i for i in d2["items"] if i.get("sku") == "S30000-1"),
               None)
    check("stray box seeded in its own bin", p31 is not None,
          [i.get("sku") for i in d2["items"]])
    check("its expected units come from the SET's live count (4)",
          p31 and p31["expected_qty"] == 4, p31)
    sets2 = d2.get("box_sets") or []
    g2 = sets2[0] if sets2 else {}
    check("group shows the set's home bin",
          g2.get("set_sku") == "S30000" and g2.get("bin") == "H1-1", g2)
    parts2 = {p["box_no"]: p for p in g2.get("parts", [])}
    check("box 2 (no listing anywhere) still listed, not in this bin",
          parts2.get(2, {}).get("in_bin") is False
          and parts2.get(2, {}).get("known_units") == 0, parts2)

    # ---- legacy batch that seeded the SET itself as a row -------------
    with S(get_engine()) as s:
        b = Batch(bin_name="F1-1", created_by="t")
        s.add(b); s.flush()
        s.add(BatchItem(batch_id=b.id, scanned_code="7420000",
                        resolved=True, sku="S20000", barcode="7420000",
                        product_title="Big Scope Set", qty_scanned=0,
                        expected_qty=3))
        s.commit(); legacy_id = b.id
    rl = cl.get(f"/api/batches/{legacy_id}")
    ld = rl.json()
    srow = next((i for i in ld["items"] if i.get("sku") == "S20000"), {})
    check("legacy SET row flagged for header folding",
          srow.get("boxset_set") is True, srow)
    check("legacy batch still gets the group",
          len(ld["batch"].get("box_sets") or []) == 1, ld["batch"].keys())

print()
if fails:
    print(f"{len(fails)} FAILURE(S):"); [print("  -", f) for f in fails]
    sys.exit(1)
print("ALL PASS")
