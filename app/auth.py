"""Access control.

Three ways in, matching the three kinds of users (sign-in added
2026-10-05, Nick: the terminal asks who you are before it shows
anything):

1. Signed-in people (browser outside Shopify): "Sign in with Microsoft"
   for telescopescanada.ca accounts (the company mail is Microsoft 365)
   or "Sign in with Google" for everyone else - plain OpenID Connect
   authorization-code flow with PKCE, no extra dependencies. Only emails
   on the users list (rfid_users) get in; a signed session cookie then
   carries who they are for 30 days.
2. Shopify admin (embedded app): App Bridge attaches a session-token JWT
   to every request. We verify it ourselves - HS256 signed with the app's
   client secret, audience = client id. The token's staff id (sub) can be
   linked to a users-list row so History knows the name.
3. Devices (the C72 gun and the print agent): a shared STATION_KEY sent as
   an X-Station-Key header. Browsers used to bootstrap the same key from a
   ?key=... URL; that door closes once sign-in is configured.

Enforcement is on when STATION_KEY or a sign-in provider is configured,
so local development with a bare .env keeps working with no ceremony.

Why no JWT library: the id_token arrives over the direct TLS call to the
provider's token endpoint, which OpenID Connect (3.1.3.7 step 6) accepts
in place of a signature check. We still check issuer, audience, nonce
and expiry.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlencode

import requests
from fastapi import Header, HTTPException, Request

from app import config

SESSION_COOKIE = "rfid_session"
OAUTH_COOKIE = "rfid_oauth"
SESSION_DAYS = 30


def _b64url_decode(chunk: str) -> bytes:
    return base64.urlsafe_b64decode(chunk + "=" * (-len(chunk) % 4))


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


# ---- Shopify session tokens --------------------------------------------------

def session_token_payload(token: str) -> dict | None:
    """The claims of a valid Shopify App Bridge session token (JWT,
    HS256), or None when it fails any check."""
    if not (config.SHOPIFY_CLIENT_ID and config.SHOPIFY_CLIENT_SECRET):
        return None
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
        expected = hmac.new(
            config.SHOPIFY_CLIENT_SECRET.encode(),
            f"{header_b64}.{payload_b64}".encode(),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(expected, _b64url_decode(sig_b64)):
            return None
        payload = json.loads(_b64url_decode(payload_b64))
        if payload.get("aud") != config.SHOPIFY_CLIENT_ID:
            return None
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except Exception:
        return None


def verify_session_token(token: str) -> bool:
    """Verify a Shopify App Bridge session token (JWT, HS256)."""
    return session_token_payload(token) is not None


# ---- signed cookies ----------------------------------------------------------

def _sign(data: dict) -> str:
    body = _b64url_encode(json.dumps(data, separators=(",", ":")).encode())
    mac = hmac.new(
        (config.AUTH_SESSION_SECRET or "").encode(), body.encode(),
        hashlib.sha256,
    ).digest()
    return body + "." + _b64url_encode(mac)


def _unsign(value: str | None) -> dict | None:
    if not value or not config.AUTH_SESSION_SECRET or "." not in value:
        return None
    body, sig = value.rsplit(".", 1)
    mac = hmac.new(
        config.AUTH_SESSION_SECRET.encode(), body.encode(), hashlib.sha256
    ).digest()
    try:
        if not hmac.compare_digest(mac, _b64url_decode(sig)):
            return None
        data = json.loads(_b64url_decode(body))
    except Exception:
        return None
    if not isinstance(data, dict) or data.get("exp", 0) < time.time():
        return None
    return data


def session_cookie_value(user_id: int, email: str, name: str | None) -> str:
    return _sign({
        "uid": int(user_id), "email": email, "name": name or "",
        "exp": int(time.time()) + SESSION_DAYS * 86400,
    })


def cookie_secure(request: Request) -> bool:
    """Secure cookies everywhere except a localhost dev server (the
    browser only sends Secure cookies over https; prod sits behind
    Azure's TLS, so the browser side IS https even when the container
    sees http)."""
    host = (request.headers.get("host") or "").split(":")[0].lower()
    return host not in ("localhost", "127.0.0.1", "[::1]")


def current_user(request: Request) -> dict | None:
    """Who the session cookie says this is: {uid, email, name}, or None.
    Removed users are cut off by the per-request row check in
    require_user, so a stale cookie alone never counts."""
    if not auth_enabled():
        return None
    data = _unsign(request.cookies.get(SESSION_COOKIE))
    if not data or "uid" not in data:
        return None
    if not _user_still_listed(int(data["uid"])):
        return None
    return {"uid": int(data["uid"]), "email": data.get("email", ""),
            "name": data.get("name", "")}


# A removed user's cookie must stop working, but 200+ endpoints asking
# the database "does this user still exist?" on every call is waste:
# the answer is cached a minute, and removals clear it outright.
_listed_cache: dict[int, tuple[bool, float]] = {}
_LISTED_TTL = 60.0


def _user_still_listed(uid: int) -> bool:
    now = time.time()
    hit = _listed_cache.get(uid)
    if hit and hit[1] > now:
        return hit[0]
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from app.database import get_engine
    from app.models import RfidUser
    try:
        with Session(get_engine()) as s:
            ok = s.scalar(
                select(RfidUser.id).where(RfidUser.id == uid)
            ) is not None
    except Exception:  # noqa: BLE001 - a DB blip must not log everyone out
        ok = hit[0] if hit else True
    _listed_cache[uid] = (ok, now + _LISTED_TTL)
    return ok


def forget_user(uid: int) -> None:
    _listed_cache.pop(uid, None)


# ---- the guard ---------------------------------------------------------------

def auth_enabled() -> bool:
    return config.auth_enabled()


def enforced() -> bool:
    return bool(config.STATION_KEY) or auth_enabled()


def require_user(
    request: Request,
    authorization: str | None = Header(default=None),
    x_station_key: str | None = Header(default=None),
) -> None:
    """FastAPI dependency guarding the app's routes."""
    if not enforced():
        return  # enforcement off (local development)

    user = current_user(request)
    if user:
        request.state.user = user
        return
    if config.STATION_KEY and x_station_key and hmac.compare_digest(
            x_station_key, config.STATION_KEY):
        return
    # The UI used to bootstrap its stored key from ?key= on page loads;
    # with sign-in configured, people sign in instead.
    url_key = request.query_params.get("key")
    if (config.STATION_KEY and url_key and not auth_enabled()
            and hmac.compare_digest(url_key, config.STATION_KEY)):
        return
    if authorization and authorization.startswith("Bearer "):
        if verify_session_token(authorization.removeprefix("Bearer ")):
            return
    raise HTTPException(
        401,
        "Sign in to use the RFID terminal." if auth_enabled() else
        "Not authorized. Open the app from Shopify admin, or use the "
        "station link with the access key.",
    )


def actor_name(request: Request, fallback: str | None = None) -> str | None:
    """The signed-in user's name for History rows, else the caller's
    own say-so (the gun's picked worker, the embedded app's linked
    name)."""
    user = getattr(request.state, "user", None)
    if user and user.get("name"):
        return user["name"]
    return fallback


# ---- OpenID Connect ----------------------------------------------------------

_PROVIDER_META = {
    "microsoft": {
        "label": "Sign in with Microsoft",
        "hint": "telescopescanada.ca accounts",
    },
    "google": {
        "label": "Sign in with Google",
        "hint": "Gmail and other Google accounts",
    },
}


def providers() -> dict[str, dict]:
    """The configured providers, name -> {label, hint, client_id,
    client_secret, discovery}."""
    out: dict[str, dict] = {}
    if config.MS_CLIENT_ID and config.MS_CLIENT_SECRET:
        tenant = config.MS_TENANT_ID or "organizations"
        out["microsoft"] = {
            **_PROVIDER_META["microsoft"],
            "client_id": config.MS_CLIENT_ID,
            "client_secret": config.MS_CLIENT_SECRET,
            "discovery": (
                f"https://login.microsoftonline.com/{tenant}/v2.0/"
                ".well-known/openid-configuration"
            ),
        }
    if config.GOOGLE_CLIENT_ID and config.GOOGLE_CLIENT_SECRET:
        out["google"] = {
            **_PROVIDER_META["google"],
            "client_id": config.GOOGLE_CLIENT_ID,
            "client_secret": config.GOOGLE_CLIENT_SECRET,
            "discovery": (
                "https://accounts.google.com/.well-known/openid-configuration"
            ),
        }
    return out


_discovery_cache: dict[str, dict] = {}


def _endpoints(name: str) -> dict:
    """authorization_endpoint / token_endpoint / issuer from the
    provider's discovery document, cached for the process."""
    hit = _discovery_cache.get(name)
    if hit:
        return hit
    prov = providers().get(name)
    if not prov:
        raise HTTPException(404, "That sign-in provider isn't configured.")
    try:
        doc = requests.get(prov["discovery"], timeout=10).json()
        found = {
            "authorization_endpoint": doc["authorization_endpoint"],
            "token_endpoint": doc["token_endpoint"],
            "issuer": doc["issuer"],
        }
    except Exception as error:  # noqa: BLE001
        raise HTTPException(
            502, f"Couldn't reach the {name} sign-in service: {error}"
        )
    _discovery_cache[name] = found
    return found


def _token_post(url: str, data: dict) -> dict:
    resp = requests.post(url, data=data, timeout=15)
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        body = {}
    if resp.status_code != 200 or "id_token" not in body:
        raise HTTPException(
            502, "The sign-in service refused the login: "
            + str(body.get("error_description") or body.get("error")
                  or resp.status_code),
        )
    return body


def callback_url(request: Request, name: str) -> str:
    host = request.headers.get("host") or "localhost"
    scheme = "http" if not cookie_secure(request) else "https"
    return f"{scheme}://{host}/auth/callback/{name}"


def begin_login(request: Request, name: str, next_path: str) -> tuple[str, str]:
    """(authorize URL to redirect to, the state cookie value to set)."""
    prov = providers().get(name)
    if not prov:
        raise HTTPException(404, "That sign-in provider isn't configured.")
    ep = _endpoints(name)
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = _b64url_encode(hashlib.sha256(verifier.encode()).digest())
    if not next_path.startswith("/") or next_path.startswith("//"):
        next_path = "/"
    params = {
        "client_id": prov["client_id"],
        "response_type": "code",
        "scope": "openid email profile",
        "redirect_uri": callback_url(request, name),
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if name == "google":
        params["prompt"] = "select_account"
    cookie = _sign({
        "p": name, "s": state, "n": nonce, "v": verifier,
        "next": next_path, "exp": int(time.time()) + 600,
    })
    return ep["authorization_endpoint"] + "?" + urlencode(params), cookie


def finish_login(request: Request, name: str) -> dict:
    """Exchange the callback's code for the person's identity:
    {email, name, subject, next}. Raises HTTPException with a short
    reason code in .detail on any failure."""
    saved = _unsign(request.cookies.get(OAUTH_COOKIE))
    state = request.query_params.get("state")
    code = request.query_params.get("code")
    if request.query_params.get("error"):
        raise HTTPException(400, "denied")
    if not saved or saved.get("p") != name or not state or not code \
            or not hmac.compare_digest(saved.get("s", ""), state):
        raise HTTPException(400, "state")
    prov = providers().get(name)
    if not prov:
        raise HTTPException(404, "provider")
    ep = _endpoints(name)
    body = _token_post(ep["token_endpoint"], {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": callback_url(request, name),
        "client_id": prov["client_id"],
        "client_secret": prov["client_secret"],
        "code_verifier": saved["v"],
    })
    try:
        claims = json.loads(_b64url_decode(body["id_token"].split(".")[1]))
    except Exception:  # noqa: BLE001
        raise HTTPException(502, "token")
    if claims.get("aud") != prov["client_id"]:
        raise HTTPException(400, "token")
    if claims.get("nonce") != saved["n"]:
        raise HTTPException(400, "token")
    if claims.get("exp", 0) < time.time() - 60:
        raise HTTPException(400, "token")
    iss = claims.get("iss", "")
    if name == "google":
        if iss not in ("https://accounts.google.com", "accounts.google.com"):
            raise HTTPException(400, "token")
        if not claims.get("email_verified", False):
            raise HTTPException(400, "unverified")
    elif not iss.startswith("https://login.microsoftonline.com/"):
        raise HTTPException(400, "token")
    email = (claims.get("email") or claims.get("preferred_username")
             or "").strip().lower()
    if "@" not in email:
        raise HTTPException(400, "noemail")
    return {
        "email": email,
        "name": (claims.get("name") or claims.get("given_name") or "").strip(),
        "subject": str(claims.get("sub") or ""),
        "next": saved.get("next") or "/",
    }
