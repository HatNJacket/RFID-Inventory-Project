"""Local UI-verification server: sqlite seeded with a batch awaiting
verify — one normal product, one flagged 'won't RFID scan' — plus a C72
sweep capture that heard ONLY the normal product's tags."""
import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                  "uiverify.db")
if os.path.exists(DB):
    os.remove(DB)
os.environ["DATABASE_URL"] = "sqlite:///" + DB.replace("\\", "/")
os.environ["SHOPIFY_STORE"] = "t.myshopify.com"
os.environ["SHOPIFY_CLIENT_ID"] = "x"
os.environ["SHOPIFY_CLIENT_SECRET"] = "x"
os.environ["SHOPIFY_WRITE_MODE"] = (
    "scan_station_only,verify_onhand,verify_onhand_lower")
os.environ["ONELEFT_MODE"] = "confirm"  # the bridge itself is faked below
os.environ.pop("STATION_KEY", None)
os.environ.pop("PRINT_AGENT_KEY", None)

from app.main import app  # noqa: E402  (env must be set first)
from app.database import get_engine  # noqa: E402
from app.models import (  # noqa: E402
    Base, Batch, BatchItem, BinMapEntry, EpcCapture, PrintJob,
    ReviewTask, RfidAssignment, RfidIncompatible,
)
from sqlalchemy.orm import Session  # noqa: E402

# Tables normally appear in the app's startup hook; seeding runs first.
Base.metadata.create_all(get_engine())

with Session(get_engine()) as s:
    # A DONE prior batch on T1-1 makes the open batch a RE-TAG bin, so
    # the shelf-reconcile payload (unheard tags, retire buttons, the
    # manual-retire button in verify's expanded rows) renders locally.
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    s.add(Batch(bin_name="T1-1", status="done", created_by="Steve",
                completed_at=_dt.now(_tz.utc) - _td(days=30)))
    b = Batch(bin_name="T1-1", status="awaiting-verify", ui_step="verify",
              created_by="Steve")
    s.add(b)
    s.flush()
    normal = BatchItem(batch_id=b.id, scanned_code="111", resolved=True,
                       sku="NORMAL-1", barcode="111",
                       product_title="Baader UHC Filter 2in",
                       shopify_variant_id="t:1", qty_scanned=2,
                       paired_count=2, bin_location="T1-1",
                       expected_qty=5,
                       shopify_product_id="gid://shopify/Product/123456789")
    flagged = BatchItem(batch_id=b.id, scanned_code="222", resolved=True,
                        sku="OPTO-LPRO", barcode="222",
                        product_title="Optolong L-Pro 2in (won't-scan test)",
                        shopify_variant_id="t:2", qty_scanned=2,
                        paired_count=2, bin_location="T1-1",
                        expected_qty=2)
    # Found MORE than Shopify knew about: the on-hand fix button's case.
    surplus = BatchItem(batch_id=b.id, scanned_code="333", resolved=True,
                        sku="SURPLUS-1", barcode="333",
                        product_title="Baader Filter Drawer (surplus test)",
                        shopify_variant_id="t:3", qty_scanned=4,
                        paired_count=4, bin_location="T1-1",
                        expected_qty=2)
    surplus2 = BatchItem(batch_id=b.id, scanned_code="444", resolved=True,
                         sku="SURPLUS-2", barcode="444",
                         product_title="Antlia ALP-T (second surplus)",
                         shopify_variant_id="t:4", qty_scanned=3,
                         paired_count=3, bin_location="T1-1",
                         expected_qty=1)
    # Already-tagged exception: boxes stickered on an earlier side trip —
    # 0 scanned / 0 paired here is CORRECT; the sweep hears their tags.
    pretag = BatchItem(batch_id=b.id, scanned_code="555", resolved=True,
                       sku="PRETAG-1", barcode="555",
                       product_title="Askar FMA180 (already-tagged test)",
                       shopify_variant_id="t:5", qty_scanned=0,
                       paired_count=0, tagged_before=2,
                       bin_location="T1-1", expected_qty=2)
    # The F9384A shape: 1 scanned+paired here, but the sweep also hears a
    # second tag recorded at ANOTHER shelf -> flagged, expandable, and
    # resolvable by setting already-tagged to 1.
    mis = BatchItem(batch_id=b.id, scanned_code="666", resolved=True,
                    sku="MIS-1", barcode="666",
                    product_title="Svbony SV405CC (mismatch test)",
                    shopify_variant_id="t:6", qty_scanned=1,
                    paired_count=1, bin_location="T1-1", expected_qty=2)
    s.add_all([normal, flagged, surplus, surplus2, pretag, mis])
    # Recommended checks with different mismatch sizes, for the sort.
    s.add_all([
        ReviewTask(category="inventory-check", sku="SMALL-1",
                   product_title="Small mismatch",
                   detail="Bin A1-1: 3 unit(s) counted but Shopify "
                          "on-hand is 4. Recommend a product check."),
        ReviewTask(category="inventory-check", sku="BIG-1",
                   product_title="Big mismatch",
                   detail="Bin B2-2: 1 unit(s) counted but Shopify "
                          "on-hand is 9. Recommend a product check."),
        ReviewTask(category="inventory-check", sku="MID-1",
                   product_title="Middle mismatch",
                   detail="Bin C3-3: 8 unit(s) counted but Shopify "
                          "on-hand is 5. Recommend a product check."),
        # Sold-out shortcut demo (Nick, 2026-08-26): fake on-hand 0 with
        # 2 live tags -> the resolve window offers Mark-all-presumed-sold.
        ReviewTask(category="tag-onhand-mismatch", sku="SOLDOUT-1",
                   product_title="Askar 71F (sold-out demo)",
                   detail="RFID tags stand for 2 unit(s) but the "
                          "expected count is 0 (Shopify on-hand 0). "
                          "Recommend a bin audit."),
    ])
    s.add(RfidIncompatible(sku="OPTO-LPRO", set_by="Steve"))
    epcs = {
        "NORMAL-1": ["AAAA0000000000000000000A", "AAAA0000000000000000000B"],
        "OPTO-LPRO": ["BBBB0000000000000000000A", "BBBB0000000000000000000B"],
    }
    # PRETAG-1's tags predate the batch (no batch_id, no barcode on the
    # tag rows — the SKU-only match must claim them, not call them
    # foreign) and the sweep heard both.
    # ...and a third earlier tag the sweep does NOT hear, so verify's
    # expanded row offers the manual retire button locally.
    for epc in ("CCCC0000000000000000000A", "CCCC0000000000000000000B",
                "CCCC0000000000000000000C"):
        s.add(RfidAssignment(rfid_id=epc, shopify_variant_id="t:5",
                             product_title="Askar FMA180",
                             sku="pretag-1", bin_location="T1-1"))
    for sku, pair in epcs.items():
        title = ("Baader UHC Filter 2in" if sku == "NORMAL-1"
                 else "Optolong L-Pro 2in (won't-scan test)")
        for epc in pair:
            s.add(PrintJob(epc=epc, status="done", batch_id=b.id,
                           shopify_variant_id="t:x", product_title=title,
                           sku=sku, barcode="111" if sku == "NORMAL-1"
                           else "222", bin_location="T1-1"))
            s.add(RfidAssignment(rfid_id=epc, shopify_variant_id="t:x",
                                 product_title=title, sku=sku,
                                 barcode="111" if sku == "NORMAL-1"
                                 else "222", bin_location="T1-1",
                                 batch_id=b.id))
    # MIS-1: one tag paired in this batch, one recorded at another shelf.
    s.add(RfidAssignment(rfid_id="DDDD0000000000000000000A",
                         shopify_variant_id="t:6",
                         product_title="Svbony SV405CC", sku="MIS-1",
                         barcode="666", bin_location="T1-1",
                         batch_id=b.id))
    s.add(RfidAssignment(rfid_id="DDDD0000000000000000000B",
                         shopify_variant_id="t:6",
                         product_title="Svbony SV405CC", sku="MIS-1",
                         bin_location="Z9-9"))
    # The C72 sweep heard the normal product, the pre-tagged boxes, and
    # BOTH MIS-1 tags.
    swept = epcs["NORMAL-1"] + [
        "CCCC0000000000000000000A", "CCCC0000000000000000000B",
        "DDDD0000000000000000000A", "DDDD0000000000000000000B",
    ]
    s.add(EpcCapture(device="C72-test", note="Bin T1-1 verify sweep",
                     batch_id=b.id, epc_count=len(swept),
                     epcs="\n".join(swept)))
    # Audit-board data: Steve's worked example (score 6), a score-4 bin,
    # an untagged bin, and the T1-1 products (received-not-found: NORMAL
    # shows 5 on hand, only 2 tagged).
    def m(sku, title, bin_, qty, barcode=None):
        return BinMapEntry(sku=sku, product_title=title, bin=bin_, qty=qty,
                           barcode=barcode, shopify_variant_id="t:" + sku)
    # Barcodes on the T1-1 rows so scan-station lookups (and C72 LINK
    # relays) resolve locally from the live bin map, like prod does.
    nm = m("NORMAL-1", "Baader UHC Filter 2in", "T1-1", 5, barcode="111")
    nm.shopify_product_id = "gid://shopify/Product/123456789"
    s.add_all([
        nm,
        m("OPTO-LPRO", "Optolong L-Pro 2in (won't-scan test)", "T1-1", 2,
          barcode="222"),
        # Expected here, nothing tagged: the row that hides behind the
        # audit's untagged toggle.
        m("PROD-Z", "Product Z (never tagged)", "BIN-T", 3),
        m("PROD-A", "Product A", "BIN-T", 6),
        m("PROD-B", "Product B", "BIN-T", 5),
        m("PROD-C", "Product C", "BIN-T", 4),
        m("PROD-F", "Product F", "BIN-D", 6),
        m("PROD-U", "Product U (untagged bin)", "BIN-U", 2),
        # Tags say K4-1, Shopify says J2-2: the Inventory tab's
        # "⇢ Shopify" offer (and the K4-1 chip's no-wrap fix).
        m("MISMATCH-1", "Mismatch Demo (tags K4-1, Shopify J2-2)",
          "J2-2", 1, barcode="999"),
    ])
    s.add(RfidAssignment(rfid_id="MMMM0000000000000000000M",
                         shopify_variant_id="t:MM", sku="MISMATCH-1",
                         product_title="Mismatch Demo (tags K4-1, "
                                       "Shopify J2-2)",
                         bin_location="K4-1"))
    # BIN-T was batch-tagged to completion; BIN-D only has stray tags.
    from datetime import datetime, timezone
    done_at = datetime.now(timezone.utc)
    real_done = Batch(bin_name="BIN-T", status="done",
                      completed_at=done_at, created_by="Steve")
    s.add(real_done)
    s.flush()
    # A finished SIDE TRIP into BIN-D: must show in Recently done with a
    # "side trip" chip, must NOT count BIN-D as a done bin, and History
    # must call it a side trip.
    s.add(Batch(bin_name="BIN-D", status="done", completed_at=done_at,
                created_by="Nick", parent_batch_id=real_done.id))
    # An already-tagged answer, for the History chip.
    from app.models import BarcodeChange
    s.add(BarcodeChange(sku="NORMAL-1", product_title="Baader UHC Filter",
                        changed_field="tagged-before", old_barcode="0",
                        new_barcode="2", changed_by="C72-test"))
    extra = {
        "PROD-A": ("BIN-T", 3), "PROD-B": ("BIN-T", 8),
        "PROD-C": ("BIN-T", 4), "PROD-F": ("BIN-D", 2),
    }
    n = 0
    for sku, (bin_, count) in extra.items():
        for _ in range(count):
            n += 1
            s.add(RfidAssignment(rfid_id=f"E{n:023d}",
                                 shopify_variant_id="t:x",
                                 product_title=sku, sku=sku,
                                 bin_location=bin_))
    # --- Receiving batch (the stepless planner-fed list) -----------------
    # One healthy in-progress product, one fully tagged, one with an
    # updated count (labels missing), one unknown-SKU problem row, one
    # non-taggable problem row, one no-bin row.
    rb = Batch(bin_name="RECEIVING", kind="receiving", status="collecting",
               created_by="TC-Planner · SO 935 · ZWO")
    s.add(rb)
    s.flush()
    now = datetime.now(timezone.utc)
    r1 = BatchItem(batch_id=rb.id, scanned_code="801", resolved=True,
                   sku="ZWO FL-HLDR-M54x15", barcode="801",
                   product_title="ZWO Filter Holder M54x15",
                   shopify_variant_id="t:R1", qty_scanned=14,
                   expected_qty=14, paired_count=9, bin_location="G2-1",
                   first_scanned_at=now)
    r2 = BatchItem(batch_id=rb.id, scanned_code="802", resolved=True,
                   sku="ZWO EAF-5V", barcode="802",
                   product_title="ZWO EAF 5V Electronic Focuser",
                   shopify_variant_id="t:R2", qty_scanned=3,
                   expected_qty=3, paired_count=3, bin_location="G2-2",
                   first_scanned_at=now)
    r3 = BatchItem(batch_id=rb.id, scanned_code="803", resolved=True,
                   sku="ZWO AM5-CW", barcode="803",
                   product_title="ZWO AM5 Counterweight",
                   shopify_variant_id="t:R3", qty_scanned=6,
                   expected_qty=4, paired_count=0, bin_location="F2-3",
                   first_scanned_at=now)
    r4 = BatchItem(batch_id=rb.id, scanned_code="GHOST-42", resolved=False,
                   qty_scanned=2, expected_qty=2, skipped=True,
                   skip_reason="Not found: no product matches this SKU or "
                               "barcode. Fix it in Shopify or link the "
                               "code at the Scan Station, then reprint.")
    r5 = BatchItem(batch_id=rb.id, scanned_code="SCREW-1", resolved=True,
                   sku="SCREW-1", product_title="M4 Thumbscrew (bag of 50)",
                   shopify_variant_id="t:R5", qty_scanned=50,
                   expected_qty=50, skipped=True,
                   skip_reason="Marked non-taggable: this product is kept "
                               "out of the RFID system, so no labels "
                               "print for it.")
    r6 = BatchItem(batch_id=rb.id, scanned_code="805", resolved=True,
                   sku="NEWPROD-1", barcode="805",
                   product_title="Askar 71F (brand new, no bin yet)",
                   shopify_variant_id="t:R6", qty_scanned=2,
                   expected_qty=2, paired_count=0, bin_location="",
                   first_scanned_at=now)
    s.add_all([r1, r2, r3, r4, r5, r6])
    rn = 0
    for item, done_n, pend_n in ((r1, 9, 5), (r2, 3, 0), (r3, 4, 0)):
        for st, cnt in (("done", done_n), ("pending", pend_n)):
            for _ in range(cnt):
                rn += 1
                s.add(PrintJob(epc=f"F{rn:023d}", status=st,
                               batch_id=rb.id, sku=item.sku,
                               product_title=item.product_title,
                               barcode=item.barcode,
                               bin_location=item.bin_location,
                               shopify_variant_id=item.shopify_variant_id))
    # --- Labels Not Printed demo (the Update-stock safety net) ----------
    # A planner push that updated Shopify WITHOUT printing: the boxes
    # are booked on their own receiving batch, no print jobs exist, and
    # one open Review task tracks the owed labels.
    ub = Batch(bin_name="RECEIVING", kind="receiving", status="collecting",
               created_by="TC-Planner · SO 940 · AirGradient")
    s.add(ub)
    s.flush()
    s.add_all([
        BatchItem(batch_id=ub.id, scanned_code="806", resolved=True,
                  sku="AG-ONE-KIT", barcode="806",
                  product_title="AirGradient One Kit (unprinted demo)",
                  shopify_variant_id="t:U1", qty_scanned=3,
                  expected_qty=3, bin_location="I5-1"),
        BatchItem(batch_id=ub.id, scanned_code="807", resolved=True,
                  sku="AG-OPEN-AIR", barcode="807",
                  product_title="AirGradient Open Air (unprinted demo)",
                  shopify_variant_id="t:U2", qty_scanned=2,
                  expected_qty=2, bin_location="I5-2"),
    ])
    unprinted_task = ReviewTask(
        category="labels-not-printed",
        product_title="TC-Planner · SO 940 · AirGradient",
        detail=("TC-Planner · SO 940 · AirGradient: stock was updated in "
                f"Shopify without printing labels. 5 label(s) are waiting "
                f"on receiving batch #{ub.id}. Resolve to queue them - "
                "the normal receiving print and pair flow takes over."),
        batch_id=ub.id, created_by="Nick",
    )
    s.add(unprinted_task)
    s.flush()
    from app.models import ReviewNote
    s.add(ReviewNote(task_key=str(unprinted_task.id),
                     note="Stock push without labels: 5 unit(s) across "
                          "2 product(s).",
                     created_by="Nick"))
    # --- Sold-out shortcut demo tags (fake on-hand for SOLDOUT-1 is 0) --
    for epc in ("50FD0000000000000000000A", "50FD0000000000000000000B"):
        s.add(RfidAssignment(rfid_id=epc, shopify_variant_id="t:SO",
                             product_title="Askar 71F (sold-out demo)",
                             sku="SOLDOUT-1", bin_location="T1-1"))
    # --- Check-step polish demos (Nick, 2026-08-26): a batch whose list
    # must order bad-chars > wrong-bin > count-mismatch, with the broken
    # character named per field.
    cb = Batch(bin_name="T3-1", status="collecting", created_by="Nick")
    s.add(cb)
    s.flush()
    s.add_all([
        BatchItem(batch_id=cb.id, scanned_code="906", resolved=True,
                  sku="COUNT-DEMO", barcode="906",
                  product_title="Baader Click-Lock (count nudge demo)",
                  shopify_variant_id="t:CN", qty_scanned=1,
                  expected_qty=5, bin_location="T3-1"),
        BatchItem(batch_id=cb.id, scanned_code="905", resolved=True,
                  sku="ZWO-HA 7nm 1.25?", barcode="905",
                  product_title="ZWO Ha 7nm Filter (broken-SKU demo)",
                  shopify_variant_id="gid://shopify/ProductVariant/77",
                  qty_scanned=1, expected_qty=1, bin_location="T3-1"),
        BatchItem(batch_id=cb.id, scanned_code="907", resolved=True,
                  sku="STRAY-DEMO", barcode="907",
                  product_title="Antlia 3nm (wrong shelf demo)",
                  shopify_variant_id="t:SD", qty_scanned=1,
                  expected_qty=1, bin_location="Z8-8"),
    ])
    # --- Backorder Noted history demo (undo = clear the note) -----------
    from app.models import BackorderDebt
    bd = BackorderDebt(sku="ZWO FL-HLDR-M54x15", units=1,
                       source="receiving · TC-Planner · SO 935 · ZWO")
    s.add(bd)
    s.flush()
    s.add(BarcodeChange(sku="ZWO FL-HLDR-M54x15",
                        product_title="ZWO Filter Holder M54x15",
                        changed_field="backorder-debt",
                        old_barcode=str(bd.id), new_barcode="1",
                        changed_by="TC-Planner · SO 935 · ZWO"))
    s.commit()
    print(f"seeded batch {b.id} on T1-1 + receiving batch {rb.id} "
          f"+ audit data")

# Fake Shopify inventory so the on-hand button works end-to-end locally.
from app import shopify as _sh  # noqa: E402
# MID-1: counted 8 > live 5 -> the resolve window offers Set-to-8.
# SMALL-1: live has caught up to the count -> one-click "agree" resolve.
# BIG-1: live above the count -> recount-with-note path.
_FAKE = {"SURPLUS-1": 2, "SURPLUS-2": 1, "NORMAL-1": 5, "OPTO-LPRO": 2,
         "MID-1": 5, "SMALL-1": 3, "BIG-1": 9,
         # 0 on hand + 2 live tags -> the sold-out shortcut appears.
         "SOLDOUT-1": 0}
def _fake_get(sku):
    return _FAKE.get(sku)
def _fake_set(sku, qty):
    before = _FAKE.get(sku, 0)
    _FAKE[sku] = int(qty)
    print(f"[fake shopify] on-hand {sku}: {before} -> {qty}")
    return before
_sh.get_on_hand = _fake_get
_sh.set_on_hand = _fake_set
# No-op the API lookup paths too: the live bin map answers product
# identity locally, and a dead API must degrade to that instead of
# surfacing token errors (mirrors the dev/tests mocking). One product
# answers so the "write bin to Shopify" buttons run end to end.
_MM = {"shopify_variant_id": "t:MM",
       "shopify_product_id": "gid://shopify/Product/424242",
       "product_title": "Mismatch Demo (tags K4-1, Shopify J2-2)",
       "variant_title": None, "sku": "MISMATCH-1", "barcode": "999",
       "bin_location": "J2-2", "image_url": None}
_sh.lookup_barcode = lambda code: (
    dict(_MM) if code in ("999", "MISMATCH-1") else None)
_sh.lookup_barcode_all = lambda code: (
    [dict(_MM)] if code in ("999", "MISMATCH-1") else [])
_sh.set_variant_bin = (
    lambda vid, b: print(f"[fake shopify] variant bin {vid} -> {b}"))
_sh.product_bin_info = lambda pid: {"variant_count": 1, "easy_bin": None}
_sh.set_product_bin = (
    lambda pid, b: print(f"[fake shopify] easyscan bin {pid} -> {b}"))
_sh.fetch_all_variant_bins = lambda: []
_sh.get_stock_info_by_skus = lambda skus: {}
_sh.get_quantities_by_skus = lambda skus: {}
_sh.get_on_hand_by_skus = lambda skus: {
    s: _FAKE[s] for s in skus if s in _FAKE}
# The broken-SKU demo: live Shopify still holds the real character our
# VARCHAR mangled to '?' (a double-prime), so the flag can NAME it.
_sh.get_variant_idents = lambda vid: (
    {"sku": "ZWO-HA 7nm 1.25″", "barcode": "905"}
    if vid == "gid://shopify/ProductVariant/77" else None)
# Native-bundle components (the Shopify Bundles / Bundles.app import):
# the MISMATCH-1 demo product answers as a bundle of 4 × NORMAL-1.
_sh.get_bundle_components = lambda gid: (
    [{"component_sku": "NORMAL-1", "qty": 4}] if gid == "t:MM" else [])

# Fake TC-Planner bridge: NORMAL-1 sits on an open PO so the on-order
# hint shows on both the Scan Station card and receiving collect.
from app import planner as _pl  # noqa: E402
_pl.health = lambda operator=None: {
    "configured": True, "ok": True,
    "service": "fake-planner", "identified_as": operator or "RFID",
    "app_url": "https://tc-planner-app.azurewebsites.net"}
def _fake_on_order(sku, operator=None):
    base = {"configured": True, "ok": True, "sku": sku,
            "total_remaining": 0, "orders": []}
    if (sku or "").upper() == "NORMAL-1":
        base["orders"] = [{
            "order_id": 10, "reference_number": 935,
            "vendor": "Sky-Watcher", "status": "partial_received",
            "expected_date": "2026-09-04",
            "ordered": 6, "received": 2, "remaining": 4,
        }]
        base["total_remaining"] = 4
    # Shipment-sort demo (Nick, 2026-08-31): a product split across TWO
    # open orders - scans fill the older one first, spill to the next,
    # then land in "no order explains these" once both are full.
    if (sku or "").upper() == "OPTO-LPRO":
        base["orders"] = [
            {"order_id": 11, "reference_number": 940,
             "vendor": "BuckeyeStargazer", "status": "partial_received",
             "expected_date": "2026-08-20",
             "ordered": 3, "received": 2, "remaining": 1},
            {"order_id": 12, "reference_number": 951,
             "vendor": "BuckeyeStargazer", "status": "open",
             "expected_date": "2026-09-05",
             "ordered": 2, "received": 0, "remaining": 2},
        ]
        base["total_remaining"] = 3
    return base
_pl.on_order_for_sku = _fake_on_order

# Fake 1-left dashboard: an in-memory pending queue shaped exactly like
# the Inventory Verification app's /pending answer, so the Audits panel
# (verdicts, auto-clear, confirm, re-queue) runs end to end offline.
from datetime import timedelta  # noqa: E402
from app import oneleft as _ol  # noqa: E402
_detected = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
def _pend(sku, title, vendor, bin_, stock):
    return {"sku": sku, "product_title": title, "vendor": vendor,
            "stock_bin": bin_, "barcode": "", "detected_date": _detected,
            "current_stock": stock}
_OL_QUEUE = [
    # Tags were paired at seed time (after detection) -> RFID answers it.
    _pend("NORMAL-1", "Baader UHC Filter 2in", "Baader", "T1-1",
          {"available": 1, "on_hand": 1}),
    # Never tagged -> needs a walk.
    _pend("PROD-Z", "Product Z (never tagged)", "Generic", "BIN-T",
          {"available": 1, "on_hand": 1}),
    # Shopify has since dropped to 0 -> walk it (never auto-cleared).
    _pend("GONE-1", "Celestron X-Cel 9mm (now zero)", "Celestron", "F2-2",
          {"available": 0, "on_hand": 0}),
    # Claims 0 but MIS-1 has tags paired after detection -> discrepancy.
    _pend("MIS-1", "Svbony SV405CC (mismatch test)", "Svbony", "T1-1",
          {"available": 0, "on_hand": 0}),
    # Their live stock fetch failed -> treated as claiming 1.
    _pend("PROD-A", "Product A", "Generic", "BIN-T", "?"),
]
def _ol_get(path):
    return {"success": True, "count": len(_OL_QUEUE), "items": _OL_QUEUE}
def _ol_post(path, body):
    global _OL_QUEUE
    if path == "/bulk-confirm":
        skus = set(body["skus"])
        confirmed = [i["sku"] for i in _OL_QUEUE if i["sku"] in skus]
        _OL_QUEUE = [i for i in _OL_QUEUE if i["sku"] not in skus]
        print(f"[fake 1-left] bulk confirm {confirmed} by {body['employee']}")
        return {"success": True, "confirmed_skus": confirmed,
                "not_found_skus": sorted(skus - set(confirmed))}
    if path == "/confirm":
        _OL_QUEUE = [i for i in _OL_QUEUE if i["sku"] != body["sku"]]
        print(f"[fake 1-left] confirm {body['sku']} by {body['employee']}")
        return {"success": True}
    if path == "/import-skus":
        sku = body["csv_content"].splitlines()[-1].strip()
        _OL_QUEUE.append(_pend(sku, f"Re-queued {sku}", "Generic", "", "?"))
        print(f"[fake 1-left] re-queued {sku}")
        return {"success": True}
    raise RuntimeError(f"unexpected 1-left call: {path}")
_ol._get = _ol_get
_ol._post = _ol_post

# Fake fulfilled orders: NORMAL-1 sold once (its 2 tags vs on-hand 1 + 1
# sold = consistent, no task), MIS-1 sold once (2 tags vs on-hand 0 + 1
# sold -> the sync files a tag-onhand-mismatch task; a bin audit whose
# sweep misses a MIS-1 tag then offers MARK SOLD).
_sh.get_fulfilled_orders = lambda since: [
    {"order_id": "gid://shopify/Order/9001", "name": "#9001",
     "fulfilled_at": datetime.now(timezone.utc).isoformat(),
     "lines": [{"sku": "NORMAL-1", "qty": 1}]},
    {"order_id": "gid://shopify/Order/9002", "name": "#9002",
     "fulfilled_at": datetime.now(timezone.utc).isoformat(),
     "lines": [{"sku": "MIS-1", "qty": 1}]},
]
_sh.get_on_hand_by_skus = lambda skus: {
    s: {"NORMAL-1": 1, "MIS-1": 0}.get((s or "").upper(), 0)
    for s in skus
}
# The 1-left confirm window's live tiles (MIS-1 shows an Unavailable one).
_sh.get_quantity_breakdown = lambda sku: {
    "available": 1, "committed": 1, "on_hand": 2,
    "unavailable": 1 if (sku or "").upper() == "MIS-1" else 0,
}

import uvicorn  # noqa: E402
uvicorn.run(app, host="127.0.0.1", port=8123)
