"""Print fixes (Nick, 2026-08-25):
- /api/print-jobs fills label content from the SAVED label store when
  the client sends none - a Scan Station print used to ignore a freshly
  edited SKU line (the ZWO Softbag1 case);
- /api/products/tags carries the saved label lines + on-hand for the
  card's preview;
- batch labels queue in the operator's WALKING order (first-scanned
  first), not the seeded alphabetical order;
- printer commands (re-align feed) queue and claim once."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_printorder_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_engine
from app.models import BinMapEntry, PrintJob
from sqlalchemy.orm import Session
from sqlalchemy import select
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

CAT = {
    "701": {"shopify_variant_id":"t:ALPHA","shopify_product_id":"g:1",
            "product_title":"Alpha Adapter","variant_title":None,
            "sku":"ALPHA-1","barcode":"701","bin_location":"P1-1"},
    "702": {"shopify_variant_id":"t:BRAVO","shopify_product_id":"g:2",
            "product_title":"Bravo Barlow","variant_title":None,
            "sku":"BRAVO-1","barcode":"702","bin_location":"P1-1"},
    "703": {"shopify_variant_id":"t:CHARLIE","shopify_product_id":"g:3",
            "product_title":"Charlie Cap","variant_title":None,
            "sku":"CHARLIE-1","barcode":"703","bin_location":"P1-1"},
}
def fake_lookup(t):
    if t in CAT: return dict(CAT[t])
    for p in CAT.values():
        if p["sku"] == t: return dict(p)
    return None

with patch("app.shopify.lookup_barcode", side_effect=fake_lookup), \
     patch("app.shopify.lookup_barcode_all",
           side_effect=lambda t: [fake_lookup(t)] if fake_lookup(t) else []), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.shopify.get_on_hand", return_value=7), \
     patch("app.main._kick_orders_sync_soon"):
  with TestClient(app) as cl:
    # Saved two-line label for ALPHA-1.
    cl.put("/api/label-names/ALPHA-1", json={
        "top_text": "Telescopes Canada",
        "sku_line": "Alpha Adapter Mk II", "updated_by": "Nick"})

    # 1) Scan Station print with NO label_name -> the saved line applies.
    r = cl.post("/api/print-jobs", json={
        "quantity": 1, "shopify_variant_id": "t:ALPHA",
        "product_title": "Alpha Adapter", "sku": "ALPHA-1",
        "barcode": "701", "requested_by": "Nick"})
    job = r.json()["jobs"][0]
    # A custom centre line with a default top is stored as
    # placement="sku" (the agent then keeps the store header and puts
    # the name on the SKU line) - assert THAT shape.
    check("scan-station print pulls the SAVED label lines",
          r.status_code == 201
          and job["label_name"] == "Alpha Adapter Mk II"
          and job["label_placement"] == "sku", job)
    # An explicit label_name (the serial flow) is never overridden.
    r = cl.post("/api/print-jobs", json={
        "quantity": 1, "shopify_variant_id": "t:ALPHA",
        "product_title": "Alpha Adapter", "sku": "ALPHA-1",
        "label_name": "UHC Filter 2in", "requested_by": "Nick"})
    job = r.json()["jobs"][0]
    check("an explicit label_name wins untouched",
          job["label_name"] == "UHC Filter 2in"
          and not job.get("label_sku"), job)

    # 2) The card's data ride-along.
    tg = cl.get("/api/products/tags?sku=ALPHA-1").json()
    check("tags endpoint carries the saved label lines",
          tg.get("label_name") == "Alpha Adapter Mk II"
          and tg.get("label_placement") == "sku", tg)
    check("tags endpoint carries on-hand", tg.get("on_hand") == 7, tg)

    # 3) Walking order: the bin pre-seeds alphabetically; scanning
    # CHARLIE then ALPHA (BRAVO untouched, no boxes) must queue
    # CHARLIE's labels first.
    bid = cl.post("/api/batches",
                  json={"bin": "P1-1", "created_by": "Nick"}).json()["id"]
    cl.post(f"/api/batches/{bid}/scan", json={"code": "703"})
    cl.post(f"/api/batches/{bid}/scan", json={"code": "703"})
    cl.post(f"/api/batches/{bid}/scan", json={"code": "701"})
    r = cl.post(f"/api/batches/{bid}/queue-labels", json={})
    check("labels queue", r.status_code in (200, 201), r.text[:200])
    with Session(get_engine()) as s:
        rows = s.scalars(select(PrintJob).where(PrintJob.batch_id == bid)
                         .order_by(PrintJob.id)).all()
        seq = [r.sku for r in rows]
    check("jobs come out in the WALKING order (Charlie first)",
          seq == ["CHARLIE-1", "CHARLIE-1", "ALPHA-1"], seq)

    # 4) Printer commands: queue once, claim once, then empty. The
    # command poll doubles as the re-align capability heartbeat.
    st = cl.get("/api/print-agent/status").json()
    check("before any command poll the agent reads not-capable",
          st.get("realign_capable") is False, st)
    r = cl.post("/api/printer-commands", json={
        "kind": "feed", "requested_by": "Nick"})
    check("re-align command queues", r.status_code == 201, r.text)
    r = cl.post("/api/printer-commands/claim?agent_version=2")
    check("agent claims the command",
          r.status_code == 200 and r.json()["count"] == 1
          and r.json()["commands"][0]["kind"] == "feed", r.text)
    r = cl.post("/api/printer-commands/claim?agent_version=2")
    check("a claim clears the queue", r.json()["count"] == 0, r.text)
    st = cl.get("/api/print-agent/status").json()
    check("a polling agent reads re-align capable with its version",
          st.get("realign_capable") is True
          and st.get("agent_version") == "2", st)
    r = cl.get("/api/print-agent/script")
    check("the current agent script downloads from the app",
          r.status_code == 200 and b"AGENT_VERSION" in r.content,
          r.status_code)

    # 5) Scan-station prints carry their per-product-load session so the
    # queue can group "printed 1, then 9, then 4" as runs of one thing.
    r = cl.post("/api/print-jobs", json={
        "quantity": 2, "shopify_variant_id": "t:ALPHA",
        "product_title": "Alpha Adapter", "sku": "ALPHA-1",
        "print_session": "sess-abc123", "requested_by": "Nick"})
    check("print jobs store their session token",
          all(j["print_session"] == "sess-abc123"
              for j in r.json()["jobs"]), r.json()["jobs"])

    # 6) Stop printing (Nick, 2026-08-25): cancels everything still
    # waiting, leaves whatever already printed alone, logs to History,
    # and refuses politely when nothing is happening.
    r = cl.post("/api/print-jobs/stop", json={"requested_by": "Nick"})
    check("stop cancels the waiting labels",
          r.status_code == 200 and r.json()["canceled"] >= 2, r.text[:200])
    with Session(get_engine()) as s:
        left = s.scalars(select(PrintJob).where(
            PrintJob.status == "pending")).all()
        check("no jobs are left waiting after a stop", not left,
              [j.id for j in left])
    ev = [e for e in cl.get("/api/history?limit=100").json()["events"]
          if e["type"] == "printing-stopped"]
    check("the stop is logged to History",
          ev and "canceled" in (ev[0]["detail"] or ""), ev[:1])
    r = cl.post("/api/print-jobs/stop", json={})
    check("stop with an idle printer says so",
          r.status_code == 422, r.text[:200])

    # 7) Resume printing (Nick, 2026-08-26): the Stop button's inverse.
    # Stopped labels never printed, so the SAME jobs return to pending -
    # original ids, original order - and the listing counts them so the
    # button can light up regardless of pagination.
    with Session(get_engine()) as s:
        stopped_ids = [j.id for j in s.scalars(
            select(PrintJob).where(PrintJob.status == "canceled")
            .order_by(PrintJob.id)).all()]
    q = cl.get("/api/print-jobs?limit=5").json()
    check("the queue listing counts resumable stopped labels",
          q.get("resumable_stopped") == len(stopped_ids), q.get(
              "resumable_stopped"))
    r = cl.post("/api/print-jobs/resume", json={"requested_by": "Nick"})
    check("resume brings every stopped label back",
          r.status_code == 200 and r.json()["resumed"] == len(stopped_ids),
          r.text[:200])
    with Session(get_engine()) as s:
        pend = s.scalars(select(PrintJob).where(
            PrintJob.status == "pending").order_by(PrintJob.id)).all()
        check("the SAME jobs are pending again - original ids and order",
              [j.id for j in pend] == stopped_ids
              and all(j.error is None for j in pend),
              [j.id for j in pend])
    ev = [e for e in cl.get("/api/history?limit=100").json()["events"]
          if e["type"] == "printing-resumed"]
    check("the resume is logged to History",
          ev and "resumed" in (ev[0]["detail"] or ""), ev[:1])
    r = cl.post("/api/print-jobs/resume", json={})
    check("resume with nothing stopped says so",
          r.status_code == 422, r.text[:200])

    # A stop against a batch that then finishes stays canceled: re-stop,
    # mark the batch done, resume finds only the batchless jobs.
    cl.post("/api/print-jobs/stop", json={"requested_by": "Nick"})
    with Session(get_engine()) as s:
        from app.models import Batch
        b = s.get(Batch, bid)
        b.status = "abandoned"
        s.commit()
        batchless = [j.id for j in s.scalars(
            select(PrintJob).where(PrintJob.status == "canceled",
                                   PrintJob.batch_id.is_(None))
            .order_by(PrintJob.id)).all()]
    r = cl.post("/api/print-jobs/resume", json={"requested_by": "Nick"})
    check("a finished batch's stopped labels stay canceled",
          (r.status_code == 200 and r.json()["resumed"] == len(batchless))
          or (r.status_code == 422 and not batchless), r.text[:200])

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
