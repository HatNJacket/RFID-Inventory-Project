"""NOT OURS foreign-tag write-off + unpaired-list dismissal (Nick,
2026-09-15): a tag physically found that doesn't belong to the store
gets marked not-ours (permanent dismissal, printed labels REFUSED,
marker undo), and the unpaired-labels list carries item_id so a
product sold without being labelled can be dismissed straight from it.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_notours_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

FOREIGN  = "DEAD0000000000000000000A"   # somebody else's tag
FOREIGN2 = "DEAD0000000000000000000B"
OWNED    = "DEAD0000000000000000000C"   # our live tag
PRINTED  = "DEAD0000000000000000000D"   # our printed receiving label

with patch("app.main.oneleft") as ol:
  with TestClient(app) as cl:
    from sqlalchemy import select
    from sqlalchemy.orm import Session as S
    from app.database import get_engine
    from app.models import (Batch, BatchItem, LabelDismissal,
                            LocateQueueEntry, PrintJob, RfidAssignment)

    # A receiving batch owing one label for OWED-1 (printed, unpaired).
    with S(get_engine()) as s:
        s.add(RfidAssignment(rfid_id=OWNED, shopify_variant_id="t:1",
                             product_title="Ours", sku="OURS-1",
                             bin_location="A1-1"))
        b = Batch(bin_name="RECV", kind="receiving", status="open",
                  created_by="Nick")
        s.add(b); s.flush()
        s.add(BatchItem(batch_id=b.id, scanned_code="111", resolved=True,
                        sku="OWED-1", barcode="111",
                        product_title="Owed One", qty_scanned=1,
                        bin_location="B2-1"))
        s.add(PrintJob(epc=PRINTED, status="done", batch_id=b.id,
                       shopify_variant_id="t:9",
                       product_title="Owed One", sku="OWED-1",
                       barcode="111", bin_location="B2-1"))
        s.commit()
        batch_id = b.id

    # Sweep hears the foreign tags + our label -> they stash.
    cl.post("/api/epc-captures", json={
        "device": "C72-test", "epcs": [FOREIGN, FOREIGN2, PRINTED]})
    q = cl.get("/api/locate-queue").json()["entries"]
    e = next((x for x in q if x.get("epc_hunt")), {})
    check("sweep stashed foreign + printed", e.get("tag_count") == 3, e)

    # ---- NOT OURS refuses our printed label ---------------------------
    r = cl.post("/api/epcs/not-ours", json={
        "epcs": [PRINTED], "worker": "Nick"})
    d = r.json()
    check("printed label refused", r.status_code == 200
          and d.get("ignored") == 0 and d.get("printed_labels") == 1, d)
    check("refusal wrote no dismissal", d.get("marker") is None, d)

    # ---- NOT OURS on genuinely foreign tags ---------------------------
    r = cl.post("/api/epcs/not-ours", json={
        "epcs": [FOREIGN, FOREIGN2, OWNED], "worker": "Nick"})
    d = r.json()
    check("both foreign tags marked", d.get("ignored") == 2, d)
    check("owned tag untouched (not counted)", d.get("printed_labels") == 0, d)
    marker = d.get("marker")
    check("marker returned", bool(marker) and marker.startswith("not-ours"),
          d)
    with S(get_engine()) as s:
        rows = s.scalars(select(LabelDismissal).where(
            LabelDismissal.dismissed_by == marker)).all()
        check("2 dismissal rows under the marker", len(rows) == 2, rows)
        owned_rows = s.scalars(select(LabelDismissal).where(
            LabelDismissal.epc == OWNED)).all()
        check("no dismissal for the owned tag", owned_rows == [], owned_rows)

    # Hunt entry pruned immediately (printed label remains owed/hunted).
    q = cl.get("/api/locate-queue").json()["entries"]
    e = next((x for x in q if x.get("epc_hunt")), {})
    check("hunt entry keeps only our printed label",
          e.get("tag_count") == 1 and e.get("epcs") == [PRINTED], e)

    # /api/epcs/unlinked no longer classifies them as unlinked.
    r = cl.post("/api/epcs/unlinked", json={"epcs": [FOREIGN, PRINTED]})
    check("classifier drops the foreign tag",
          r.json().get("unlinked") == [PRINTED], r.text[:200])

    # Future sweeps never re-stash them.
    r = cl.post("/api/epc-captures", json={
        "device": "C72-test", "epcs": [FOREIGN, FOREIGN2]})
    check("re-heard foreign tags don't re-stash",
          not r.json().get("unlinked_stashed"), r.text[:200])
    q = cl.get("/api/locate-queue").json()["entries"]
    e = next((x for x in q if x.get("epc_hunt")), {})
    check("hunt entry still only our printed label",
          e.get("epcs") == [PRINTED], e)

    # History event + marker undo.
    h = cl.get("/api/history?limit=20").json()["events"]
    ev = next((x for x in h if x["type"] == "not-our-tag"), None)
    check("history shows not-our-tag event", ev is not None, h[:3])
    check("event offers the marker undo",
          ev and ev.get("undo", {}).get("kind") == "unpaired-ignore"
          and ev["undo"].get("marker") == marker, ev)
    r = cl.post("/api/epcs/ignore-heard/undo", json={
        "marker": marker, "worker": "Nick"})
    check("undo removes the dismissals", r.status_code == 200, r.text[:200])
    with S(get_engine()) as s:
        left = s.scalars(select(LabelDismissal)).all()
        check("dismissal table empty again", left == [], left)

    # ---- unpaired-labels list: item_id + sold-without-label ----------
    r = cl.get("/api/receiving/unpaired-labels").json()
    rows = r.get("products", [])
    check("owed label on the list", len(rows) == 1
          and rows[0]["sku"] == "OWED-1" and rows[0]["count"] == 1, r)
    item_id = rows[0].get("item_id") if rows else None
    check("row carries its receiving item_id", isinstance(item_id, int),
          rows)

    r = cl.post(
        f"/api/batches/{batch_id}/items/{item_id}/dismiss-sold",
        json={"worker": "Nick"})
    check("sold-without-label dismissal accepted", r.status_code == 200,
          r.text[:200])
    r = cl.get("/api/receiving/unpaired-labels").json()
    check("list empty after dismissal", r.get("count") == 0, r)

print()
if fails:
    print(f"{len(fails)} FAILURE(S):"); [print("  -", f) for f in fails]
    sys.exit(1)
print("ALL PASS")
