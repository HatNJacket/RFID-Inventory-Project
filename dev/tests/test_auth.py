"""Sign-in (2026-10-05): the terminal asks who you are before it shows
anything. Microsoft/Google OpenID Connect with a fake provider here,
the users list deciding who gets past the login page, the signed
session cookie, the device key still working for the gun, and the
Users endpoints with their History rows.
"""
import json, os, sys, tempfile, base64, hashlib, hmac, time
sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
os.environ["SHOPIFY_STORE"]="t.myshopify.com"; os.environ["SHOPIFY_CLIENT_ID"]="shopcid"
os.environ["SHOPIFY_CLIENT_SECRET"]="shopsecret"
os.environ["STATION_KEY"] = "gunkey"
os.environ.pop("PRINT_AGENT_KEY", None)
os.environ["AUTH_SESSION_SECRET"] = "testsecret"
os.environ["MS_TENANT_ID"] = "tenant-1"
os.environ["MS_CLIENT_ID"] = "mscid"
os.environ["MS_CLIENT_SECRET"] = "mssecret"
os.environ["GOOGLE_CLIENT_ID"] = "gcid"
os.environ["GOOGLE_CLIENT_SECRET"] = "gsecret"
db = os.path.join(tempfile.gettempdir(), "rfid_auth_test.db")
if os.path.exists(db): os.remove(db)
os.environ["DATABASE_URL"] = "sqlite:///" + db.replace("\\","/")
from unittest.mock import patch
from urllib.parse import urlparse, parse_qs
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session
import app.main as M
import app.auth as A
from app.main import app
from app.database import get_engine
from app.models import RfidUser, BarcodeChange
fails=[]
def check(l,c,x=""):
    print(("PASS  " if c else "FAIL  ")+l+("" if c else f"  <- {x}"))
    if not c: fails.append(l)

def b64(b): return base64.urlsafe_b64encode(b).decode().rstrip("=")
def fake_id_token(claims):
    return "h." + b64(json.dumps(claims).encode()) + ".s"

FAKE_EP = {"authorization_endpoint": "https://idp.test/authorize",
           "token_endpoint": "https://idp.test/token",
           "issuer": "https://login.microsoftonline.com/tenant-1/v2.0"}
# What the fake provider will say the next time the app exchanges a code.
NEXT_CLAIMS = {}
LAST_TOKEN_POST = {}
def fake_token_post(url, data):
    LAST_TOKEN_POST.clear(); LAST_TOKEN_POST.update(data)
    return {"id_token": fake_id_token(NEXT_CLAIMS)}

def do_login(cl, provider, claims, next_path="/"):
    """Walk the redirect dance and return the callback response."""
    r = cl.get(f"/auth/login/{provider}", params={"next": next_path},
               follow_redirects=False)
    loc = r.headers.get("location", "")
    q = parse_qs(urlparse(loc).query)
    state = q.get("state", [""])[0]
    saved = A._unsign(cl.cookies.get(A.OAUTH_COOKIE))
    c = dict(claims)
    c.setdefault("aud", "mscid" if provider == "microsoft" else "gcid")
    c.setdefault("nonce", saved["n"] if saved else "")
    c.setdefault("exp", int(time.time()) + 600)
    c.setdefault("iss", "https://login.microsoftonline.com/tenant-1/v2.0"
                 if provider == "microsoft" else "https://accounts.google.com")
    NEXT_CLAIMS.clear(); NEXT_CLAIMS.update(c)
    return cl.get(f"/auth/callback/{provider}",
                  params={"code": "abc", "state": state},
                  follow_redirects=False)

def shopify_token(sub):
    hdr = b64(b'{"alg":"HS256"}')
    pl = b64(json.dumps({"aud": "shopcid", "sub": sub,
                         "exp": int(time.time()) + 600}).encode())
    sig = b64(hmac.new(b"shopsecret", f"{hdr}.{pl}".encode(),
                       hashlib.sha256).digest())
    return f"{hdr}.{pl}.{sig}"

with patch("app.shopify.lookup_barcode", return_value=None), \
     patch("app.shopify.fetch_all_variant_bins", return_value=[]), \
     patch("app.auth._endpoints", side_effect=lambda n: FAKE_EP), \
     patch("app.auth._token_post", side_effect=fake_token_post):
  M._maybe_refresh_bin_map = lambda *a, **k: False
  with TestClient(app, base_url="https://testserver") as cl:
    check("sign-in is on", A.auth_enabled())

    r = cl.get("/", follow_redirects=False)
    check("the terminal redirects a stranger to the login page",
          r.status_code == 303 and r.headers["location"] == "/login", r.status_code)
    r = cl.get("/?tab=audits", follow_redirects=False)
    check("  ...remembering where they were going",
          "next=%2F%3Ftab%3Daudits" in r.headers.get("location", ""), r.headers.get("location"))
    r = cl.get("/login")
    check("the login page offers both providers",
          r.status_code == 200 and "Sign in with Microsoft" in r.text
          and "Sign in with Google" in r.text)
    r = cl.get("/api/users")
    check("the API refuses a stranger", r.status_code == 401)
    r = cl.get("/api/users", headers={"X-Station-Key": "gunkey"})
    check("the gun's device key still works", r.status_code == 200, r.text)
    r = cl.get("/api/users", params={"key": "gunkey"})
    check("  ...but the browser's ?key= bootstrap door is closed", r.status_code == 401)

    r = cl.get("/auth/login/microsoft", follow_redirects=False)
    loc = r.headers.get("location", "")
    q = parse_qs(urlparse(loc).query)
    check("sign-in starts at the provider with PKCE and state",
          loc.startswith("https://idp.test/authorize?")
          and q.get("code_challenge_method") == ["S256"] and "state" in q
          and q.get("redirect_uri") == ["https://testserver/auth/callback/microsoft"], loc)

    r = do_login(cl, "microsoft", {"email": "nick@telescopescanada.ca",
                                   "name": "Nick Drapak", "sub": "ms-1"})
    check("an email NOT on the list bounces back to login",
          r.status_code == 303 and "err=notlisted" in r.headers["location"]
          and "nick%40telescopescanada.ca" in r.headers["location"], r.headers.get("location"))
    check("  ...with no session cookie", A.SESSION_COOKIE not in cl.cookies)

    # Seed the list the way the Users tab does (the device key is enough).
    r = cl.post("/api/users", headers={"X-Station-Key": "gunkey"},
                json={"email": "Nick@TelescopesCanada.ca", "name": "Nick",
                      "worker": "Steve"})
    check("a user is added (email lowered)", r.status_code == 201
          and r.json()["user"]["email"] == "nick@telescopescanada.ca", r.text)
    r = cl.post("/api/users", headers={"X-Station-Key": "gunkey"},
                json={"email": "nick@telescopescanada.ca"})
    check("  ...twice is refused", r.status_code == 409)
    r = cl.post("/api/users", headers={"X-Station-Key": "gunkey"},
                json={"email": "not an email"})
    check("  ...garbage is refused", r.status_code == 422)
    r = cl.post("/api/users", headers={"X-Station-Key": "gunkey"},
                json={"email": "alex@gmail.com"})
    alex_id = r.json()["user"]["id"]
    check("a nameless Google user is added", r.status_code == 201
          and r.json()["user"]["name"] is None)

    r = cl.get("/auth/callback/microsoft", params={"code": "x", "state": "bogus"},
               follow_redirects=False)
    check("a callback with the wrong state is refused",
          "err=state" in r.headers.get("location", ""), r.headers.get("location"))

    r = do_login(cl, "microsoft", {"email": "nick@telescopescanada.ca",
                                   "name": "Nick Drapak", "sub": "ms-1"},
                 next_path="/?tab=audits")
    check("a listed Microsoft user signs in and lands where they were going",
          r.status_code == 303 and r.headers["location"] == "/?tab=audits", r.headers.get("location"))
    check("  ...the token exchange carried the PKCE verifier and secret",
          LAST_TOKEN_POST.get("code_verifier") and LAST_TOKEN_POST.get("client_secret") == "mssecret")
    check("  ...a session cookie is set", A.SESSION_COOKIE in cl.cookies)
    r = cl.get("/", follow_redirects=False)
    check("the terminal now renders", r.status_code == 200 and "account-card" in r.text)
    r = cl.get("/login", follow_redirects=False)
    check("  ...and the login page sends a signed-in person home",
          r.status_code == 303 and r.headers["location"] == "/")
    r = cl.get("/api/me")
    me = r.json()
    check("/api/me knows them", me["user"]["email"] == "nick@telescopescanada.ca"
          and me["user"]["name"] == "Nick" and me["auth_enabled"], me)
    with Session(get_engine()) as s:
        row = s.scalar(select(RfidUser).where(RfidUser.email == "nick@telescopescanada.ca"))
        check("  ...the row records the provider, subject and last seen",
              row.provider == "microsoft" and row.subject == "ms-1"
              and row.last_seen_at is not None)

    # Google: the verified flag matters, and the name fills a blank row.
    r = do_login(cl, "google", {"email": "alex@gmail.com", "name": "Alex",
                                "sub": "g-9", "email_verified": False})
    check("an unverified Google email is refused",
          "err=unverified" in r.headers.get("location", ""), r.headers.get("location"))
    r = do_login(cl, "google", {"email": "alex@gmail.com", "name": "Alex",
                                "sub": "g-9", "email_verified": True})
    check("a verified listed Google user signs in", r.headers.get("location") == "/")
    me = cl.get("/api/me").json()
    check("  ...the provider's name fills a blank row", me["user"]["name"] == "Alex", me)

    r = cl.get("/api/users")
    check("the cookie alone authorizes API calls", r.status_code == 200)
    r = cl.post("/api/users", json={"email": "kevin@telescopescanada.ca", "name": "Kevin"})
    check("adding stamps the signed-in name (not the body's)",
          r.json()["user"]["added_by"] == "Alex", r.text)
    r = cl.delete(f"/api/users/{alex_id}")
    check("you can't remove yourself", r.status_code == 409)
    kevin_id = [u["id"] for u in cl.get("/api/users").json()["users"]
                if u["email"] == "kevin@telescopescanada.ca"][0]
    r = cl.patch(f"/api/users/{kevin_id}", json={"name": "Kev"})
    check("renaming works", r.status_code == 200 and r.json()["user"]["name"] == "Kev")
    r = cl.delete(f"/api/users/{kevin_id}")
    check("removing works", r.status_code == 200)
    r = cl.get("/api/users/names", headers={"X-Station-Key": "gunkey"})
    check("the gun's names list is just names",
          r.json()["names"] == ["Alex", "Nick"], r.json())
    with Session(get_engine()) as s:
        kinds = [c.changed_field for c in s.scalars(select(BarcodeChange))]
    check("History has the add, rename and remove rows",
          kinds.count("user-added") == 3 and "user-renamed" in kinds
          and "user-removed" in kinds, kinds)

    # Removal cuts a live session off.
    nick_id = [u["id"] for u in cl.get("/api/users").json()["users"]
               if u["email"] == "nick@telescopescanada.ca"][0]
    alex_cookie = cl.cookies.get(A.SESSION_COOKIE)
    do_login(cl, "microsoft", {"email": "nick@telescopescanada.ca", "sub": "ms-1"})
    r = cl.delete(f"/api/users/{alex_id}")
    check("Nick removes Alex", r.status_code == 200, r.text)
    cl.cookies.set(A.SESSION_COOKIE, alex_cookie)
    r = cl.get("/api/users")
    check("  ...and Alex's cookie stops working at once", r.status_code == 401)

    r = cl.post("/auth/logout", follow_redirects=False)
    check("sign out clears the cookie",
          r.status_code == 303 and "rfid_session=" in r.headers.get("set-cookie", "")
          and ("Max-Age=0" in r.headers.get("set-cookie", "")
               or "expires" in r.headers.get("set-cookie", "").lower()), r.headers.get("set-cookie"))

    # Shopify admin: the session token's staff id links to a user once.
    cl.cookies.clear()
    tok = shopify_token("staff-77")
    r = cl.get("/api/me", headers={"Authorization": f"Bearer {tok}"})
    check("inside Shopify an unlinked staff member is offered the pick",
          r.json()["user"] is None and r.json()["shopify_sub"] == "staff-77", r.json())
    r = cl.get("/", params={"host": "abc"}, follow_redirects=False)
    check("  ...and the embedded page still renders (no cookie needed)", r.status_code == 200)
    r = cl.post("/api/me/link-shopify", json={"user_id": nick_id},
                headers={"Authorization": f"Bearer {tok}"})
    check("linking the staff id works", r.status_code == 200 and r.json()["user"]["shopify_linked"])
    r = cl.get("/api/me", headers={"Authorization": f"Bearer {tok}"})
    check("  ...and /api/me now knows them", r.json()["user"]["email"] == "nick@telescopescanada.ca")
    r = cl.post("/api/me/link-shopify", json={"user_id": nick_id})
    check("  ...linking without a Shopify token is refused", r.status_code == 401)

print()
if fails:
    print(f"{len(fails)} FAILED"); sys.exit(1)
print("ALL PASS")
