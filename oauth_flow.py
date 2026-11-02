"""Autorización OAuth de Mercado Libre desde el navegador (para la versión en la nube).

1) El dueño abre /auth/start?key=AUTH_SETUP_KEY -> se genera un state (y PKCE) que se
   guarda en Supabase y se redirige a Mercado Libre.
2) Mercado Libre vuelve a /auth/callback?code=...&state=... -> se valida el state,
   se canjea el code por tokens y se guardan en Supabase. Los tokens nunca se muestran.
"""
import base64
import hashlib
import hmac
import os
import secrets
import time
from urllib.parse import urlencode

from ml_client import MLError, get_store, store_token_response, token_request


def _cfg():
    cid = os.environ.get("ML_CLIENT_ID")
    sec = os.environ.get("ML_CLIENT_SECRET")
    redirect = os.environ.get("ML_REDIRECT_URI")
    if not (cid and sec and redirect):
        raise MLError("Faltan ML_CLIENT_ID, ML_CLIENT_SECRET o ML_REDIRECT_URI en el servidor.")
    host = os.environ.get("ML_AUTH_HOST", "https://auth.mercadolibre.cl")
    pkce = os.environ.get("ML_USE_PKCE", "1") == "1"
    return cid, sec, redirect, host, pkce


def start() -> str:
    cid, _sec, redirect, host, pkce = _cfg()
    store = get_store()
    if not hasattr(store, "set_pending"):
        raise MLError("La autorización por navegador requiere Supabase (SUPABASE_URL y SUPABASE_SERVICE_KEY).")
    state = secrets.token_urlsafe(24)
    params = {"response_type": "code", "client_id": cid, "redirect_uri": redirect, "state": state}
    verifier = None
    if pkce:
        verifier = secrets.token_urlsafe(64)[:96]
        digest = hashlib.sha256(verifier.encode()).digest()
        params["code_challenge"] = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        params["code_challenge_method"] = "S256"
    store.set_pending(state, verifier)
    return f"{host}/authorization?{urlencode(params)}"


def finish(code: str | None, state: str | None) -> dict:
    cid, sec, redirect, _host, _pkce = _cfg()
    store = get_store()
    if not code or not state:
        raise MLError("Falta el código o el estado en la respuesta de Mercado Libre.")
    pending = store.get_pending()
    if not pending["state"] or not hmac.compare_digest(state, pending["state"]):
        raise MLError("Estado inválido o ya utilizado. Vuelve a abrir /auth/start.")
    if time.time() - pending["at"] > 600:
        raise MLError("La autorización expiró (más de 10 minutos). Vuelve a abrir /auth/start.")
    store.clear_pending()  # el state sirve una sola vez

    data = {
        "grant_type": "authorization_code",
        "client_id": cid,
        "client_secret": sec,
        "code": code,
        "redirect_uri": redirect,
    }
    if pending["verifier"]:
        data["code_verifier"] = pending["verifier"]
    r = token_request(data)
    if r.status_code != 200:
        raise MLError(f"Mercado Libre rechazó el código ({r.status_code}): {r.text[:200]}")

    resp = r.json()
    expected = os.environ.get("ML_EXPECTED_USER_ID")
    if expected and str(resp.get("user_id")) != expected:
        raise MLError("La cuenta autorizada no es la esperada (ML_EXPECTED_USER_ID). No se guardó nada.")
    return store_token_response(resp)
