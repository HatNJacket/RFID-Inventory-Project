"""SKUs with a literal double-quote (Nick, 2026-08-26 - Antlia's 2\"
filters: ANT-ULTRA-2.5nm-2"-Ha). The old search building STRIPPED the
quote, so every Shopify SKU search for these silently missed: bin moves
404'd before writing anything, on-hand read null. Terms now embed with
a backslash escape (verified live against the store), and the exact
post-filters still match the ORIGINAL sku string."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="x"
os.environ["SHOPIFY_CLIENT_SECRET"]="x"
os.environ["ORDERS_SYNC_DISABLE"]="1"
db = os.path.join(tempfile.gettempdir(), "rfid_quotesku_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from app import shopify
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

SKU = 'ANT-ULTRA-2.5nm-2"-Ha'
searches = []

def fake_query(query, variables=None):
    search = (variables or {}).get("search", "")
    searches.append(search)
    # Answer only when the search carries the ESCAPED quote - exactly
    # how the live store behaves (stripped terms match nothing).
    hit = '2\\"-Ha' in search
    node = {
        "sku": SKU, "barcode": "96745277503611",
        "inventoryQuantity": 3,
        "inventoryItem": {"inventoryLevels": {"nodes": [
            {"quantities": [{"name": "on_hand", "quantity": 3}]}
        ]}},
    }
    return {"productVariants": {"nodes": [node] if hit else []}}

with patch("app.shopify.query_shopify", side_effect=fake_query):
    check("the escape helper doubles backslashes then escapes quotes",
          shopify._search_term('A\\B"C') == 'A\\\\B\\"C',
          shopify._search_term('A\\B"C'))

    r = shopify.get_on_hand(SKU)
    check("get_on_hand finds a quoted SKU", r == 3, r)
    check("...because the search carried the escaped quote",
          any('sku:"ANT-ULTRA-2.5nm-2\\"-Ha"' in s for s in searches),
          searches[-2:])

    searches.clear()
    r = shopify.get_quantities_by_skus([SKU, "CLEAN-1"])
    check("batched quantities keep quoted SKUs in the sweep",
          r.get(SKU) == 3, r)
    check("...batch search escapes them too",
          any('\\"-Ha' in s for s in searches), searches)

print()
print("FAILED: "+", ".join(fails) if fails else "ALL CHECKS PASSED")
sys.exit(1 if fails else 0)
