"""Remote debug link (2026-10-07): the gun's log lines are stored and
read back in order; a queued command is claimed once, by the right gun
(or any gun for "*"), and its result and screenshot come back; unknown
commands are refused."""
import base64, os, sys, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"] = "t.myshopify.com"
os.environ["SHOPIFY_CLIENT_ID"] = "x"
os.environ["SHOPIFY_CLIENT_SECRET"] = "x"
os.environ["ORDERS_SYNC_DISABLE"] = "1"
os.environ.pop("STATION_KEY", None)
d = os.path.join(tempfile.gettempdir(), "rfid_devlog_test_photos")
shutil.rmtree(d, ignore_errors=True)
os.environ["BOX_PHOTO_DIR"] = d
db = os.path.join(tempfile.gettempdir(), "rfid_devlog_test.db")
if os.path.exists(db):
    os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\", "/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app

fails = []


def check(label, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + label + ("" if cond else f"  <- {extra}"))
    if not cond:
        fails.append(label)


with patch("app.main._maybe_refresh_bin_map", return_value=False):
    with TestClient(app) as cl:
        cl.post("/api/devlog/lines", json={"device": "C72", "lines": ["one", "two"]})
        cl.post("/api/devlog/lines", json={"device": "C72-B", "lines": ["other"]})
        got = cl.get("/api/devlog/lines").json()["lines"]
        check("log lines come back oldest first", [x["line"] for x in got] == ["one", "two", "other"], str(got))
        got = cl.get("/api/devlog/lines", params={"device": "C72", "after": got[0]["id"]}).json()["lines"]
        check("lines filter by gun and position", [x["line"] for x in got] == ["two"], str(got))
        r = cl.post("/api/devlog/commands", json={"cmd": "format_disk"})
        check("unknown commands are refused", r.status_code == 400, r.text)
        c1 = cl.post("/api/devlog/commands", json={"device": "C72-B", "cmd": "ping"}).json()["command"]
        c2 = cl.post("/api/devlog/commands", json={"cmd": "shot"}).json()["command"]
        n = cl.get("/api/devlog/commands/next", params={"device": "C72"}).json()["command"]
        check("a gun skips commands meant for another gun", n and n["id"] == c2["id"], str(n))
        n2 = cl.get("/api/devlog/commands/next", params={"device": "C72"}).json()["command"]
        check("a command is claimed only once", n2 is None, str(n2))
        jpg = base64.b64encode(b"\xff\xd8\xff\xe0" + b"1" * 50).decode()
        cl.post(f"/api/devlog/commands/{c2['id']}/result", json={"result": "Screenshot of X", "shot_b64": jpg})
        st = cl.get(f"/api/devlog/commands/{c2['id']}").json()["command"]
        check("the result comes back", st["result"] == "Screenshot of X" and st["has_shot"] and st["done_at"], str(st))
        r = cl.get(f"/api/devlog/commands/{c2['id']}/shot")
        check("the screenshot is served", r.status_code == 200 and r.content.startswith(b"\xff\xd8"), r.status_code)
        n3 = cl.get("/api/devlog/commands/next", params={"device": "C72-B"}).json()["command"]
        check("the other gun gets its own command", n3 and n3["id"] == c1["id"], str(n3))

print()
print(f"{len(fails)} failed" if fails else "all passed")
sys.exit(1 if fails else 0)
