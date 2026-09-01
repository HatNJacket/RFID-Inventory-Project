"""Printer wedge watchdog + purge (Nick, 2026-09-01): the ZD220 can
stop taking data while Windows reports no error - the server marks jobs
done at hand-off, so only the agent's view of the WINDOWS queue can
tell. v4 agents report queue depth + oldest-job age on every command
poll; the status endpoint turns that into a wedged flag, and a "purge"
command tells the agent to empty the Windows queue."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_printerwedge_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
import app.main as main
from app.main import app
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

with patch("app.main._maybe_refresh_bin_map", return_value=False):
  with TestClient(app) as cl:
    # Baseline: no agent has reported - nothing wedged, purge not
    # offered, old fields untouched.
    r = cl.get("/api/print-agent/status")
    b = r.json()
    check("baseline status: no wedge data yet",
          b["wedged"] is False and b["win_jobs"] is None
          and b["purge_capable"] is False
          and "online" in b and "realign_capable" in b, r.text[:200])

    # A v4 agent polls with a HEALTHY queue (1 fresh job).
    r = cl.post("/api/printer-commands/claim?printer=warehouse-zebra"
                "&agent_version=4&win_jobs=1&win_oldest_s=4")
    check("v4 poll accepted", r.status_code == 200, r.text[:150])
    b = cl.get("/api/print-agent/status").json()
    check("fresh short queue is NOT wedged",
          b["wedged"] is False and b["win_jobs"] == 1
          and b["win_oldest_seconds"] == 4
          and b["purge_capable"] is True, str(b)[:250])

    # The queue's head has sat for 20 minutes: wedged.
    cl.post("/api/printer-commands/claim?printer=warehouse-zebra"
            "&agent_version=4&win_jobs=16&win_oldest_s=1200")
    b = cl.get("/api/print-agent/status").json()
    check("old stuck head flags wedged",
          b["wedged"] is True and b["win_jobs"] == 16
          and b["win_oldest_seconds"] == 1200, str(b)[:250])

    # Empty queue clears the flag.
    cl.post("/api/printer-commands/claim?printer=warehouse-zebra"
            "&agent_version=4&win_jobs=0&win_oldest_s=0")
    b = cl.get("/api/print-agent/status").json()
    check("empty queue is healthy again",
          b["wedged"] is False and b["win_jobs"] == 0, str(b)[:200])

    # Purge command round-trip: queue from the terminal, agent claims it.
    r = cl.post("/api/printer-commands",
                json={"printer": "warehouse-zebra", "kind": "purge",
                      "requested_by": "Nick"})
    check("purge command queues", r.status_code == 201
          and r.json()["queued"] == "purge", r.text[:150])
    r = cl.post("/api/printer-commands/claim?printer=warehouse-zebra"
                "&agent_version=4&win_jobs=0&win_oldest_s=0")
    cmds = r.json()["commands"]
    check("agent claims the purge",
          len(cmds) == 1 and cmds[0]["kind"] == "purge"
          and cmds[0]["requested_by"] == "Nick", r.text[:200])
    r = cl.post("/api/printer-commands/claim?printer=warehouse-zebra"
                "&agent_version=4&win_jobs=0&win_oldest_s=0")
    check("commands clear on claim", r.json()["count"] == 0, r.text[:150])

    # feed still valid; junk kinds refused.
    r = cl.post("/api/printer-commands",
                json={"printer": "warehouse-zebra", "kind": "feed"})
    check("feed still accepted", r.status_code == 201, r.text[:120])
    r = cl.post("/api/printer-commands",
                json={"printer": "warehouse-zebra", "kind": "eject"})
    check("unknown kinds refused", r.status_code == 422, r.status_code)

    # A v3 agent (no win stats): status must not claim purge capability.
    main._printer_win_queue.clear()
    main._agent_versions.clear()
    cl.post("/api/printer-commands/claim?printer=warehouse-zebra"
            "&agent_version=3")
    b = cl.get("/api/print-agent/status").json()
    check("a v3 agent never offers purge",
          b["purge_capable"] is False and b["wedged"] is False,
          str(b)[:200])

print()
print(f"{'FAIL' if fails else 'OK'}  {len(fails)} failing")
sys.exit(1 if fails else 0)
