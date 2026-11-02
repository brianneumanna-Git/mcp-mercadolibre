"""Autorización inicial para USO LOCAL (en la nube usa /auth/start?key=...).
Guarda los tokens en Supabase si SUPABASE_URL y SUPABASE_SERVICE_KEY están en .env; si no, en tokens.json.

Uso:  python auth_setup.py
1) Abre la URL que se imprime, inicia sesión con la cuenta DUEÑA del negocio y acepta.
2) Mercado Libre te redirige a tu Redirect URI con ?code=... en la barra de direcciones
   (aunque la página muestre un error 404, no importa).
3) Copia la URL completa de la barra y pégala aquí.
"""
import base64
import hashlib
import os
import secrets
import sys
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from ml_client import API, MLError, store_token_response  # carga .env


def main() -> None:
    cid = os.environ.get("ML_CLIENT_ID")
    sec = os.environ.get("ML_CLIENT_SECRET")
    redirect = os.environ.get("ML_REDIRECT_URI")
    auth_host = os.environ.get("ML_AUTH_HOST", "https://auth.mercadolibre.cl")
    use_pkce = os.environ.get("ML_USE_PKCE", "1") == "1"
    if not (cid and sec and redirect):
        sys.exit("Completa ML_CLIENT_ID, ML_CLIENT_SECRET y ML_REDIRECT_URI en el archivo .env")

    state = secrets.token_urlsafe(16)
    params = {
        "response_type": "code",
        "client_id": cid,
        "redirect_uri": redirect,
        "state": state,
    }
    verifier = None
    if use_pkce:
        verifier = secrets.token_urlsafe(64)[:96]
        digest = hashlib.sha256(verifier.encode()).digest()
        params["code_challenge"] = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        params["code_challenge_method"] = "S256"

    print("\n1) Abre esta URL en tu navegador (con la cuenta dueña del negocio):\n")
    print(f"{auth_host}/authorization?{urlencode(params)}\n")
    pasted = input("2) Pega aquí la URL completa a la que te redirigió: ").strip()

    q = parse_qs(urlparse(pasted).query)
    code = q.get("code", [pasted])[0]
    if "state" in q and q["state"][0] != state:
        sys.exit("El 'state' no coincide. Vuelve a empezar (posible URL de otra sesión).")

    data = {
        "grant_type": "authorization_code",
        "client_id": cid,
        "client_secret": sec,
        "code": code,
        "redirect_uri": redirect,
    }
    if verifier:
        data["code_verifier"] = verifier

    r = httpx.post(
        f"{API}/oauth/token",
        headers={"accept": "application/json", "content-type": "application/x-www-form-urlencoded"},
        data=data,
        timeout=30,
    )
    if r.status_code != 200:
        sys.exit(f"Error {r.status_code}: {r.text}\n(El code vale pocos minutos y sirve una sola vez.)")

    tokens = store_token_response(r.json())
    scope = tokens.get("scope") or ""
    print(f"\nListo. Cuenta (user_id): {tokens['user_id']} | scopes: {scope}")
    if "offline_access" not in scope:
        print("ATENCIÓN: falta 'offline_access'; el token no se podrá renovar solo.")
    print("Tokens guardados en tokens.json. Ya puedes iniciar el servidor MCP.")


if __name__ == "__main__":
    try:
        main()
    except MLError as e:
        sys.exit(str(e))
