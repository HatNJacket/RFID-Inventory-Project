"""Boxify snapshot (Nick, 2026-09-14): dimensions live only in
Boxify's own database, so the terminal imports its CSV export and
lists every variant still shipping as the 8-cubic-inch default.
Import replaces the snapshot wholesale; zero or blank dimensions
count as missing; status feeds the audit card + pane.
"""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_boxify_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

HEAD = ("ProductTitle,ProductType,VariantTitle,VariantSKU,Length(cm),"
        "Width(cm),Height(cm),Padding(cm),KeepVertical,PackMode,"
        "ProductId,VariantId,NB\n")
CSV1 = HEAD + "\n".join([
    'Widget A,Type,Default Title,WID-A,"12.5","7.0","6.5","",false,1,'
    '[100],[1001],""',
    'Widget B,Type,Small,WID-B,"","","","",false,1,[200],[2001],""',
    'Widget B,Type,Large,WID-B-L,"0","4","4","",false,1,[200],[2002],""',
    'Widget C,Type,Default Title,WID-C,"junk","4","4","",false,1,'
    '[300],[3001],""',
]) + "\n"

with patch("app.main.oneleft"):
  with TestClient(app) as cl:
    r = cl.get("/api/boxify/status").json()
    check("empty snapshot answers imported False",
          r.get("imported") is False and r.get("items") == [], r)

    r = cl.post("/api/boxify/import", json={
        "csv_text": "\ufeff" + CSV1, "imported_by": "Nick"})
    d = r.json()
    check("import accepted (BOM tolerated)", r.status_code == 200,
          r.text[:300])
    check("blank, zero and junk dimensions all count missing",
          d.get("variants") == 4 and d.get("missing") == 3, d)

    r = cl.get("/api/boxify/status").json()
    check("status counts variants and distinct products",
          r["total_variants"] == 4 and r["missing_variants"] == 3
          and r["missing_products"] == 2 and r["imported_at"], r)
    check("missing list carries titles + skus",
          {x["sku"] for x in r["items"]} == {"WID-B", "WID-B-L",
                                             "WID-C"}, r["items"])
    r = cl.get("/api/boxify/status?query=widget b").json()
    check("query filters by product title",
          {x["sku"] for x in r["items"]} == {"WID-B", "WID-B-L"},
          r["items"])
    r = cl.get("/api/boxify/status?query=WID-C").json()
    check("query filters by sku",
          [x["sku"] for x in r["items"]] == ["WID-C"], r["items"])

    # ---- re-import replaces wholesale --------------------------------
    CSV2 = HEAD + ('Widget B,Type,Small,WID-B,"3","3","3","",false,1,'
                   '[200],[2001],""\n')
    r = cl.post("/api/boxify/import", json={"csv_text": CSV2})
    check("re-import accepted", r.status_code == 200, r.text[:200])
    r = cl.get("/api/boxify/status").json()
    check("snapshot replaced wholesale, nothing missing now",
          r["total_variants"] == 1 and r["missing_variants"] == 0
          and r["items"] == [], r)

    # ---- refusals -----------------------------------------------------
    r = cl.post("/api/boxify/import", json={
        "csv_text": "a,b,c\n1,2,3\n"})
    check("a non-Boxify CSV is refused with a plain reason",
          r.status_code == 422 and "Boxify" in r.text, r.text[:200])
    r = cl.post("/api/boxify/import", json={"csv_text": HEAD})
    check("a header-only export is refused",
          r.status_code == 422, r.text[:200])

    hist = cl.get("/api/history?limit=20").json()["events"]
    n = sum(1 for e in hist if e.get("type") == "boxify-import")
    check("each import lands one History event", n == 2,
          [e.get("type") for e in hist[:6]])

print()
if fails:
    print(f"{len(fails)} FAILURE(S):"); [print("  -", f) for f in fails]
    sys.exit(1)
print("ALL PASS")
