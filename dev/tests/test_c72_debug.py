"""C72 live tuning + telemetry: the gun polls /api/c72/tuning (~2 s) and
applies parameter changes without an APK build; it streams locate ticks
to /api/c72/debug-log (pruned ring) so field tuning is a conversation.
Diagnostic plumbing — deliberately NOT in History.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_c72dbg_test.db")
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
    # Empty on first ask — the gun applies its built-in defaults.
    r = cl.get("/api/c72/tuning")
    check("tuning starts empty", r.status_code == 200
          and r.json()["values"] == {}, r.text)

    # Set + merge.
    r = cl.post("/api/c72/tuning", json={
        "values": {"fresh_ms": 2500, "fade": 0.85}, "worker": "Claude"})
    check("set values", r.json()["values"] == {"fresh_ms": 2500,
          "fade": 0.85}, r.text)
    r = cl.post("/api/c72/tuning", json={"values": {"debug": True}})
    check("merge keeps earlier keys",
          r.json()["values"]["fresh_ms"] == 2500
          and r.json()["values"]["debug"] is True, r.text)

    # null deletes a key -> gun falls back to its default.
    r = cl.post("/api/c72/tuning", json={"values": {"fade": None}})
    check("null deletes a key", "fade" not in r.json()["values"], r.text)

    # Replace mode drops everything not sent.
    r = cl.post("/api/c72/tuning", json={
        "values": {"blend": 0.4}, "merge": False})
    check("replace mode", r.json()["values"] == {"blend": 0.4}, r.text)
    r = cl.get("/api/c72/tuning")
    check("read-back matches + updated_by kept",
          r.json()["values"] == {"blend": 0.4}, r.text)

    # Telemetry: post, read newest-first.
    r = cl.post("/api/c72/debug-log", json={
        "device": "C72-nick",
        "lines": ["tick reads=2 best=-52 ema=51.0 pct=51",
                  "tick reads=0 best=- ema=51.0 pct=51"]})
    check("log accepts a batch", r.status_code == 201
          and r.json()["stored"] == 2, r.text)
    r = cl.get("/api/c72/debug-log?limit=10")
    lines = r.json()["lines"]
    check("newest first with device",
          len(lines) == 2 and lines[0]["line"].endswith("pct=51")
          and lines[0]["device"] == "C72-nick", str(lines))

    # Ring prune: push past 2000 and confirm the cap.
    for _ in range(21):
        cl.post("/api/c72/debug-log", json={
            "device": "x", "lines": [f"l{i}" for i in range(100)]})
    r = cl.get("/api/c72/debug-log?limit=1000")
    check("ring pruned to <= 2000 (limit answers 1000)",
          len(r.json()["lines"]) == 1000, str(len(r.json()["lines"])))
    from sqlalchemy.orm import Session as S
    from sqlalchemy import select, func as F
    from app.database import get_engine
    from app.models import C72DebugEvent
    with S(get_engine()) as s:
        n = s.scalar(select(F.count()).select_from(C72DebugEvent))
    check("table capped near 2000", n <= 2000, str(n))

    # Command channel: create -> pending -> ack -> gone from pending.
    r = cl.post("/api/c72/commands", json={
        "command": "get_state", "worker": "Claude"})
    check("command created", r.status_code == 201 and r.json()["id"] > 0,
          r.text)
    cid = r.json()["id"]
    cl.post("/api/c72/commands", json={
        "command": "set_pref", "arg": "auto_floor=7"})
    r = cl.get("/api/c72/commands/pending")
    cmds = r.json()["commands"]
    check("both pending, oldest first", len(cmds) == 2
          and cmds[0]["id"] == cid
          and cmds[1]["arg"] == "auto_floor=7", str(cmds))
    r = cl.post(f"/api/c72/commands/{cid}/done", json={
        "result": "tab=4 power=8", "device": "C72-nick"})
    check("ack ok", r.status_code == 200, r.text)
    r = cl.get("/api/c72/commands/pending")
    check("acked command left the queue",
          [c["id"] for c in r.json()["commands"]] != [] and
          all(c["id"] != cid for c in r.json()["commands"]), r.text)
    r = cl.get("/api/c72/commands?limit=5")
    hist = r.json()["commands"]
    done = [c for c in hist if c["id"] == cid][0]
    check("history keeps the result", done["done"] is True
          and done["result"] == "tab=4 power=8"
          and done["done_by"] == "C72-nick", str(done))
    r = cl.post("/api/c72/commands/99999/done", json={})
    check("ack of unknown command -> 404", r.status_code == 404, r.text)

print()
sys.exit(1 if fails else 0)
