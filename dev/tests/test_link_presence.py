"""LINK presence: the gun heartbeats through its tuning poll (and through
every LINK scan POST, which covers old APKs), each web terminal stamps a
per-page-load tid through its scan poll, and the toggle-ON seat call
returns who else is listening so the terminal can warn before double-
printing. All in-memory — no schema.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_link_presence_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from fastapi.testclient import TestClient
import app.main as M
from app.main import app
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

with TestClient(app) as cl:
    # --- gun presence via the tuning poll (new APKs) ----------------------
    r = cl.get("/api/c72/tuning")
    check("tuning poll without params still answers", r.status_code == 200,
          r.text)
    st = cl.get("/api/link/status").json()
    check("no params -> no gun registered", st["guns"] == [], st)

    r = cl.get("/api/c72/tuning", params={"device": "C72", "tab": "sweep"})
    check("tuning poll with device+tab answers", r.status_code == 200, r.text)
    st = cl.get("/api/link/status").json()
    check("gun appears with its tab",
          len(st["guns"]) == 1 and st["guns"][0]["device"] == "C72"
          and st["guns"][0]["tab"] == "sweep", st)
    check("gun age is fresh", st["guns"][0]["seen_seconds"] <= 1, st)

    cl.get("/api/c72/tuning", params={"device": "C72", "tab": "link"})
    st = cl.get("/api/link/status").json()
    check("tab updates in place (no duplicate gun)",
          len(st["guns"]) == 1 and st["guns"][0]["tab"] == "link", st)

    # --- gun presence via a LINK scan POST (old APKs too) -----------------
    cl.post("/api/link/scans",
            json={"kind": "barcode", "value": "1", "device": "OLD-GUN"})
    st = cl.get("/api/link/status").json()
    old = [g for g in st["guns"] if g["device"] == "OLD-GUN"]
    check("a scan POST stamps its gun as on-link",
          len(old) == 1 and old[0]["tab"] == "link", st)

    # --- terminal presence + the toggle-ON pre-check ----------------------
    r = cl.get("/api/link/scans",
               params={"after": -1, "tid": "tid-A", "op": "Nick"})
    a = r.json()
    check("first terminal's seat call sees no other listeners",
          a["listeners"] == [], a)
    check("seat call reports the live guns",
          {g["device"] for g in a["guns"]} == {"C72", "OLD-GUN"}, a)

    r = cl.get("/api/link/scans",
               params={"after": -1, "tid": "tid-B", "op": "Steve"})
    b = r.json()
    check("second terminal sees the first, named",
          len(b["listeners"]) == 1 and b["listeners"][0]["tid"] == "tid-A"
          and b["listeners"][0]["operator"] == "Nick", b)
    check("caller is stamped before the snapshot (race defuse): A now sees B",
          any(t["tid"] == "tid-B" for t in
              cl.get("/api/link/scans",
                     params={"after": -1, "tid": "tid-A", "op": "Nick"})
              .json()["listeners"]))

    # --- the hot-path others counter --------------------------------------
    cursor = b["cursor"]
    r = cl.get("/api/link/scans",
               params={"after": cursor, "tid": "tid-B", "op": "Steve"})
    check("forward poll carries others=1", r.json().get("others") == 1,
          r.json())

    r = cl.post("/api/link/presence/release", json={"tid": "tid-A"})
    check("release answers ok", r.status_code == 200 and r.json()["ok"],
          r.text)
    r = cl.get("/api/link/scans",
               params={"after": cursor, "tid": "tid-B", "op": "Steve"})
    check("released terminal leaves others=0 immediately",
          r.json().get("others") == 0, r.json())

    # --- mixed fleet: a pre-update terminal polls without a tid -----------
    cl.get("/api/link/scans", params={"after": cursor})
    r = cl.get("/api/link/scans",
               params={"after": -1, "tid": "tid-B", "op": "Steve"})
    legacy = [t for t in r.json()["listeners"] if t["tid"] == "legacy"]
    check("tid-less forward poll registers the synthetic legacy listener",
          len(legacy) == 1
          and legacy[0]["operator"] == "a pre-update terminal", r.json())
    seat = cl.get("/api/link/scans", params={"after": -1}).json()
    check("a tid-less SEAT call does not register a listener",
          "legacy" in {t["tid"] for t in seat["listeners"]}
          and len(seat["listeners"]) == 2, seat)  # legacy + tid-B only

    # --- TTL expiry -------------------------------------------------------
    M._link_terminals["tid-B"]["seen"] -= (M.LINK_PRESENCE_TTL + 1)
    M._link_guns["OLD-GUN"]["seen"] -= (M.LINK_PRESENCE_TTL + 1)
    st = cl.get("/api/link/status").json()
    check("expired terminal drops out of status",
          "tid-B" not in {t["tid"] for t in st["listeners"]}, st)
    check("expired gun drops out of status",
          "OLD-GUN" not in {g["device"] for g in st["guns"]}, st)
    check("live entries survive the prune",
          "C72" in {g["device"] for g in st["guns"]}, st)

    # --- validation -------------------------------------------------------
    r = cl.post("/api/link/presence/release", json={"tid": ""})
    check("empty tid on release is rejected", r.status_code == 422, r.text)

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
