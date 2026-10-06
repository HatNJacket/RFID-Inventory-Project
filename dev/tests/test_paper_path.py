"""Paper labels (2026-10-06): the Munbyn paper printer's path end to end.

Server: a print request with stock="paper" becomes a kind="paper" job aimed
at the paper printer; RFID agents (legacy or named) never claim paper jobs;
the paper agent's EXCLUSIVE claim takes only paper jobs aimed at it (never
an untargeted RFID job, never an RFID job someone aimed at it); a printed
paper label never creates a tag record and never counts as owed pairing;
status reads per printer, with the paper printer's summary alongside.

Agent v11 (--paper): the label renders at 456 x 228 dots with the Zebra
label's wording rules; it is sent as ONE TSPL bitmap; a job completes only
when the printer answers ready again; faults, silence, over-long lines and
non-paper jobs fail the job instead of printing something wrong; Zebra
commands are refused (ZPL froze the Munbyn) and feed becomes FORMFEED."""
import os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"] = "t.myshopify.com"
os.environ["SHOPIFY_CLIENT_ID"] = "x"
os.environ["SHOPIFY_CLIENT_SECRET"] = "x"
os.environ["ORDERS_SYNC_DISABLE"] = "1"
os.environ.pop("STATION_KEY", None)
os.environ.pop("PRINT_AGENT_KEY", None)
os.environ.pop("PAPER_PRINTER", None)
db = os.path.join(tempfile.gettempdir(), "rfid_paper_path_test.db")
if os.path.exists(db):
    os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\", "/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select, func
import app.main as main
from app.main import app
from app.models import PrintJob, RfidAssignment
from app import database
import print_agent as pa

fails = []


def check(label, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + label + ("" if cond else f"  <- {extra}"))
    if not cond:
        fails.append(label)


def job_body(**kw):
    body = {"quantity": 1, "shopify_variant_id": "gid://v/1",
            "product_title": "Thumbscrews M6", "sku": "TS-M6",
            "barcode": "123456", "bin_location": "H2-1"}
    body.update(kw)
    return body


def claim(cl, **params):
    r = cl.post("/api/print-jobs/claim", params=params)
    assert r.status_code == 200, r.text
    return r.json()["jobs"]


# ------------------------------------------------------------ server side --
with patch("app.main._maybe_refresh_bin_map", return_value=False):
    with TestClient(app) as cl:
        r = cl.post("/api/print-jobs", json=job_body(stock="paper", quantity=2))
        paper = r.json()["jobs"] if r.status_code == 201 else []
        check("a paper request queues paper jobs aimed at the paper printer",
              len(paper) == 2 and all(j["kind"] == "paper"
                                      and j["printer"] == "warehouse-paper"
                                      for j in paper), r.text[:300])
        r = cl.post("/api/print-jobs",
                    json=job_body(stock="paper", printer="warehouse-zebra"))
        check("paper wins over a printer the browser had picked",
              r.json()["jobs"][0]["printer"] == "warehouse-paper", r.text[:200])
        rfid = cl.post("/api/print-jobs", json=job_body(sku="RF-1")).json()["jobs"][0]
        check("a normal request stays an RFID job",
              rfid["kind"] is None and rfid["printer"] is None, str(rfid))
        aimed = cl.post("/api/print-jobs",
                        json=job_body(sku="RF-2", printer="warehouse-paper")).json()["jobs"][0]

        got = claim(cl, limit=20, exclusive="true")
        check("an exclusive claim without a printer id takes nothing", got == [], str(got))

        got = claim(cl, limit=20)
        kinds = {j["kind"] for j in got}
        check("the legacy RFID agent never takes paper jobs",
              "paper" not in kinds and any(j["id"] == rfid["id"] for j in got),
              str([(j["id"], j["kind"]) for j in got]))
        check("the legacy agent still claims every RFID job (old behaviour)",
              any(j["id"] == aimed["id"] for j in got), str([j["id"] for j in got]))
        # Put the RFID job someone aimed at the paper printer back in the
        # queue, so the exclusive-claim check below can prove it skips it.
        gen = database.get_session()
        s = next(gen)
        try:
            s.get(PrintJob, aimed["id"]).status = "pending"
            s.commit()
        finally:
            gen.close()

        got = claim(cl, limit=20, printer="warehouse-zebra")
        check("a named RFID agent never takes paper jobs",
              all(j["kind"] != "paper" for j in got), str(got)[:200])

        got = claim(cl, limit=20, printer="warehouse-paper", exclusive="true")
        ids = {j["id"] for j in got}
        check("the paper agent takes exactly the paper jobs",
              ids == {j["id"] for j in paper} | {r.json()["jobs"][0]["id"]},
              f"{ids}")
        check("the paper agent never takes an RFID job aimed at it",
              aimed["id"] not in ids, str(ids))

        jid = paper[0]["id"]
        r = cl.post(f"/api/print-jobs/{jid}/complete",
                    params={"create_assignment": "true"})
        check("completing a paper label works", r.status_code == 200, r.text[:200])
        check("a paper label never becomes a tag record",
              r.json().get("assignment") is None, r.text[:200])
        gen = database.get_session()
        s = next(gen)
        try:
            n_assign = s.scalar(select(func.count()).select_from(RfidAssignment)
                                .where(RfidAssignment.rfid_id == paper[0]["epc"]))
            owed = s.scalar(select(func.count()).select_from(PrintJob)
                            .where(PrintJob.id == jid, main._NOT_COMPANION))
        finally:
            gen.close()
        check("no assignment row exists for the paper EPC", n_assign == 0, n_assign)
        check("paper labels never count as owed pairings", owed == 0, owed)

        # Status per printer: the paper agent's fault and claims stay its own.
        cl.post("/api/print-agent/heartbeat", json={
            "printer": "warehouse-zebra", "version": pa.AGENT_VERSION,
            "transport": "usb-direct (vid_0a5f&pid_0164)", "readback": "counter",
            "fault": None, "holding": 0})
        cl.post("/api/print-agent/heartbeat", json={
            "printer": "warehouse-paper", "version": pa.AGENT_VERSION,
            "transport": "usb-direct (vid_09c6&pid_0248)", "readback": "status",
            "fault": "out of paper", "holding": 0, "last_error": "x"})
        main._agent_seen_by.clear()
        claim(cl, printer="warehouse-paper", exclusive="true")
        st = cl.get("/api/print-agent/status").json()
        check("the RFID status ignores the paper agent's fault",
              st["fault"] is None and st["transport"].startswith("usb-direct (vid_0a5f"),
              str(st)[:300])
        check("the paper agent's claims don't make the RFID agent online",
              st["online"] is False, str(st)[:200])
        check("the paper printer's summary rides along",
              st["paper"]["online"] is True and st["paper"]["fault"] == "out of paper",
              str(st.get("paper")))
        claim(cl, limit=1)
        check("the legacy agent's own claim makes the RFID status online",
              cl.get("/api/print-agent/status").json()["online"] is True)
        pr = cl.get("/api/printers").json()["printers"]
        check("the printer list marks the paper printer",
              any(p["name"] == "warehouse-paper" and p["paper"] for p in pr)
              and any(p["name"] == "warehouse-zebra" and not p["paper"] for p in pr),
              str(pr))

        js = open(os.path.join(os.path.dirname(main.__file__), "static", "app.js"),
                  encoding="utf-8").read()
        check("the web printer picker leaves the paper printer out",
              "filter((p) => !p.paper)" in js)


# ------------------------------------------------------------- agent side --
lines = pa.paper_label_lines({"sku": "93581", "label_name": "Cool Name",
                              "label_placement": "sku", "case_units": 8,
                              "bin_location": "A1", "other_bins": "B2"})
check("paper wording follows the Zebra rules",
      lines == {"header": "Telescopes Canada", "centre": "8 x Cool Name",
                "barcode": "93581", "bin": "BIN: A1. Other: B2"}, str(lines))
img = pa.render_paper_label({"sku": "ZWO AMH", "barcode": "6977641321679",
                             "bin_location": "RSB"})
check("the label renders at 456 x 228 dots", img.size == (456, 228), img.size)
data = pa.paper_tspl(img)
check("it is one TSPL bitmap job",
      data.startswith(b"SIZE 2.25,1.125\r\nGAP 0.08,0\r\nCLS\r\nBITMAP 0,0,57,228,0,")
      and data.endswith(b"\r\nPRINT 1\r\n") and b"BOX" not in data, data[:80])
try:
    pa.render_paper_label({"sku": "X" * 90, "barcode": "1"})
    check("an over-long centre line is refused", False, "rendered")
except pa.PaperLabelError as e:
    check("an over-long centre line is refused", "too long" in str(e), str(e))
try:
    pa.code128b_widths("café")
    check("a barcode with non-ASCII characters is refused", False)
except pa.PaperLabelError:
    check("a barcode with non-ASCII characters is refused", True)


class FakeTr:
    readback = "status"

    def __init__(self, answers):
        self.answers = list(answers)
        self.writes = []

    def query(self, cmd, first_timeout_ms=1500):
        return self.answers.pop(0) if self.answers else b""

    def write(self, data, timeout_ms=10000):
        self.writes.append(data)

    def describe(self):
        return "fake"

    def reconnect(self):
        pass


class FakeClient:
    printer_id = "warehouse-paper"

    def __init__(self):
        self.done, self.failed, self.results = [], [], []

    def complete(self, jid, create_assignment):
        self.done.append((jid, create_assignment))

    def fail(self, jid, error):
        self.failed.append((jid, error))

    def post_result(self, cid, ok, out):
        self.results.append((cid, ok, out))

    def heartbeat(self, payload):
        return {}


class Args:
    no_rfid = True
    once = True
    poll = 0
    usb_device = "vid_09c6"


def agent(answers):
    return pa.PaperAgent(Args(), FakeClient(), FakeTr(answers))


JOB = {"id": 7, "kind": "paper", "sku": "TS-M6", "barcode": "123456",
       "bin_location": "H2-1"}

a = agent([b"\x00", b"\x00"])
a._print_one(dict(JOB))
check("a ready printer prints and completes without a tag record",
      a.client.done == [(7, False)] and len(a.tr.writes) == 1
      and a.tr.writes[0].startswith(b"SIZE"), f"{a.client.done} {len(a.tr.writes)}")

a = agent([b"\x00"])
a._print_one(dict(JOB, kind=None))
check("a non-paper job is refused, not printed",
      a.client.failed and not a.tr.writes and not a.client.done, a.client.failed)

a = agent([b"\x00"])
a._print_one(dict(JOB, sku="W" * 80))
check("an over-long label fails with a fix-it message, not printed",
      a.client.failed and "label editor" in a.client.failed[0][1] and not a.tr.writes,
      a.client.failed)

a = agent([b"\x04"])
a._print_one(dict(JOB))
check("out of paper before sending fails the job and holds the printer",
      a.client.failed and "out of paper" in a.client.failed[0][1]
      and a.fault == "out of paper" and not a.tr.writes, f"{a.client.failed} {a.fault}")

with patch.object(pa, "PAPER_READY_TIMEOUT", 0.3), patch.object(pa.time, "sleep", lambda s: None):
    a = agent([b"\x00"])
    a._print_one(dict(JOB))
check("silence after sending fails the job instead of claiming it printed",
      a.client.failed and not a.client.done and a.fault, f"{a.client.failed} {a.fault}")

a = agent([])
a._handle_command({"kind": "feed", "id": "c1"})
a._handle_command({"kind": "zpl", "id": "c2", "payload": {"zpl": "^XA^XZ"}})
check("feed becomes a TSPL FORMFEED", a.tr.writes == [b"FORMFEED\r\n"], a.tr.writes)
check("raw ZPL is refused on the paper printer",
      a.client.results[-1][1] is False and "Zebra" in a.client.results[-1][2],
      a.client.results)

src = open(pa.__file__, encoding="utf-8").read()
check("the agent version is bumped for the paper mode", pa.AGENT_VERSION == "11")
check("paper mode never sends the Zebra startup ZPL",
      "run_paper_agent(args)\n        return\n\n    transport = make_transport(args)" in src)

print()
print(f"{'ALL PASS' if not fails else str(len(fails)) + ' FAILED'}")
sys.exit(1 if fails else 0)
