"""Printer registry + routing (the Scan Station printer picker).

Agents register their printer on every claim (detection, liveness). A named
agent claims only jobs aimed at it or at no printer; a LEGACY agent (no
printer param) registers as warehouse-zebra and claims everything - the
single-printer warehouse must keep printing with the old agent binary."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_printers_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from fastapi.testclient import TestClient
from app.main import app
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

P = {"shopify_variant_id":"t:1","shopify_product_id":"gid://p/1",
     "product_title":"Test Scope","sku":"TS-1","barcode":"111"}

with TestClient(app) as cl:
    # No agent has ever checked in.
    r = cl.get("/api/printers").json()
    check("registry starts empty", r["count"]==0, r)

    # Three jobs: untargeted, aimed at zebra-desk, aimed at zebra-back.
    cl.post("/api/print-jobs", json={**P, "quantity":1})
    cl.post("/api/print-jobs", json={**P, "quantity":1, "printer":"zebra-desk"})
    cl.post("/api/print-jobs", json={**P, "quantity":1, "printer":"zebra-back"})
    jobs = cl.get("/api/print-jobs?status=pending").json()["jobs"]
    check("printer stored on the job",
          sorted(j["printer"] or "-" for j in jobs)==["-","zebra-back","zebra-desk"],
          jobs)

    # Named agent: registers itself, claims its own + untargeted only.
    r = cl.post("/api/print-jobs/claim?printer=zebra-desk&kind=ZD621R+%C2%B7+RFID+encoder").json()
    got = sorted(j["printer"] or "-" for j in r["jobs"])
    check("named agent claims untargeted + its own", got==["-","zebra-desk"], r)
    check("named agent never claims another printer's job",
          all(j["printer"] in (None,"zebra-desk") for j in r["jobs"]), r)

    # Registration happened, with liveness and the kind descriptor.
    reg = cl.get("/api/printers").json()
    check("claim registered the printer", reg["count"]==1
          and reg["printers"][0]["name"]=="zebra-desk", reg)
    check("registered printer is online", reg["printers"][0]["online"] is True, reg)
    check("kind descriptor survives", "ZD621R" in (reg["printers"][0]["kind"] or ""), reg)

    # Legacy agent (no printer param): registers as the default name and
    # claims EVERYTHING still pending - including other printers' jobs.
    r = cl.post("/api/print-jobs/claim").json()
    check("legacy agent claims the leftover targeted job",
          [j["printer"] for j in r["jobs"]]==["zebra-back"], r)
    reg = cl.get("/api/printers").json()
    names = sorted(p["name"] for p in reg["printers"])
    check("legacy agent registered under the default name",
          names==["warehouse-zebra","zebra-desk"], names)

    # The picker's data: both rows carry last_seen + online.
    check("liveness fields present",
          all(p["last_seen"] and p["online"] is True for p in reg["printers"]),
          reg)

    # A job aimed at an unknown/offline printer just waits for it.
    cl.post("/api/print-jobs", json={**P, "quantity":1, "printer":"zebra-desk"})
    r = cl.post("/api/print-jobs/claim?printer=zebra-back").json()
    check("wrong-name agent leaves the job pending", r["count"]==0, r)
    r = cl.post("/api/print-jobs/claim?printer=zebra-desk").json()
    check("right-name agent picks it up", r["count"]==1
          and r["jobs"][0]["printer"]=="zebra-desk", r)

    # Azure SQL hands last_seen back tz-AWARE; the 45s throttle's naive
    # subtraction 500'd every claim after the first stamp (printer
    # "offline" while the agent ran fine - Nick, 2026-08-26). sqlite
    # reads are naive, so pin it against an aware value still sitting
    # in the session's identity map.
    from datetime import datetime, timezone
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from app.database import get_engine
    from app.models import Printer
    from app.main import _touch_printer
    with Session(get_engine()) as s:
        row = s.scalar(select(Printer).where(Printer.name=="zebra-desk"))
        row.last_seen = datetime.now(timezone.utc)  # aware, like prod
        s.flush()
        try:
            _touch_printer(s, "zebra-desk", None)
            ok, err = True, None
        except TypeError as e:
            ok, err = False, str(e)
        check("a tz-aware last_seen never crashes the claim throttle",
              ok, err)
        s.rollback()

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
