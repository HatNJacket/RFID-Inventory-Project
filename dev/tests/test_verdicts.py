"""The audit verdict ladder (2026-10-06, spec in ROADMAP): the rack
model's numbers (last count, sold since, received since, Expected now),
every ladder row's copy and action, the roll-up, and the heard-context
stamp the "heard elsewhere" row reads.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_verdicts_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
import json
from datetime import datetime, timezone
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
import app.main as M
import app.verdicts as V
from app.main import app
from app.database import get_engine
from app.models import (BinMapEntry, BinAudit, RfidAssignment, RetiredTag,
                        SoldRecord, Batch, BatchItem, OrderReceipt,
                        RfidIncompatible)
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)
def utc(m, d, h=12): return datetime(2026, m, d, h, 0, tzinfo=timezone.utc)
def tag(s, epc, sku, bin_, heard=None, ctx=None, units=None):
    s.add(RfidAssignment(rfid_id=epc, shopify_variant_id="t:1", product_title=sku,
          sku=sku, bin_location=bin_, assigned_at=utc(8, 10),
          last_heard_at=heard, last_heard_ctx=ctx, case_units=units))

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]):
  M._maybe_refresh_bin_map = lambda *a, **k: False
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        # F9168A on I1-2: Shopify on-hand 3 of which 1 unavailable (snapshot
        # qty = on-hand minus unavailable = 2). Logged Sep 1 at 4.
        s.add(BinMapEntry(sku="F9168A", bin="I1-2", product_title="ASI533", qty=2,
                          unavailable=1, shopify_variant_id="t:1"))
        s.add(BinAudit(bin="I1-2", audited_at=utc(9, 1), audited_by="Nick",
                       baseline=json.dumps({"F9168A": 4})))
        for i, epc in enumerate(["E1A41F2C", "E27C21E0", "E3B0D4F1", "E4AAAAAA"]):
            tag(s, epc, "F9168A", "I1-2", heard=utc(9, 30), ctx="I1-2 audit")
        s.add(RetiredTag(rfid_id="R0A41F2C", sku="F9168A", product_title="ASI533",
                         kind="sold", retired_at=utc(9, 28)))
        s.add(SoldRecord(order_id="s:1", order_name="50527", sku="F9168A",
                         quantity=1, retired=0, fulfilled_at=utc(10, 1),
                         source="shipstation"))
        # F9172D: logged at 1, 1 tag, Shopify 1.
        s.add(BinMapEntry(sku="F9172D", bin="I1-2", product_title="Drawer", qty=1,
                          unavailable=0, shopify_variant_id="t:2"))
        tag(s, "D1000001", "F9172D", "I1-2", heard=utc(9, 30), ctx="I1-2 audit")
        # 94214: incompatible, Shopify 6.
        s.add(BinMapEntry(sku="94214", bin="I1-2", product_title="Dovetail", qty=6,
                          unavailable=0, shopify_variant_id="t:3"))
        s.add(RfidIncompatible(sku="94214"))
        s.commit()
        a = s.scalar(select(BinAudit))
        a.baseline = json.dumps({"F9168A": 4, "F9172D": 1}); s.commit()

    model = cl.get("/api/audit/model/I1-2").json()
    by = {p["sku"]: p for p in model["products"]}
    f = by["F9168A"]
    check("the model carries the last count with its date",
          f["last_count"]["qty"] == 4 and f["last_count"]["at"].startswith("2026-09-01"), f["last_count"])
    check("  ...sold since the last count", [x["order"] for x in f["sold_since"]] == ["50527"], f["sold_since"])
    check("  ...Shopify on-hand INCLUDING the unavailable unit",
          f["shopify"]["on_hand"] == 3 and f["shopify"]["unavailable"] == 1, f["shopify"])
    check("  ...tags with their last-heard context",
          len(f["tags"]) == 4 and all(t["last_heard_ctx"] == "I1-2 audit" for t in f["tags"]))

    # Nick's example: 2 live tags + the retired one answer.
    heard = {"E3B0D4F1", "E4AAAAAA", "R0A41F2C", "D1000001"}
    r = cl.post("/api/audit/verdicts/I1-2", json={"epcs": sorted(heard)}).json()
    v = {i["sku"]: i for i in r["items"]}
    x = v["F9168A"]
    kinds = [p["kind"] for p in x["problems"]]
    check("F9168A: Expected now 3 (4 - 1 sold), heard 2",
          x["expected"] == 3 and x["heard"] == 2, (x["expected"], x["heard"]))
    check("  ...ladder: ghost, shipped, unavailable; no Shopify row (settled 3 = on-hand 3)",
          kinds == ["ghost", "shipped", "unavailable"], kinds)
    check("  ...summary line", x["summary"] == "3 to resolve: retired tag answered \u00b7 1 shipped \u00b7 1 unavailable", x["summary"])
    check("  ...colour red (worst wins)", x["colour"] == "red")
    g, sh, un = x["problems"]
    check("  ...ghost copy", g["text"] == "\u2026A41F2C was retired as sold on Sep 28, but it answered this sweep. The box never left."
          and g["action"] == "Un-retire", g["text"])
    check("  ...shipped copy pairs the oldest silent tag with the order (ties in list order)",
          sh["text"] == "\u2026A41F2C was last heard Sep 30 during the I1-2 audit. Order #50527 shipped on Oct 1."
          and sh["action"] == "Mark sold" and sh["confirm"] is False, sh["text"])
    check("  ...unavailable copy and action",
          un["text"].startswith("\u20267C21E0 was last heard Sep 30 during the I1-2 audit. Shopify holds 1 unit as unavailable.")
          and un["action"] == "Clear unavailable", un["text"])
    check("  ...resolve all lists the actions in order",
          x["resolve_all"] == ["Un-retire", "Mark sold", "Clear unavailable"], x["resolve_all"])
    check("F9172D matches (expected 1, heard 1, Shopify 1)",
          v["F9172D"]["colour"] == "green" and v["F9172D"]["summary"] == "Match", v["F9172D"])
    check("the incompatible product is a hand count",
          v["94214"]["mode"] == "hand" and v["94214"]["problems"][0]["action"] == "Confirm count"
          and "6 on hand" in v["94214"]["problems"][0]["text"], v["94214"])
    check("roll-up: 3 products, 1 match, 1 to resolve, 1 by hand",
          (r["count"], r["all_match"], r["to_resolve"], r["by_hand"]) == (3, 1, 1, 1), r)

    # The pure ladder, case by case.
    base = {"sku": "X", "bins": ["i1-2"], "last_count": {"qty": 2, "at": "2026-09-01T12:00:00+00:00"},
            "sold_since": [], "received_since": [], "retired": [],
            "shopify": {"on_hand": 2, "unavailable": 0}, "flags": {},
            "tags": [{"epc": "T1", "bin": "I1-2", "here": True, "units": 1,
                      "last_heard_at": "2026-09-30T12:00:00+00:00", "last_heard_ctx": "I1-2 audit"},
                     {"epc": "T2", "bin": "I1-2", "here": True, "units": 1,
                      "last_heard_at": "2026-10-05T10:40:00+00:00", "last_heard_ctx": "packing scan"}]}
    j = V.judge(base, {"T1"})
    check("heard elsewhere: a silent tag last heard during the packing scan",
          [p["kind"] for p in j["problems"]] == ["elsewhere", "shopify"]
          and j["problems"][0]["text"] == "\u2026T2 was last heard Oct 5 during the packing scan."
          and j["problems"][0]["action"] == "Locate", j["problems"])
    check("  ...and Shopify's disagreement is evaluated last, against the settled shelf",
          j["problems"][1]["fragment"] == "shelf 1, Shopify 2" and j["problems"][1]["action"] == "Set on-hand to 1", j["problems"][1])
    j = V.judge(base, set())
    kinds = [p["kind"] for p in j["problems"]]
    check("nothing heard: one elsewhere, one missing, then Shopify",
          kinds == ["elsewhere", "missing", "shopify"], kinds)
    m = j["problems"][1]
    check("  ...missing copy",
          m["title"] == "1 tag missing since Sep 30" and m["action"] == "Count and set"
          and m["text"] == "\u2026T1 was last heard Sep 30 during the I1-2 audit. No sale, no receipt, not heard anywhere since.", m)
    over = dict(base); over["last_count"] = {"qty": 1, "at": "2026-09-01T12:00:00+00:00"}
    j = V.judge(over, {"T1", "T2"})
    check("over: heard 2, expected 1",
          j["problems"][0]["kind"] == "over" and j["problems"][0]["fragment"] == "heard 2, expected 1"
          and j["problems"][0]["action"] == "Count and set", j["problems"])
    rec = dict(base); rec["received_since"] = [{"stock_order": 1003, "when": "2026-10-03T12:00:00+00:00", "units": 2, "unpaired": 2}]
    rec["tags"] = [base["tags"][0]]
    j = V.judge(rec, set())
    check("received, not shelved: expected 4 (2 + 2 received), the receipt explains 2, the tag is missing",
          j["expected"] == 4 and [p["kind"] for p in j["problems"]][:2] == ["received", "missing"]
          and j["problems"][0]["text"] == "Stock order #1003 received Oct 3: 2 units, 2 with no label paired yet."
          and j["problems"][0]["action"] == "Print 2 labels", j["problems"])
    # Oversold shows when available is known and negative.
    ov = dict(base); ov["shopify"] = {"on_hand": 0, "unavailable": 1, "available": -1}
    j = V.judge(ov, {"T1", "T2"})
    kinds = [p["kind"] for p in j["problems"]]
    check("oversold: Shopify row then the oversold note", kinds == ["shopify", "oversold"], kinds)
    # Sales only explain a silence that came AFTER the tag was last heard.
    late = dict(base); late["sold_since"] = [{"order": "1", "when": "2026-09-20T12:00:00+00:00", "units": 1}]
    late["tags"] = [base["tags"][0]]
    j = V.judge(late, set())
    check("a sale BEFORE the tag was last heard does not explain it",
          [p["kind"] for p in j["problems"]][0] == "missing", j["problems"])

    # The stamp: an audit check records what heard the tag and where.
    r = cl.post("/api/bins/I1-2/check", json={"epcs": ["D1000001"]})
    with Session(get_engine()) as s:
        t = s.scalar(select(RfidAssignment).where(RfidAssignment.rfid_id == "D1000001"))
    check("a bin check stamps 'I1-2 audit' as the heard context", t.last_heard_ctx == "I1-2 audit", t.last_heard_ctx)

print()
if fails:
    print(f"{len(fails)} FAILED"); sys.exit(1)
print("ALL PASS")
