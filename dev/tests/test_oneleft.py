"""1-left dashboard bridge: the Audits tab joins the Inventory
Verification queue against RFID evidence, auto-confirms fully-answered
checks (their /bulk-confirm — the ONLY writes are confirm/re-queue),
records receipts, and renders them in History. Verifies the evidence
rules, the gates, the fail-soft paths and the undo.
"""
import os, sys, tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
os.environ["ONELEFT_MODE"] = "confirm"
os.environ["ONELEFT_URL"] = "http://oneleft.test/api/api"
db = os.path.join(tempfile.gettempdir(), "rfid_oneleft_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")

from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session as S
from app.main import app
from app import oneleft
from app.database import get_engine
from app.models import Batch, BatchItem, RfidAssignment
import app.main as M

fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

NOW = datetime.now(timezone.utc).replace(tzinfo=None)
DETECT_DT = NOW - timedelta(days=2)
DETECT = DETECT_DT.replace(tzinfo=timezone.utc).isoformat()
BEFORE = NOW - timedelta(days=5)

def item(sku, on_hand=1, detected=DETECT, **kw):
    return {
        "sku": sku, "product_title": f"Product {sku}", "vendor": "TestCo",
        "stock_bin": kw.get("bin", "B1-1"), "barcode": f"bc-{sku}",
        "detected_date": detected,
        "current_stock": ("?" if on_hand is None else
                          {"available": on_hand, "on_hand": on_hand}),
    }

PENDING = [
    item("FRESH-TAG"),        # tag paired after detection -> confirmable
    item("OLD-TAG"),          # tag paired before detection -> needs-walk
    item("SWEPT"),            # old tag HEARD by a new sweep -> confirmable
    item("BATCHED"),          # counted in a post-detection batch
    item("NAKED"),            # no tags at all -> needs-walk
    item("GONE", on_hand=0),  # claim dropped to 0 -> never auto
    item("GHOST", on_hand=0), # claim 0 but evidence -> discrepancy
    item("MYSTERY", on_hand=None),  # their stock fetch failed -> claim 1
]

# Every remote call is captured here; nothing leaves the process.
calls = []
def fake_get(path):
    calls.append(("GET", path, None))
    return {"success": True, "count": len(PENDING), "items": PENDING}
def fake_post(path, body):
    calls.append(("POST", path, body))
    if path == "/bulk-confirm":
        return {"success": True, "confirmed_skus": body["skus"],
                "not_found_skus": []}
    return {"success": True}

kicks = []

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.shopify.get_on_hand", return_value=None), \
     patch("app.shopify.get_stock_info_by_skus", return_value={}), \
     patch("app.shopify.get_quantities_by_skus", return_value={}), \
     patch("app.oneleft._get", side_effect=fake_get), \
     patch("app.oneleft._post", side_effect=fake_post):
  M._maybe_refresh_bin_map = lambda *a, **k: False
  with TestClient(app) as cl:
    # Setup runs with kick() recorded, not executed, so the auto pass
    # below is a single deterministic call instead of a thread race.
    with patch("app.oneleft.kick",
               side_effect=lambda t, o=None: kicks.append(t)):
        def tag(sku, epc, at):
            r = cl.post("/api/rfid-assignments", json={
                "rfid_id": epc, "shopify_variant_id": "v1",
                "product_title": f"Product {sku}", "sku": sku,
                "assigned_by": "Nick"})
            assert r.status_code == 201, r.text
            with S(get_engine()) as s:  # backdate (the API stamps now)
                s.execute(update(RfidAssignment)
                          .where(RfidAssignment.rfid_id == epc)
                          .values(assigned_at=at))
                s.commit()
        # 2 min ago: still fresh vs a 2-day-old detection, but safely
        # OLDER than any receipt written during this run (sqlite stamps
        # rows at second granularity, so same-second ordering is a coin
        # flip — the requeue-pin checks need the pair to sort first).
        tag("FRESH-TAG", "A"*24, NOW - timedelta(seconds=120))
        tag("OLD-TAG",   "B"*24, BEFORE)
        tag("SWEPT",     "C"*24, BEFORE)
        tag("GHOST",     "D"*24, NOW)
        tag("MYSTERY",   "E"*24, NOW)

        r = cl.post("/api/epc-captures", json={
            "epcs": ["C"*24, "F"*24], "device": "C72-test"})
        check("sweep accepted", r.status_code == 201, r.text)
        check("pair and sweep both kicked an auto pass",
              "tag paired" in kicks and "C72 sweep" in kicks, str(kicks))

        with S(get_engine()) as s:
            batch = Batch(bin_name="B1-1", created_by="Nick",
                          status="collecting")
            s.add(batch); s.flush()
            s.add(BatchItem(batch_id=batch.id, scanned_code="bc-BATCHED",
                            sku="BATCHED", product_title="Product BATCHED",
                            qty_scanned=1))
            s.commit()

    # ---- evidence rules, one verdict per scenario --------------------
    r = cl.get("/api/oneleft/board")
    b = r.json()
    check("board ok", r.status_code == 200 and b["ok"], r.text)
    verdicts = {i["sku"]: i["verdict"] for i in b["items"]}
    check("fresh tag answers", verdicts.get("FRESH-TAG") == "confirmable",
          str(verdicts))
    check("stale tag does not", verdicts.get("OLD-TAG") == "needs-walk",
          str(verdicts))
    check("new sweep of old tag answers",
          verdicts.get("SWEPT") == "confirmable", str(verdicts))
    check("batch count answers", verdicts.get("BATCHED") == "confirmable",
          str(verdicts))
    check("no evidence needs a walk", verdicts.get("NAKED") == "needs-walk",
          str(verdicts))
    check("claim 0 never auto-clears", verdicts.get("GONE") == "zero-claim",
          str(verdicts))
    check("claim 0 with evidence flags",
          verdicts.get("GHOST") == "discrepancy", str(verdicts))
    check("unknown claim treated as 1",
          verdicts.get("MYSTERY") == "confirmable", str(verdicts))
    fresh = next(i for i in b["items"] if i["sku"] == "FRESH-TAG")
    check("evidence text names the source",
          any("paired" in d for d in fresh["evidence"]),
          str(fresh["evidence"]))

    # ---- the auto pass -----------------------------------------------
    calls.clear()
    r = cl.post("/api/oneleft/scan", json={"worker": "Nick"})
    check("scan ran", r.status_code == 200 and r.json()["ran"], r.text)
    bulk = [c for c in calls if c[1] == "/bulk-confirm"]
    check("one bulk-confirm", len(bulk) == 1, str(len(bulk)))
    if bulk:
        body = bulk[0][2]
        check("bulk-confirm only evidence-complete SKUs",
              sorted(body["skus"]) == ["BATCHED", "FRESH-TAG", "MYSTERY",
                                       "SWEPT"], str(body))
        check("employee mapped to a valid dashboard name (Nick isn't one)",
              body["employee"] in oneleft.VALID_EMPLOYEES, str(body))
    forbidden = [c for c in calls if any(
        bad in c[1] for bad in ("update-stock", "update-bin",
                                "update-barcode", "report-issue"))]
    check("never touches their stock/bin/barcode endpoints",
          not forbidden, str(forbidden))

    receipts = cl.get("/api/oneleft/board").json()["receipts"]
    auto = [x for x in receipts if x["action"] == "auto" and x["ok"]]
    check("receipts recorded for the auto pass", len(auto) == 4,
          str([(x['sku'], x['action'], x['ok']) for x in receipts]))
    check("receipt carries evidence text",
          any("paired" in (x["evidence"] or "") for x in auto),
          str([x["evidence"] for x in auto]))

    # ---- history shows the story -------------------------------------
    ev = cl.get("/api/history?limit=300").json()["events"]
    ol = [e for e in ev if e["type"] == "oneleft"]
    check("history has 1-left events", len(ol) >= 4, str(len(ol)))
    ph = cl.get("/api/product-history?term=FRESH-TAG").json()["events"]
    check("product history includes its 1-left clear",
          any(e["type"] == "oneleft" for e in ph),
          str([e["type"] for e in ph]))

    # ---- manual confirm + requeue undo -------------------------------
    calls.clear()
    r = cl.post("/api/oneleft/confirm", json={"sku": "NAKED",
                                              "worker": "Nick"})
    check("manual confirm allowed regardless of verdict",
          r.status_code == 200 and r.json()["ok"], r.text)
    check("manual confirm used /confirm with mapped employee",
          any(c[1] == "/confirm" and
              c[2]["employee"] in oneleft.VALID_EMPLOYEES for c in calls),
          str(calls))
    r = cl.post("/api/oneleft/requeue", json={"sku": "NAKED",
                                              "worker": "Nick"})
    check("requeue (undo) posts import-skus",
          r.status_code == 200 and any(
              c[1] == "/import-skus" and "NAKED" in c[2]["csv_content"]
              for c in calls), str(calls))

    # ---- a re-queue pins the check for a human -----------------------
    # FRESH-TAG's evidence would re-clear it on the next pass; after an
    # operator re-queues it, it must stay put until NEW evidence.
    r = cl.post("/api/oneleft/requeue", json={"sku": "FRESH-TAG",
                                              "worker": "Nick"})
    check("second requeue ok", r.status_code == 200, r.text)
    b2 = cl.get("/api/oneleft/board").json()
    v2 = {i["sku"]: i["verdict"] for i in b2["items"]}
    check("re-queued check is pinned, not confirmable",
          v2.get("FRESH-TAG") == "requeued", str(v2))
    calls.clear()
    cl.post("/api/oneleft/scan", json={"worker": "Nick"})
    bulk2 = [c for c in calls if c[1] == "/bulk-confirm"]
    check("auto pass skips the pinned check",
          all("FRESH-TAG" not in c[2]["skus"] for c in bulk2), str(bulk2))
    # New evidence (a sweep hearing its tag NOW) unpins it. Backdate the
    # requeue receipt first: CURRENT_TIMESTAMP is second-granular, and
    # "same second" must stay pinned, so give the sweep a clear lead.
    from app.models import OneLeftCheck as OLC
    with S(get_engine()) as s:
        s.execute(update(OLC).where(OLC.action == "requeue")
                  .values(created_at=NOW - timedelta(seconds=90)))
        s.commit()
    r = cl.post("/api/epc-captures", json={"epcs": ["A"*24],
                                           "device": "C72-test"})
    v3 = {i["sku"]: i["verdict"]
          for i in cl.get("/api/oneleft/board").json()["items"]}
    check("new evidence after the re-queue unpins it",
          v3.get("FRESH-TAG") == "confirmable", str(v3))

    # ---- pause switch blocks autos, scan endpoint says so ------------
    r = cl.post("/api/oneleft/auto", json={"on": False, "worker": "Nick"})
    check("auto toggle saves", r.status_code == 200
          and r.json()["auto"] is False, r.text)
    r = cl.post("/api/oneleft/scan", json={"worker": "Nick"})
    check("scan refuses while paused",
          r.json().get("ran") is False
          and r.json().get("reason") == "auto-off", r.text)
    cl.post("/api/oneleft/auto", json={"on": True, "worker": "Nick"})
    r = cl.post("/api/oneleft/scan", json={"worker": "Nick"})
    check("manual scan runs when resumed", r.json().get("ran") is True,
          r.text)

    # ---- fail soft ---------------------------------------------------
    with patch("app.oneleft._get",
               side_effect=RuntimeError("dashboard down")):
        oneleft.invalidate_pending_cache()
        r = cl.get("/api/oneleft/board")
        check("dashboard outage answers 200 with error",
              r.status_code == 200 and not r.json()["ok"]
              and "dashboard down" in (r.json().get("error") or ""), r.text)
        res = oneleft.scan_and_confirm("test", None)
        check("auto pass degrades on outage", res["ran"] is False, str(res))
    oneleft.invalidate_pending_cache()

print()
sys.exit(1 if fails else 0)
