"""Refresh timing log + stats (the shared refresh-button component).

Buttons post how long each run took; the stats endpoint serves a recent
median per kind and names any server-side auto refresh running right now
(so a page that loads mid-refresh resumes the animation mid-fill)."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_refresh_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from fastapi.testclient import TestClient
from app.main import app
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

with TestClient(app) as cl:
    r = cl.get("/api/refresh-stats").json()
    check("stats start empty", r=={"stats":{},"running":{}}, r)

    # Seven runs; the median must shrug off the one outlier.
    for ms in (4000, 4200, 3900, 60000, 4100, 4000, 4050):
        cl.post("/api/refresh-log", json={"kind":"bin-map-pull","ms":ms})
    cl.post("/api/refresh-log",
            json={"kind":"oneleft-board","ms":900,"source":"auto"})
    r = cl.get("/api/refresh-stats").json()
    check("median per kind, outlier ignored",
          3900 <= r["stats"].get("bin-map-pull", 0) <= 4200, r)
    check("second kind tracked separately",
          r["stats"].get("oneleft-board")==900, r)

    # Server-side auto marker: running now -> reported; stale -> not.
    from app.database import get_engine
    from sqlalchemy.orm import Session as S
    from app.models import AppSetting
    from datetime import datetime, timedelta
    with S(get_engine()) as s:
        s.add(AppSetting(key="refresh_running:orders-sync",
                         value=datetime.utcnow().isoformat()))
        s.add(AppSetting(key="refresh_running:dead-sync",
                         value=(datetime.utcnow()-timedelta(hours=2)).isoformat()))
        s.commit()
    r = cl.get("/api/refresh-stats").json()
    check("live auto refresh reported as running",
          "orders-sync" in r["running"], r)
    check("stale (crashed) marker ignored",
          "dead-sync" not in r["running"], r)

    # Retention: the log keeps ~50 rows per kind.
    for ms in range(120):
        cl.post("/api/refresh-log", json={"kind":"spam","ms":1000+ms})
    from app.models import RefreshLog
    from sqlalchemy import select, func as f
    with S(get_engine()) as s:
        n = s.scalar(select(f.count()).select_from(RefreshLog)
                     .where(RefreshLog.kind=="spam"))
    check("log pruned to ~50 rows per kind", n <= 51, n)

    r = cl.post("/api/refresh-log", json={"kind":"x","ms":-5})
    check("negative duration rejected", r.status_code==422, r.status_code)

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
