"""Label-match fallback (Nick, 2026-08-31, the Buckeye box labels):
vendor labels carry the maker's own item string - usually our SKU, but
sometimes separator-shifted (EAF-FTF30 vs EAF-FTF-30) or really the
VARIANT name (ZWO-Slider-Gen2 vs variant 'ZWO Slider Gen2' on sku
ZWO-SliderCase-Gen2). /api/products/label-match folds separators only
(never edit distance), answers UNIQUE hits, and hands ambiguity back
as candidates."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
os.environ.pop("STATION_KEY", None); os.environ.pop("PRINT_AGENT_KEY", None)
db = os.path.join(tempfile.gettempdir(), "rfid_labelmatch_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_engine
from app.models import BinMapEntry
from sqlalchemy.orm import Session
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.lookup_barcode_all", return_value=[]), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.main._maybe_refresh_bin_map", return_value=False):
  with TestClient(app) as cl:
    with Session(get_engine()) as s:
        s.add(BinMapEntry(sku="EAF-FTF-30", barcode="59995776891995",
                          product_title="Buckeye ZWO EAF Bracket",
                          variant_title="EAF-FTF-30", bin="E4-1", qty=1,
                          shopify_variant_id="t:FTF"))
        s.add(BinMapEntry(sku="ZWO-SliderCase-Gen2", barcode="93952531000379",
                          product_title="Buckeye Filter Slider Case",
                          variant_title="ZWO Slider Gen2", bin="E4-3",
                          qty=2, shopify_variant_id="t:SLIDER"))
        # A fold collision pair: 'AB-CD' and 'AB CD' both fold to ABCD.
        s.add(BinMapEntry(sku="AB-CD", barcode="801",
                          product_title="Widget One", bin="Z1-1", qty=1,
                          shopify_variant_id="t:W1"))
        s.add(BinMapEntry(sku="AB CD", barcode="802",
                          product_title="Widget Two", bin="Z1-2", qty=1,
                          shopify_variant_id="t:W2"))
        s.commit()

    r = cl.get("/api/products/label-match/EAF-FTF30")
    check("a separator-shifted label finds its SKU",
          r.status_code == 200 and r.json()["ok"]
          and r.json()["product"]["sku"] == "EAF-FTF-30"
          and r.json()["matched_by"] == "sku", r.text[:200])

    r = cl.get("/api/products/label-match/ZWO-Slider-Gen2")
    check("a label that is really the VARIANT name resolves",
          r.status_code == 200 and r.json()["ok"]
          and r.json()["product"]["sku"] == "ZWO-SliderCase-Gen2"
          and r.json()["matched_by"] == "variant name"
          and r.json()["matched_value"] == "ZWO Slider Gen2",
          r.text[:250])

    r = cl.get("/api/products/label-match/AB.CD")
    check("a fold collision answers CANDIDATES, never a guess",
          r.status_code == 200 and r.json().get("ambiguous")
          and len(r.json()["candidates"]) == 2, r.text[:250])

    r = cl.get("/api/products/label-match/XY")
    check("too-short labels refuse to match", r.status_code == 404,
          r.status_code)

    r = cl.get("/api/products/label-match/TOTALLY-UNKNOWN-9")
    check("a genuinely unknown label 404s", r.status_code == 404,
          r.status_code)

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
