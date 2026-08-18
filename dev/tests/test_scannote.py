"""Per-product scan notes: set/clear via the edit window, shown on every
lookup (web card + C72 both read /api/products/by-barcode), History-logged."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_scannote_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.main import app
from app.database import get_engine
from app.models import BinMapEntry
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]):
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        s.add(BinMapEntry(sku="CASE-8", barcode="888",
                          product_title="Case of Eight", bin="A1-1", qty=8))
        s.commit()

    r = cl.put("/api/products/CASE-8/scan-note",
               json={"note":"Open the case before tagging","changed_by":"Nick"})
    check("note saves", r.status_code==200
          and r.json()["scan_note"]=="Open the case before tagging", r.text)

    p = cl.get("/api/products/by-barcode/888").json()
    check("note rides the barcode lookup",
          p.get("scan_note")=="Open the case before tagging", p)
    p = cl.get("/api/products/by-barcode/CASE-8").json()
    check("note rides the SKU lookup too",
          p.get("scan_note")=="Open the case before tagging", p)

    h = cl.get("/api/product-history?term=CASE-8").json()
    notes = [e for e in h["events"] if e["type"]=="scan-note"]
    check("History records the note change",
          len(notes)==1 and notes[0]["worker"]=="Nick"
          and notes[0]["shopify"] is False, notes)

    # Unchanged save -> no duplicate History row.
    cl.put("/api/products/CASE-8/scan-note",
           json={"note":"Open the case before tagging","changed_by":"Nick"})
    h = cl.get("/api/product-history?term=CASE-8").json()
    check("re-saving the same note logs nothing new",
          len([e for e in h["events"] if e["type"]=="scan-note"])==1, "")

    # Clearing.
    r = cl.put("/api/products/CASE-8/scan-note",
               json={"note":"","changed_by":"Nick"})
    check("empty note clears", r.json()["scan_note"] is None, r.text)
    p = cl.get("/api/products/by-barcode/888").json()
    check("cleared note vanishes from lookups", "scan_note" not in p, p)
    h = cl.get("/api/product-history?term=CASE-8").json()
    check("the clear is History-logged too",
          len([e for e in h["events"] if e["type"]=="scan-note"])==2, "")

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
