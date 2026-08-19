"""Re-tagging a previously-done bin (Nick, 2026-08-19): prev_done_at
flag, untouched-batch auto-expiry, the check-step shelf sweep with its
match/unheard/silent verdicts, presumed-sold retirement + undo,
dead-tag replacement, and tombstone recognition in sweeps."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["SHOPIFY_WRITE_MODE"]="scan_station_only,verify_onhand"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_retag_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

# Catalog: A sold-down (4 tags, on-hand 2), B weak-tag (3 tags, on-hand
# 3), C all-silent (2 tags, on-hand 2), D never tagged.
PRODUCTS = {
    "111": {"sku": "RETAG-A", "title": "ZWO ASIAIR Plus"},
    "222": {"sku": "RETAG-B", "title": "Telrad Finder"},
    "333": {"sku": "RETAG-C", "title": "Baader Solar Film"},
    "444": {"sku": "RETAG-D", "title": "Svbony SV220"},
}
ONHAND = {"RETAG-A": 2, "RETAG-B": 3, "RETAG-C": 2, "RETAG-D": 3}

def fake_lookup(t):
    p = PRODUCTS.get(t)
    if p is None:
        for bc, q in PRODUCTS.items():
            if q["sku"].upper() == str(t).upper():
                p, t = q, bc
                break
    if p is None: return None
    return {"shopify_variant_id": "gid:" + p["sku"],
            "shopify_product_id": "gid:p" + p["sku"],
            "product_title": p["title"], "variant_title": None,
            "sku": p["sku"], "barcode": t, "bin_location": "F1-1"}

with patch("app.shopify.lookup_barcode", side_effect=fake_lookup), \
     patch("app.shopify.lookup_barcode_all",
           side_effect=lambda t: [fake_lookup(t)] if fake_lookup(t) else []), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_on_hand", return_value=2), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus",
           side_effect=lambda skus: {
               s: ONHAND[k] for s in skus
               for k in [s.upper().replace("RETAG", "RETAG")]
               if k in ONHAND
           }), \
     patch("app.main._kick_orders_sync_soon"):
  with TestClient(app) as cl:
    from sqlalchemy import select
    from sqlalchemy.orm import Session as S
    from app.database import get_engine
    from app.models import Batch, RetiredTag, RfidAssignment

    # ---- seed: a DONE full batch on F1-1 + this bin's old tags --------
    with S(get_engine()) as s:
        s.add(Batch(bin_name="F1-1", status="done", created_by="Nick",
                    completed_at=datetime.now(timezone.utc)
                    - timedelta(days=90)))
        tag_rows = [
            ("RETAG-A", 4), ("RETAG-B", 3), ("RETAG-C", 2),
        ]
        n = 0
        for sku, count in tag_rows:
            for i in range(count):
                n += 1
                s.add(RfidAssignment(
                    rfid_id=f"E200{n:020d}",
                    shopify_variant_id="gid:" + sku,
                    product_title=PRODUCTS[
                        [b for b, p in PRODUCTS.items()
                         if p["sku"] == sku][0]]["title"],
                    sku=sku, bin_location="F1-1",
                ))
        s.commit()

    # ---- prev_done_at rides list + detail ------------------------------
    bid = cl.post("/api/batches",
                  json={"bin": "F1-1", "created_by": "Nick"}).json()["id"]
    lst = cl.get("/api/batches?status=open").json()["batches"]
    me = next(b for b in lst if b["id"] == bid)
    check("open list carries prev_done_at for a re-tag bin",
          bool(me.get("prev_done_at")), me)
    det = cl.get(f"/api/batches/{bid}").json()
    check("batch detail carries prev_done_at",
          bool(det["batch"].get("prev_done_at")), det["batch"])
    check("no shelf sweep yet", det["batch"]["shelf_swept_at"] is None,
          det["batch"])
    b2 = cl.post("/api/batches",
                 json={"bin": "ZZ-9", "created_by": "Nick"}).json()["id"]
    lst = cl.get("/api/batches?status=open").json()["batches"]
    other = next(b for b in lst if b["id"] == b2)
    check("a never-done bin has NO prev_done_at",
          not other.get("prev_done_at"), other)

    # ---- auto-expiry: untouched > 4h quietly abandons -------------------
    with S(get_engine()) as s:
        row = s.get(Batch, b2)
        row.created_at = datetime.now(timezone.utc) - timedelta(hours=5)
        s.commit()
    lst = cl.get("/api/batches?status=open").json()["batches"]
    check("untouched 5h-old batch auto-expired",
          all(b["id"] != b2 for b in lst), lst)
    with S(get_engine()) as s:
        check("expired batch is marked abandoned",
              s.get(Batch, b2).status == "abandoned",
              s.get(Batch, b2).status)

    # ---- collect: 2×A, 3×B, 1×C, 3×D ------------------------------------
    for code, times in (("111", 2), ("222", 3), ("333", 1), ("444", 3)):
        for _ in range(times):
            cl.post(f"/api/batches/{bid}/scan", json={"code": code})
    items = {i["sku"]: i for i in cl.get(
        f"/api/batches/{bid}").json()["items"] if i["sku"]}

    # ---- shelf sweep: hears 2×A, 1×B, none of C -------------------------
    with S(get_engine()) as s:
        a_epcs = [t.rfid_id for t in s.scalars(
            select(RfidAssignment).where(RfidAssignment.sku == "RETAG-A")
        )][:2]
        b_epc = [t.rfid_id for t in s.scalars(
            select(RfidAssignment).where(RfidAssignment.sku == "RETAG-B")
        )][:1]
    swept = a_epcs + b_epc + ["E200FFFFFFFFFFFFFFFF0001"]
    r = cl.post(f"/api/batches/{bid}/shelf-sweep",
                json={"epcs": swept, "device": "C72",
                      "apply": False}).json()
    st = {x["sku"]: x for x in r["items"]}
    check("A: heard 2 = expected min(4 on file, 2 on-hand) -> match",
          st["RETAG-A"]["state"] == "match"
          and st["RETAG-A"]["presumed_sold"] == 2, st.get("RETAG-A"))
    check("B: heard 1 of expected 3 -> unheard (yellow)",
          st["RETAG-B"]["state"] == "unheard", st.get("RETAG-B"))
    check("C: none heard of expected 2 -> silent (red)",
          st["RETAG-C"]["state"] == "silent", st.get("RETAG-C"))
    check("D: no records -> none",
          st["RETAG-D"]["state"] == "none", st.get("RETAG-D"))
    check("unmatched EPC counted unknown", r["unknown"] == 1, r)
    check("preview did not store a sweep",
          cl.get(f"/api/batches/{bid}").json()["batch"]["shelf_swept_at"]
          is None, "")

    r = cl.post(f"/api/batches/{bid}/shelf-sweep",
                json={"epcs": swept, "device": "C72",
                      "apply": True}).json()
    det = cl.get(f"/api/batches/{bid}").json()
    check("apply stamps shelf_swept_at",
          det["batch"]["shelf_swept_at"] is not None, det["batch"])
    items = {i["sku"]: i for i in det["items"] if i["sku"]}
    check("apply writes tagged_before from heard (A=2, B=1, C=0)",
          items["RETAG-A"]["tagged_before"] == 2
          and items["RETAG-B"]["tagged_before"] == 1
          and items["RETAG-C"]["tagged_before"] == 0,
          {k: v["tagged_before"] for k, v in items.items()})
    g = cl.get(f"/api/batches/{bid}/shelf-sweep").json()
    check("stored sweep re-reconciles on GET",
          g.get("swept") is True and any(
              x["sku"] == "RETAG-B" and x["state"] == "unheard"
              for x in g["items"]), g)

    # ---- check list carries the verdicts --------------------------------
    rev = cl.get(f"/api/batches/{bid}/review").json()
    by = {e["item"]["sku"]: e for e in rev["items"] if e["item"]["sku"]}
    check("review flags B tags-unheard",
          "tags-unheard" in by.get("RETAG-B", {}).get("flags", []),
          by.get("RETAG-B", {}).get("flags"))
    check("review flags C tags-silent",
          "tags-silent" in by.get("RETAG-C", {}).get("flags", []),
          by.get("RETAG-C", {}).get("flags"))
    check("flagged entries carry the shelf payload",
          by.get("RETAG-B", {}).get("shelf", {}).get("on_file") == 3,
          by.get("RETAG-B", {}).get("shelf"))

    # ---- "won't RFID scan" products sit the verdicts out ------------------
    from app.models import BatchItem, RfidIncompatible
    with S(get_engine()) as s:
        s.add(RfidIncompatible(sku="RETAG-C", set_by="Nick"))
        # A hand-set already-tagged count on the mute product — the
        # sweep must NOT zero it (that would print double labels).
        row = s.scalar(select(BatchItem).where(
            BatchItem.batch_id == bid, BatchItem.sku == "RETAG-C"))
        row.tagged_before = 2
        s.commit()
    r = cl.post(f"/api/batches/{bid}/shelf-sweep",
                json={"epcs": swept, "device": "C72",
                      "apply": True}).json()
    st = {x["sku"]: x for x in r["items"]}
    check("noscan product reads 'noscan', never silent-red",
          st["RETAG-C"]["state"] == "noscan", st.get("RETAG-C"))
    items = {i["sku"]: i for i in cl.get(
        f"/api/batches/{bid}").json()["items"] if i["sku"]}
    check("apply leaves a noscan product's hand-set count alone",
          items["RETAG-C"]["tagged_before"] == 2
          and items["RETAG-A"]["tagged_before"] == 2,
          {k: v["tagged_before"] for k, v in items.items()})
    with S(get_engine()) as s:
        s.delete(s.get(RfidIncompatible, "RETAG-C"))
        row = s.scalar(select(BatchItem).where(
            BatchItem.batch_id == bid, BatchItem.sku == "RETAG-C"))
        row.tagged_before = 0
        s.commit()

    # ---- dead-tag replacement -------------------------------------------
    b_item = items["RETAG-B"]
    r = cl.post(f"/api/batches/{bid}/items/{b_item['id']}/replace-tag",
                json={"changed_by": "Nick"})
    check("dead replace (no EPC) retires the oldest unheard record",
          r.status_code == 200 and r.json()["kind"] == "dead", r.text)
    check("dead replace never touches the heard tag",
          r.json()["retired_epc"] not in swept, r.json())
    wrong = cl.post(
        f"/api/batches/{bid}/items/{b_item['id']}/replace-tag",
        json={"epc": a_epcs[0], "changed_by": "Nick"})
    check("off-box read of ANOTHER product's tag is refused",
          wrong.status_code == 409, (wrong.status_code, wrong.text))
    with S(get_engine()) as s:
        left = [t.rfid_id for t in s.scalars(
            select(RfidAssignment).where(RfidAssignment.sku == "RETAG-B")
        ) if t.rfid_id not in swept]
    r = cl.post(f"/api/batches/{bid}/items/{b_item['id']}/replace-tag",
                json={"epc": left[0], "changed_by": "Nick"})
    check("off-box read retires that exact EPC as 'replaced'",
          r.status_code == 200 and r.json()["kind"] == "replaced"
          and r.json()["retired_epc"] == left[0], r.text)

    # ---- presumed-sold retire + tombstones + undo ------------------------
    a_all = None
    with S(get_engine()) as s:
        a_all = [t.rfid_id for t in s.scalars(
            select(RfidAssignment).where(RfidAssignment.sku == "RETAG-A")
        )]
    unheard_a = [e for e in a_all if e not in swept]
    r = cl.post("/api/assignments/retire",
                json={"epcs": unheard_a, "kind": "presumed-sold",
                      "changed_by": "Nick"})
    check("presumed-sold retire moves both unheard A records",
          r.status_code == 200 and len(r.json()["retired"]) == 2, r.text)
    with S(get_engine()) as s:
        check("retired table holds them forever",
              len(s.scalars(select(RetiredTag)).all()) == 4, "")
    # A retired EPC heard at verify is NAMED, not 'unknown'.
    v = cl.post(f"/api/batches/{bid}/verify",
                json={"epcs": [unheard_a[0]]}).json()
    check("verify names a heard tombstone instead of 'unknown'",
          len(v["retired_heard"]) == 1 and not v["unknown_epcs"],
          {"retired": v["retired_heard"], "unknown": v["unknown_epcs"]})
    check("presumed-sold tombstone reads as possible return",
          "return" in v["retired_heard"][0]["message"], v["retired_heard"])
    hist = cl.get("/api/history").json()
    kinds = [e for e in hist["events"] if e["type"] == "tag-retired"]
    check("every retirement is History-logged with an undo",
          len(kinds) >= 4 and all(
              e.get("undo", {}).get("kind") == "tag-retired"
              for e in kinds), kinds[:2])
    r = cl.post("/api/assignments/unretire",
                json={"epcs": [unheard_a[0]], "changed_by": "Nick"})
    check("unretire restores the record", r.status_code == 200, r.text)
    with S(get_engine()) as s:
        back = s.scalar(select(RfidAssignment).where(
            RfidAssignment.rfid_id == unheard_a[0]))
        check("restored row is active again with its SKU",
              back is not None and back.sku == "RETAG-A", "")

print()
if fails:
    print(f"{len(fails)} FAILED"); sys.exit(1)
print("ALL PASS"); sys.exit(0)
