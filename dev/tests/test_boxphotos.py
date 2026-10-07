"""Box photos collector (2026-10-07): the gun's developer-mode camera.

Matching: what Azure read becomes "auto" (one clear SKU, misread letters
like I/1 and O/0 forgiven), "ask" (a product family such as F9301A ->
AA/AB/AC, the size word ranking the right one first) or "none".
Server: an upload stores the files, reads them and files the photo; a
retried upload never doubles; "Keep in SKU" files straight in; the
current list shows unsent photos and the batch's products; filing,
unfiling, deleting, restoring and "Looks right" move photos between
states; search puts the batch first; Send to sorter makes one hand-off
that only one terminal can load, takes the photos out of the current
shipment and writes History."""
import base64, os, sys, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"] = "t.myshopify.com"
os.environ["SHOPIFY_CLIENT_ID"] = "x"
os.environ["SHOPIFY_CLIENT_SECRET"] = "x"
os.environ["ORDERS_SYNC_DISABLE"] = "1"
os.environ.pop("STATION_KEY", None)
os.environ["VISION_ENDPOINT"] = "https://vision.invalid"
os.environ["VISION_KEY"] = "k"
photos_dir = os.path.join(tempfile.gettempdir(), "rfid_boxphotos_test")
shutil.rmtree(photos_dir, ignore_errors=True)
os.environ["BOX_PHOTO_DIR"] = photos_dir
db = os.path.join(tempfile.gettempdir(), "rfid_boxphotos_test.db")
if os.path.exists(db):
    os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\", "/")
from unittest.mock import patch
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.main import app
from app import boxphotos as bp
from app.database import get_engine
from app.models import Batch, BatchItem, BinMapEntry, BarcodeChange, BoxPhoto

fails = []


def check(label, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + label + ("" if cond else f"  <- {extra}"))
    if not cond:
        fails.append(label)


CATALOG = [
    ("F9172B", "Svbony SV136 1.25'' 18mm 72-Degree Eyepiece", None, "I1-3"),
    ("F9172A", "Svbony SV136 1.25\" 72 Degree 9mm Eyepiece", None, "I1-3"),
    ("F9301AA", "SVBONY 4/10/23mm Wide Angle 62 Aspheric", "4mm", "I1-5"),
    ("F9301AB", "SVBONY 4/10/23mm Wide Angle 62 Aspheric", "10mm", "I1-5"),
    ("F9301AC", "SVBONY 4/10/23mm Wide Angle 62 Aspheric", "23mm", "I1-5"),
    ("W2578A", "Svbony 1.25\" 45 Degree Arcing Prism Diagonal", None, "I2-1"),
    ("F9146A", "Svbony 2X 1.25\" Achromatic Barlow Lens", None, "I1-2"),
]
cat = [{"sku": s, "product_title": t, "variant_title": v} for s, t, v, _ in CATALOG]

# ------------------------------------------------------------- matching --
m = bp.match_skus(["SVBONY", "F9172B", "SV136 18mm 1.25in"], cat)
check("a clean read of a SKU files it automatically",
      m["status"] == "auto" and m["sku"] == "F9172B", str(m))
m = bp.match_skus(["F9I72B"], cat)
check("an I read for a 1 is forgiven", m["status"] == "auto" and m["sku"] == "F9172B", str(m))
m = bp.match_skus(["W2578 A"], cat)
check("a SKU split by a space still matches",
      m["status"] == "auto" and m["sku"] == "W2578A", str(m))
m = bp.match_skus(["F9301A", "10mm"], cat)
check("a product family asks, with the size that was read first",
      m["status"] == "ask" and m["guesses"][0]["sku"] == "F9301AB"
      and {g["sku"] for g in m["guesses"]} == {"F9301AA", "F9301AB", "F9301AC"}, str(m))
m = bp.match_skus(["SVBONY", "www.svbony.com", "Made in China"], cat)
check("no SKU on the photo reads as none", m["status"] == "none" and not m["sku"], str(m))
m = bp.match_skus(["F9172"], cat, {"F9172A"})
check("between two close SKUs the batch's product ranks first",
      m["status"] == "ask" and m["guesses"][0]["sku"] == "F9172A", str(m))

# ------------------------------------------------- ticked multi-SKU label --
import io
from PIL import Image, ImageDraw


def label(ticked):
    """A drawn label: four SKU lines, each with a box to its left, one
    filled in; returns (jpeg bytes, the reader's layout)."""
    img = Image.new("L", (800, 500), 235)
    d = ImageDraw.Draw(img)
    layout = []
    for i, code in enumerate(["W9132A", "W9132B", "W9132C", "W9132D"]):
        y = 80 + i * 90
        d.rectangle([100, y, 140, y + 40], outline=30, width=3)
        if code == ticked:
            d.ellipse([106, y + 6, 134, y + 34], fill=20)
        d.rectangle([160, y + 8, 420, y + 32], fill=60)  # the printed text
        layout.append({"text": f"L=240mm ({code})",
                       "box": [160, y, 430, y, 430, y + 40, 160, y + 40]})
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    return buf.getvalue(), layout


img, lay = label("W9132C")
t = bp.find_ticked(img, lay)
check("the ticked box is found by its ink", t["sku"] == "W9132C", str(t))
img0, lay0 = label(None)
check("no tick, no winner", bp.find_ticked(img0, lay0)["sku"] is None)
cm = {p["sku"].upper(): p for p in cat}
cm["W9132B"] = {"sku": "W9132B", "product_title": "Dew strip", "variant_title": "320mm"}
cm["W9132C"] = {"sku": "W9132C", "product_title": "Dew strip", "variant_title": "400mm"}
lines = [x["text"] for x in lay]
v = bp.apply_tick({"status": "ask", "sku": None, "guesses": [{"sku": "W9132B"}, {"sku": "W9132C"}]},
                  lines, lay, img, cm)
check("a ticked label files itself to the ticked SKU, as auto",
      v["status"] == "auto" and v["sku"] == "W9132C" and v["guesses"][0]["note"] == "ticked", str(v))
only_b = {"W9132B": cm["W9132B"]}
v = bp.apply_tick({"status": "auto", "sku": "W9132B", "guesses": [{"sku": "W9132B"}]},
                  lines, lay0, img0, only_b)
check("a multi-SKU label never auto-files without a tick, even with one SKU we stock",
      v["status"] == "ask" and v["sku"] is None and "Pick the ticked one" in v["note"], str(v))
v = bp.apply_tick({"status": "auto", "sku": "W9132B", "guesses": [{"sku": "W9132B"}]},
                  lines, lay, img, only_b)
check("a tick on a SKU we don't stock asks instead",
      v["status"] == "ask" and "W9132C" in v["note"], str(v))
v = bp.apply_tick({"status": "auto", "sku": "F9172B", "guesses": []}, ["F9172B"], None, b"", cm)
check("a one-SKU label is left alone", v["status"] == "auto" and v["sku"] == "F9172B", str(v))

# --------------------------------------------------------------- server --
with Session(get_engine()) as s:
    pass  # engine up; tables come from the app's startup

JPEG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"0" * 400).decode()


def upload(cl, uid, lines, **kw):
    with patch("app.boxphotos.azure_read", return_value=(lines, None)):
        body = {"uid": uid, "image_b64": JPEG, "thumb_b64": JPEG, "worker": "Steve"}
        body.update(kw)
        r = cl.post("/api/boxphotos", json=body)
    assert r.status_code == 200, r.text
    return r.json()["photo"]


with patch("app.main._maybe_refresh_bin_map", return_value=False):
    with TestClient(app) as cl:
        with Session(get_engine()) as s:
            for sku, title, variant, binn in CATALOG:
                s.add(BinMapEntry(sku=sku, product_title=title, variant_title=variant,
                                  bin=binn, barcode="99" + sku[-4:]))
            b = Batch(bin_name="SO 1042 Svbony", status="collecting")
            s.add(b)
            s.flush()
            for sku in ("F9172A", "F9146A"):
                s.add(BatchItem(batch_id=b.id, scanned_code=sku, resolved=True, sku=sku,
                                product_title="t " + sku))
            s.commit()
            batch_id = b.id
        bp._catalog_cache.update(at=0.0, rows=[])

        p1 = upload(cl, "uid-0001-a", ["SVBONY", "F9172B", "SV136 18mm"], batch_id=batch_id)
        check("an upload is read and filed automatically",
              p1["status"] == "auto" and p1["sku"] == "F9172B" and "SV136" in (p1["title"] or ""), str(p1))
        files = os.listdir(os.path.join(photos_dir, os.listdir(photos_dir)[0]))
        check("the photo and its thumbnail are saved",
              "uid-0001-a.jpg" in files and "uid-0001-a_t.jpg" in files, str(files))
        again = upload(cl, "uid-0001-a", ["nothing"])
        with Session(get_engine()) as s:
            n = len(s.scalars(select(BoxPhoto)).all())
        check("a retried upload returns the same photo, never a second one",
              again["id"] == p1["id"] and n == 1, f"{again} n={n}")
        p2 = upload(cl, "uid-0002-a", ["SVBONY"], pin_sku="f9172b", new_box=False)
        check("Keep in SKU files straight into the folder as confirmed",
              p2["status"] == "confirmed" and p2["sku"] == "F9172B" and p2["new_box"] is False, str(p2))
        pa = upload(cl, "uid-0002-b", ["SVBONY"], pin_sku="F9172B", pin_auto=True, new_box=False)
        check("a photo filed by its box-mate's read stays auto (not a label yet)",
              pa["status"] == "auto" and pa["sku"] == "F9172B", str(pa))
        cl.post(f"/api/boxphotos/{pa['id']}/delete")
        p3 = upload(cl, "uid-0003-a", ["F9301A", "10mm"], batch_id=batch_id)
        p4 = upload(cl, "uid-0004-a", ["SVBONY", "svbony.com"], batch_id=batch_id)
        check("unsure and unreadable photos wait for a person",
              p3["status"] == "ask" and p4["status"] == "none", f"{p3['status']} {p4['status']}")
        with patch("app.boxphotos.config.VISION_KEY", ""):
            body = {"uid": "uid-0005-a", "image_b64": JPEG, "thumb_b64": JPEG}
            p5 = cl.post("/api/boxphotos", json=body).json()["photo"]
        check("with no reader set up the photo still saves, to sort by hand",
              p5["status"] == "none" and "set up" in (p5["ocr_error"] or ""), str(p5))

        cur = cl.get("/api/boxphotos/current", params={"batch_id": batch_id}).json()
        check("the current shipment lists every photo, newest first",
              [p["uid"] for p in cur["photos"]][:2] == ["uid-0005-a", "uid-0004-a"]
              and len(cur["photos"]) == 5, str([p["uid"] for p in cur["photos"]]))
        check("the batch's products come along for the folder list",
              cur["batch"]["label"] == "SO 1042 Svbony"
              and {p["sku"] for p in cur["batch"]["products"]} == {"F9172A", "F9146A"}, str(cur["batch"]))

        r = cl.post(f"/api/boxphotos/{p3['id']}/file", json={"sku": "F9301AB", "worker": "Steve"})
        check("picking a guess files the photo as confirmed",
              r.json()["photo"]["status"] == "confirmed" and r.json()["photo"]["sku"] == "F9301AB", r.text)
        r = cl.post(f"/api/boxphotos/{p3['id']}/file", json={"sku": "F9301AB", "auto": True})
        check("filing by inference keeps the photo auto",
              r.json()["photo"]["status"] == "auto", r.text)
        r = cl.post(f"/api/boxphotos/{p3['id']}/unfile")
        check("undo puts it back with its guesses",
              r.json()["photo"]["status"] == "ask" and r.json()["photo"]["sku"] is None, r.text)
        r = cl.post(f"/api/boxphotos/{p3['id']}/file", json={"sku": "NOPE-1"})
        check("a SKU that isn't in the catalog is refused", r.status_code == 404, r.text)
        cl.post(f"/api/boxphotos/{p3['id']}/file", json={"sku": "F9301AB"})
        cl.post(f"/api/boxphotos/{p4['id']}/delete")
        cur = cl.get("/api/boxphotos/current").json()
        check("a deleted photo leaves the shipment",
              p4["id"] not in [p["id"] for p in cur["photos"]])
        r = cl.post(f"/api/boxphotos/{p4['id']}/restore")
        check("restoring brings it back to sort by hand", r.json()["photo"]["status"] == "none", r.text)
        cl.post(f"/api/boxphotos/{p4['id']}/delete")

        r = cl.post("/api/boxphotos/confirm", json={"ids": [p1["id"], p2["id"]], "worker": "Steve"})
        with Session(get_engine()) as s:
            st = s.get(BoxPhoto, p1["id"])
            st_status, st_by = st.status, st.confirmed_by
        check("Looks right confirms only the auto filings",
              r.json()["confirmed"] == 1 and st_status == "confirmed" and st_by == "Steve", r.text)

        r = cl.get(f"/api/boxphotos/{p1['id']}/thumb")
        check("thumbnails are served", r.status_code == 200 and r.content.startswith(b"\xff\xd8"), r.status_code)

        res = cl.get("/api/boxphotos/search", params={"q": "arcing prism", "batch_id": batch_id}).json()["results"]
        check("search finds products by name words", [x["sku"] for x in res] == ["W2578A"], str(res))
        res = cl.get("/api/boxphotos/search", params={"q": "F917", "batch_id": batch_id}).json()["results"]
        check("search puts the batch's products first", res and res[0]["sku"] == "F9172A" and res[0]["in_batch"], str(res))
        res = cl.get("/api/boxphotos/search", params={"q": "99578A"}).json()["results"]
        check("a scanned barcode finds its product", res and res[0]["sku"] == "W2578A", str(res))

        body = {"items": [{"sku": "F9172B", "title": "SV136 18mm", "qty": 1},
                          {"sku": "F9301AB", "qty": 2}],
                "photo_ids": [p1["id"], p2["id"], p3["id"]], "batch_id": batch_id, "worker": "Steve"}
        r = cl.post("/api/boxphotos/send", json=body)
        h = r.json()["handoff"]
        check("Send to sorter makes one hand-off with the box count",
              h["boxes"] == 3 and len(h["items"]) == 2, r.text)
        cur = cl.get("/api/boxphotos/current").json()
        check("sent photos leave the current shipment, the rest stay",
              [p["uid"] for p in cur["photos"]] == ["uid-0005-a"] and cur["handoffs_pending"] == 1,
              str([p["uid"] for p in cur["photos"]]))
        with Session(get_engine()) as s:
            hist = s.scalars(select(BarcodeChange).where(
                BarcodeChange.changed_field == "box-photos-sent")).all()
            hist_ok = len(hist) == 1 and hist[0].new_barcode.startswith("3 boxes of 2 products")
        check("sending writes History", hist_ok)
        lst = cl.get("/api/boxphotos/handoffs").json()["handoffs"]
        check("the web sorter sees the pending list", len(lst) == 1 and lst[0]["id"] == h["id"], str(lst))
        r1 = cl.post(f"/api/boxphotos/handoffs/{h['id']}/load", json={"worker": "Nick"})
        r2 = cl.post(f"/api/boxphotos/handoffs/{h['id']}/load", json={})
        check("only one terminal can load a list",
              r1.status_code == 200 and r2.status_code == 409, f"{r1.status_code} {r2.status_code}")
        check("a loaded list stops showing", cl.get("/api/boxphotos/handoffs").json()["handoffs"] == [])
        h2 = cl.post("/api/boxphotos/send", json={"items": [{"sku": "W2578A", "qty": 1}]}).json()["handoff"]
        cl.post(f"/api/boxphotos/handoffs/{h2['id']}/dismiss")
        check("a dismissed list stops showing", cl.get("/api/boxphotos/handoffs").json()["handoffs"] == [])
        f1 = upload(cl, "uid-0009-f", ["F9146A"], focus_diopters=4.0, af_ms=400,
                    af_result="locked", focus_mode="trigger")
        upload(cl, "uid-0010-f", ["F9146A"], focus_diopters=5.0, af_ms=900,
               af_result="locked", focus_mode="trigger")
        upload(cl, "uid-0011-f", ["F9146A"], focus_diopters=9.0, af_ms=1500,
               af_result="timeout", focus_mode="trigger")
        fs = cl.get("/api/boxphotos/focus").json()
        check("each photo keeps its focus, and the summary uses locked ones",
              f1["focus_diopters"] == 4.0 and fs["n"] == 3
              and fs["median_diopters"] == 4.5 and fs["median_cm"] == 22
              and fs["results"] == {"locked": 2, "timeout": 1}, str(fs))
        check("the current shipment carries the focus summary for the gun",
              cl.get("/api/boxphotos/current").json()["focus"]["median_diopters"] == 4.5)
        for u in ("uid-0009-f", "uid-0010-f", "uid-0011-f"):
            with Session(get_engine()) as s:
                row = s.scalar(select(BoxPhoto).where(BoxPhoto.uid == u))
                row.status = "deleted"
                s.commit()
        bx = upload(cl, "uid-0012-b", ["F9146A"], box_uid="box-aaa")
        r = cl.post(f"/api/boxphotos/{bx['id']}/quality", json={"quality": "blurry"})
        check("a photo keeps its box and takes a quality tag",
              bx["box_uid"] == "box-aaa" and r.json()["photo"]["quality"] == "blurry", r.text)
        r = cl.post(f"/api/boxphotos/{bx['id']}/quality", json={"quality": "fuzzy"})
        check("only good, blurry or angle are accepted", r.status_code == 422, r.status_code)
        r = cl.post(f"/api/boxphotos/{bx['id']}/quality", json={"quality": None})
        check("the tag can be cleared", r.json()["photo"]["quality"] is None, r.text)
        cl.post(f"/api/boxphotos/{bx['id']}/delete")
        exp = cl.get("/api/boxphotos/export").json()["photos"]
        check("export lists every photo with its file", len(exp) == 10 and all(e["file"] for e in exp))

print()
print(f"{len(fails)} failed" if fails else "all passed")
sys.exit(1 if fails else 0)
