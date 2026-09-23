"""Print agent v6 (2026-09-23): cloud control plane + truthful dones.

Server side: /api/print-agent/heartbeat stores the printer's own story
(fault, holding, transport, readback) and hands back queued commands
(now with ids + payloads) plus the served agent version - the
self-update signal. Command output round-trips through
/api/print-agent/command-result. The script download accepts the AGENT
key so the agent can update itself. /api/print-agent/bootstrap serves
the one-time upgrade script with no auth and no secrets.

Agent side: ~HS parsing, fault gating (under-temp never holds), and the
confirm loop that believes the printer's odometer over the spooler -
'vanished' labels are detected instead of falsely done."""
import os, sys, tempfile, types
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_agentv6_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
import app.main as main
import app.config as config
from app.main import app
import print_agent
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

# ---------------------------------------------------------- server side ----
with patch("app.main._maybe_refresh_bin_map", return_value=False):
  with TestClient(app) as cl:
    latest = main._agent_latest_version()
    check("server parses its own agent version",
          latest == print_agent.AGENT_VERSION, latest)

    # v6+ heartbeat: stores state, stamps the v4/v5 books too.
    r = cl.post("/api/print-agent/heartbeat", json={
        "printer": "warehouse-zebra", "version": print_agent.AGENT_VERSION,
        "transport": "usb-direct (vid_0a5f&pid_0164)",
        "readback": "counter", "fault": None, "holding": 0,
        "status": {"formats": 0, "paused": False},
        "counters": {"done": 3, "failed": 0},
    })
    check("heartbeat accepted", r.status_code == 200
          and r.json()["latest_version"] == latest, r.text[:200])
    b = cl.get("/api/print-agent/status").json()
    check("status surfaces the heartbeat",
          b["transport"].startswith("usb-direct")
          and b["readback"] == "counter" and b["fault"] is None
          and b["agent_counters"]["done"] == 3
          and b["latest_version"] == latest
          and b["update_available"] is False, str(b)[:300])
    check("heartbeat counts as a command poll (realign capable)",
          b["realign_capable"] is True, str(b)[:200])

    # A FAULT held by the printer itself shows up verbatim.
    cl.post("/api/print-agent/heartbeat", json={
        "printer": "warehouse-zebra", "version": "6",
        "fault": "media out", "holding": 4,
    })
    b = cl.get("/api/print-agent/status").json()
    check("printer fault + held labels surface",
          b["fault"] == "media out" and b["holding"] == 4, str(b)[:200])

    # An older agent version flags update_available.
    cl.post("/api/print-agent/heartbeat", json={
        "printer": "warehouse-zebra", "version": "5",
    })
    b = cl.get("/api/print-agent/status").json()
    check("older agent flags update_available",
          b["update_available"] is True, str(b)[:200])

    # Extended commands queue with ids + payloads and ride the heartbeat.
    r = cl.post("/api/printer-commands", json={
        "printer": "warehouse-zebra", "kind": "shell",
        "payload": {"cmd": "Get-Date"}, "requested_by": "Nick"})
    cmd_id = r.json().get("id")
    check("shell command queues with an id",
          r.status_code == 201 and bool(cmd_id), r.text[:200])
    r = cl.post("/api/print-agent/heartbeat", json={
        "printer": "warehouse-zebra", "version": "6"})
    cmds = r.json()["commands"]
    check("heartbeat delivers the command with payload",
          len(cmds) == 1 and cmds[0]["id"] == cmd_id
          and cmds[0]["kind"] == "shell"
          and cmds[0]["payload"]["cmd"] == "Get-Date", r.text[:250])
    r = cl.post("/api/print-agent/heartbeat", json={
        "printer": "warehouse-zebra", "version": "6"})
    check("commands clear on delivery", r.json()["count"] == 0, r.text[:150])
    for kind in ("update", "restart", "getlog", "query", "zpl", "testlabel"):
        r = cl.post("/api/printer-commands",
                    json={"printer": "warehouse-zebra", "kind": kind})
        check(f"kind '{kind}' accepted", r.status_code == 201, r.text[:120])
    cl.post("/api/print-agent/heartbeat",
            json={"printer": "warehouse-zebra", "version": "6"})  # drain
    r = cl.post("/api/printer-commands",
                json={"printer": "warehouse-zebra", "kind": "eject"})
    check("junk kinds still refused", r.status_code == 422, r.status_code)

    # Command result round trip.
    b = cl.get(f"/api/print-agent/command-result/{cmd_id}").json()
    check("result pending before the agent answers",
          b["pending"] is True, str(b)[:150])
    r = cl.post("/api/print-agent/command-result",
                json={"id": cmd_id, "ok": True, "output": "exit 0\nhello"})
    check("agent posts the result", r.status_code == 200, r.text[:150])
    b = cl.get(f"/api/print-agent/command-result/{cmd_id}").json()
    check("operator reads the result",
          b["pending"] is False and b["ok"] is True
          and "hello" in b["output"], str(b)[:200])

    # Script download honors the AGENT key once keys are enforced.
    config.STATION_KEY = "sk-test"
    config.PRINT_AGENT_KEY = "ak-test"
    try:
        r = cl.get("/api/print-agent/script")
        check("script refused with no key at all", r.status_code == 401,
              r.status_code)
        r = cl.get("/api/print-agent/script",
                   headers={"X-Agent-Key": "ak-test"})
        check("script served for the agent key", r.status_code == 200
              and b"AGENT_VERSION" in r.content, r.status_code)
        r = cl.get("/api/print-agent/script?key=sk-test")
        check("script still served for the station key",
              r.status_code == 200, r.status_code)
        r = cl.get("/api/print-agent/script",
                   headers={"X-Agent-Key": "wrong"})
        check("wrong agent key falls through to 401",
              r.status_code == 401, r.status_code)
        r = cl.post("/api/print-agent/heartbeat", json={"version": "6"})
        check("heartbeat refused without the agent key",
              r.status_code == 401, r.status_code)
        # Bootstrap stays open: it is fetched from a mangled remote
        # keyboard and carries no secrets.
        r = cl.get("/api/print-agent/bootstrap")
        check("bootstrap serves with no auth", r.status_code == 200
              and "v6 bootstrap" in r.text
              and "X-Agent-Key" in r.text, r.status_code)
        check("bootstrap points at https", 'https://testserver' in r.text,
              r.text[:120])
        check("bootstrap holds no secrets",
              "sk-test" not in r.text and "ak-test" not in r.text, "leak")
    finally:
        config.STATION_KEY = None
        config.PRINT_AGENT_KEY = None

# ----------------------------------------------------------- agent side ----
HS_OK = (b"\x02030,0,0,1245,000,0,0,0,000,0,0,0\x03\r\n"
         b"\x02001,0,0,0,0,2,6,0,00000000,1,000\x03\r\n"
         b"\x021234,0\x03\r\n")
HS_BUSY = (b"\x02030,0,0,1245,002,0,0,0,000,0,0,0\x03\r\n"
           b"\x02001,0,0,0,0,2,6,0,00000000,1,000\x03\r\n")
HS_PAPER_OUT = (b"\x02030,1,1,1245,001,0,0,0,000,0,0,0\x03\r\n"
                b"\x02001,0,1,0,0,2,6,0,00000000,1,000\x03\r\n")
HS_COLD = (b"\x02030,0,0,1245,000,0,0,0,000,0,1,0\x03\r\n"
           b"\x02001,0,0,1,0,2,6,0,00000000,1,000\x03\r\n")

st = print_agent.parse_hs(HS_OK)
check("~HS healthy parse", st is not None and st["formats"] == 0
      and not st["paper_out"] and not st["paused"]
      and st["head_open"] is False, str(st))
st = print_agent.parse_hs(HS_BUSY)
check("~HS busy parse sees 2 buffered formats", st["formats"] == 2, str(st))
st = print_agent.parse_hs(HS_PAPER_OUT)
f = print_agent.active_faults(st)
check("paper out + pause + head open all fault",
      "media out" in f and "paused" in f and "head open" in f, str(f))
st = print_agent.parse_hs(HS_COLD)
check("under-temp and direct-thermal ribbon flag never hold the queue",
      print_agent.active_faults(st) == [], str(st))
check("garbage parses to None",
      print_agent.parse_hs(b"") is None
      and print_agent.parse_hs(b"nonsense") is None, "")

class FakeTr:
    def __init__(self, statuses, counts):
        self.statuses = list(statuses); self.counts = list(counts)
        self.readback = "counter" if counts else "status"
        self.supports_count = bool(counts)
        self.sent = []
    def _next(self, seq):
        return seq.pop(0) if len(seq) > 1 else seq[0]
    def status(self): return print_agent.parse_hs(self._next(self.statuses))
    def label_count(self): return self._next(self.counts)
    def send(self, zpl): self.sent.append(zpl)
    def query(self, cmd, first_timeout_ms=0): return b""
    def describe(self): return "fake"

class FakeClient:
    printer_id = None
    def __init__(self): self.completed=[]; self.failed=[]; self.results=[]
    def heartbeat(self, payload): return {"commands": [], "latest_version": None}
    def complete(self, job_id, create_assignment): self.completed.append(job_id)
    def fail(self, job_id, why): self.failed.append((job_id, why))
    def post_result(self, cid, ok, output): self.results.append((cid, ok, output))
    # Track the CURRENT version dynamically: a hardcoded old version here
    # once made _self_update run FOR REAL and clobber the repo's
    # print_agent.py with this one-liner (2026-09-23). Never again.
    def download_script(self):
        return f'AGENT_VERSION = "{print_agent.AGENT_VERSION}"\n'

def mk_agent(tr):
    args = print_agent.build_parser().parse_args(
        ["--app", "http://x", "--dry-run", "--no-auto-update"])
    return print_agent.Agent(args, FakeClient(), tr)

print_agent.LABEL_SETTLE_TIMEOUT = 6  # keep the tests quick

def mk_job(n):
    return {"id": n, "epc": f"A{n:023d}", "sku": f"SKU-{n}",
            "product_title": "T", "shopify_variant_id": "v1"}

# v7 burst: one continuous run, every label confirmed IN ORDER from the
# odometer. statuses: ready-check, busy, then drained; counts: base 100,
# then the polls watch it climb to 102.
ag = mk_agent(FakeTr([HS_OK, HS_BUSY, HS_OK], [100, 101, 102]))
ag._print_burst([mk_job(1), mk_job(2)])
check("burst: both labels odometer-confirmed in order",
      ag.client.completed == [1, 2] and not ag.client.failed
      and ag.counters["done"] == 2 and ag.counters["vanished"] == 0,
      str(ag.client.__dict__)[:250])
check("burst sent both formats down the wire",
      len(ag.tr.sent) == 2, len(ag.tr.sent))

# Eaten tail: buffer drains but the odometer stops at base+1 forever ->
# label 1 confirmed, label 2 retried as its own burst 3x, then failed.
ag = mk_agent(FakeTr([HS_OK], [100, 101]))
ag._print_burst([mk_job(1), mk_job(2)])
check("burst: eaten tail confirmed-head kept, tail failed loudly",
      ag.client.completed == [1] and len(ag.client.failed) == 1
      and ag.client.failed[0][0] == 2
      and "swallowed" in ag.client.failed[0][1],
      str(ag.client.failed)[:250])
check("burst: the eaten label was re-sent before failing (3 tries)",
      len(ag.tr.sent) == 4 and ag.counters["retried"] == 2,
      str((len(ag.tr.sent), ag.counters))[:200])

# No odometer on this printer: drained is the best truth available.
ag = mk_agent(FakeTr([HS_OK, HS_BUSY, HS_OK], []))
ag._print_burst([mk_job(1), mk_job(2)])
check("burst without a counter -> both drained-done",
      ag.client.completed == [1, 2] and not ag.client.failed,
      str(ag.client.__dict__)[:200])

# Fault mid-burst holds (rest stays buffered), then recovers + confirms.
ag = mk_agent(FakeTr([HS_OK, HS_PAPER_OUT, HS_OK], [100, 101, 102]))
ag._print_burst([mk_job(1), mk_job(2)])
check("fault mid-burst recovers to confirmed",
      ag.client.completed == [1, 2] and not ag.client.failed
      and ag.fault is None, str((ag.fault, ag.client.failed))[:200])

# Stalled: a format sits in the buffer with no fault flag and no
# progress -> the deadline runs out (it must NOT extend forever on
# formats>0 alone) and the unconfirmed jobs fail loudly.
ag = mk_agent(FakeTr([HS_OK, HS_BUSY], [100, 100]))
ag._print_burst([mk_job(1)])
check("burst: no progress + no fault -> stalled, job failed",
      not ag.client.completed and len(ag.client.failed) == 1
      and "stalled" in ag.client.failed[0][1],
      str(ag.client.failed)[:200])

# Command handling posts results under the command id.
ag = mk_agent(FakeTr([HS_OK], [100, 101]))
ag._handle_command({"id": "c1", "kind": "getlog", "payload": {"lines": 5}})
check("getlog posts a result", ag.client.results
      and ag.client.results[0][0] == "c1", str(ag.client.results)[:150])
ag._handle_command({"id": "c2", "kind": "nonsense"})
check("unknown kind reports not-ok",
      ag.client.results[-1][0] == "c2" and ag.client.results[-1][1] is False,
      str(ag.client.results)[:150])

# Self-update: same version served -> no exit, no swap. os.replace is
# stubbed for the duration as a belt: whatever a future regression does
# here, the real print_agent.py must never be touched by a test.
ag = mk_agent(FakeTr([HS_OK], [100, 101]))
_real_replace = print_agent.os.replace
print_agent.os.replace = lambda a, b: (_ for _ in ()).throw(
    AssertionError("self-update tried to swap the real file"))
try:
    ag._self_update(force=False)
    check("self-update no-ops on same version", True)
except (SystemExit, AssertionError) as e:
    check("self-update no-ops on same version", False, repr(e))
finally:
    print_agent.os.replace = _real_replace

print()
print(f"{'FAIL' if fails else 'OK'}  {len(fails)} failing")
sys.exit(1 if fails else 0)
