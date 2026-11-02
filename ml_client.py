"""Cliente de la API de Mercado Libre con manejo automático de tokens.

Dos almacenes de tokens, elegidos por variables de entorno:
- SupabaseStore (nube): si existen SUPABASE_URL y SUPABASE_SERVICE_KEY.
- FileStore (local): tokens.json junto al código.

El access token dura 6 horas. El refresh token es de UN SOLO USO: cada renovación
entrega uno nuevo que se guarda de inmediato. En la nube pueden llegar varias
solicitudes a la vez, así que la renovación usa un "arrendamiento" (lease) en la
fila de Supabase: solo una solicitud renueva, las demás esperan y releen el token.
"""
import json
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

API = "https://api.mercadolibre.com"
_FORM = {"accept": "application/json", "content-type": "application/x-www-form-urlencoded"}


class MLError(Exception):
    """Error al hablar con la API de Mercado Libre o con el almacén de tokens."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


REAUTH_HINT = (
    "Vuelve a autorizar la cuenta abriendo /auth/start?key=... en tu servidor de Vercel "
    "(o, en uso local, con: python auth_setup.py)."
)


# ---------------------------------------------------------------- utilidades
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(value: str) -> float:
    value = re.sub(r"\.\d+", "", value).replace("Z", "+00:00")
    return datetime.fromisoformat(value).timestamp()


# ------------------------------------------------------------------ HTTP
_HTTP: httpx.Client | None = None


def get_http() -> httpx.Client:
    global _HTTP
    if _HTTP is None:
        _HTTP = httpx.Client(timeout=30)
    return _HTTP


def set_http(client: httpx.Client | None) -> None:
    """Solo para pruebas."""
    global _HTTP
    _HTTP = client


def token_request(data: dict) -> httpx.Response:
    return get_http().post(f"{API}/oauth/token", headers=_FORM, data=data)


# -------------------------------------------------------------- almacenes
class FileStore:
    """Tokens en un archivo local (uso en tu computador)."""

    kind = "archivo"

    def __init__(self, path=None, log_path=None):
        self.path = Path(path or os.environ.get("ML_TOKEN_FILE", BASE_DIR / "tokens.json"))
        self.log_path = Path(log_path or BASE_DIR / "acciones.jsonl")
        self._tlock = threading.Lock()

    def load(self) -> dict:
        if not self.path.exists():
            raise MLError("No hay tokens guardados. " + REAUTH_HINT)
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, data: dict) -> None:
        tmp = tempfile.NamedTemporaryFile(
            "w", delete=False, dir=self.path.parent, suffix=".tmp", encoding="utf-8"
        )
        try:
            json.dump(data, tmp)
            tmp.close()
            try:
                os.chmod(tmp.name, 0o600)
            except OSError:
                pass
            os.replace(tmp.name, self.path)
        except Exception:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            raise

    def acquire_lease(self) -> bool:
        return self._tlock.acquire(blocking=False)

    def release_lease(self) -> None:
        try:
            self._tlock.release()
        except RuntimeError:
            pass

    def log(self, entry: dict) -> None:
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


class SupabaseStore:
    """Tokens en Supabase (PostgREST). Usa la service key SOLO en el servidor."""

    kind = "supabase"

    def __init__(self, url: str, key: str, transport=None):
        self.base = url.rstrip("/") + "/rest/v1"
        self.h = {"apikey": key, "Content-Type": "application/json"}
        # Las claves nuevas (sb_secret_...) NO son JWT: van solo en "apikey"; si se envían
        # en Authorization: Bearer, Supabase intenta leerlas como JWT y rechaza la solicitud.
        # La clave antigua service_role (JWT, empieza con "eyJ") sí va también en Authorization.
        if key.startswith("eyJ"):
            self.h["Authorization"] = f"Bearer {key}"
        self.http = httpx.Client(timeout=20, transport=transport)

    @classmethod
    def from_env(cls):
        return cls(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

    # -- helpers
    def _check(self, r: httpx.Response) -> httpx.Response:
        if r.status_code >= 400:
            raise MLError(f"Supabase {r.status_code}: {r.text[:300]}")
        return r

    def _row(self) -> dict:
        r = self._check(
            self.http.get(
                f"{self.base}/ml_tokens", params={"id": "eq.1", "select": "*"}, headers=self.h
            )
        )
        rows = r.json()
        return rows[0] if rows else {}

    def _patch(self, body: dict, extra_params=None, representation=False) -> httpx.Response:
        params = {"id": "eq.1", **(extra_params or {})}
        prefer = "return=representation" if representation else "return=minimal"
        return self._check(
            self.http.patch(
                f"{self.base}/ml_tokens",
                params=params,
                json=body,
                headers={**self.h, "Prefer": prefer},
            )
        )

    # -- tokens
    def load(self) -> dict:
        row = self._row()
        if not row.get("access_token"):
            raise MLError("La cuenta de Mercado Libre aún no está autorizada. " + REAUTH_HINT)
        return {
            "access_token": row["access_token"],
            "refresh_token": row["refresh_token"],
            "expires_at": _parse_ts(row["expires_at"]),
            "user_id": row["user_id"],
            "scope": row.get("scope"),
        }

    def save(self, data: dict) -> None:
        self._patch(
            {
                "access_token": data["access_token"],
                "refresh_token": data["refresh_token"],
                "expires_at": _iso(datetime.fromtimestamp(data["expires_at"], timezone.utc)),
                "user_id": data.get("user_id"),
                "scope": data.get("scope"),
                "lock_until": None,
                "updated_at": _iso(_utcnow()),
            }
        )

    # -- arrendamiento para renovar el refresh token una sola vez
    def acquire_lease(self, seconds: int = 30) -> bool:
        now = _utcnow()
        # El valor va entre comillas dobles: PostgREST lo exige para valores con caracteres
        # reservados dentro de or=(...), y es válido para cualquier valor.
        r = self._patch(
            {"lock_until": _iso(now + timedelta(seconds=seconds))},
            extra_params={"or": f'(lock_until.is.null,lock_until.lt."{_iso(now)}")'},
            representation=True,
        )
        return bool(r.json())

    def release_lease(self) -> None:
        self._patch({"lock_until": None})

    # -- bitácora de escrituras
    def log(self, entry: dict) -> None:
        self._check(
            self.http.post(
                f"{self.base}/ml_actions",
                json={
                    "tool": entry["herramienta"],
                    "request": entry["request"],
                    "result": entry["resultado"],
                },
                headers={**self.h, "Prefer": "return=minimal"},
            )
        )

    # -- autorización OAuth desde el navegador
    def set_pending(self, state: str, verifier: str | None) -> None:
        self._patch(
            {"pending_state": state, "pending_verifier": verifier, "pending_at": _iso(_utcnow())}
        )

    def get_pending(self) -> dict:
        row = self._row()
        at = row.get("pending_at")
        return {
            "state": row.get("pending_state"),
            "verifier": row.get("pending_verifier"),
            "at": _parse_ts(at) if at else 0,
        }

    def clear_pending(self) -> None:
        self._patch({"pending_state": None, "pending_verifier": None, "pending_at": None})


_store = None


def get_store():
    global _store
    if _store is None:
        if os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY"):
            _store = SupabaseStore.from_env()
        else:
            _store = FileStore()
    return _store


def set_store(store) -> None:
    """Solo para pruebas."""
    global _store
    _store = store


def store_token_response(resp: dict) -> dict:
    data = {
        "access_token": resp["access_token"],
        "refresh_token": resp["refresh_token"],
        "expires_at": time.time() + int(resp.get("expires_in", 21600)),
        "user_id": resp.get("user_id"),
        "scope": resp.get("scope"),
    }
    get_store().save(data)
    return data


def log_action(tool: str, request: dict, result) -> None:
    """Bitácora de todo lo que el MCP escribe en tu cuenta."""
    text = json.dumps(result, default=str)
    entry = {
        "fecha": _utcnow().isoformat(),
        "herramienta": tool,
        "request": request,
        "resultado": result if len(text) < 4000 else {"omitido": "respuesta muy larga"},
    }
    try:
        get_store().log(entry)
    except Exception:
        # Un fallo de bitácora no debe ocultar que la escritura sí ocurrió.
        pass


# ---------------------------------------------------------------- cliente
class MLClient:
    def __init__(self) -> None:
        self.site = os.environ.get("ML_SITE", "MLC")
        self.read_only = os.environ.get("ML_READ_ONLY", "0") == "1"
        self._uid: int | None = None

    def _creds(self):
        cid = os.environ.get("ML_CLIENT_ID")
        sec = os.environ.get("ML_CLIENT_SECRET")
        if not cid or not sec:
            raise MLError("Faltan ML_CLIENT_ID / ML_CLIENT_SECRET en las variables de entorno.")
        return cid, sec

    def _refresh(self, tokens: dict) -> dict:
        cid, sec = self._creds()
        r = token_request(
            {
                "grant_type": "refresh_token",
                "client_id": cid,
                "client_secret": sec,
                "refresh_token": tokens["refresh_token"],
            }
        )
        if r.status_code != 200:
            raise MLError(
                f"No se pudo renovar el token ({r.status_code}): {r.text[:300]}. " + REAUTH_HINT,
                status=r.status_code,
            )
        return store_token_response(r.json())

    @staticmethod
    def _usable(tokens: dict, bad: str | None) -> bool:
        if bad:
            return tokens["access_token"] != bad  # otro ya lo renovó
        return tokens["expires_at"] - time.time() > 120

    def access_token(self, bad: str | None = None) -> str:
        store = get_store()
        for _ in range(8):
            tokens = store.load()
            if self._usable(tokens, bad):
                return tokens["access_token"]
            if store.acquire_lease():
                try:
                    tokens = store.load()  # releer: pudo renovarse mientras esperábamos
                    if self._usable(tokens, bad):
                        return tokens["access_token"]
                    return self._refresh(tokens)["access_token"]
                finally:
                    store.release_lease()
            time.sleep(1.0)
        raise MLError("No se pudo renovar el token (otra solicitud lo está renovando). Reintenta.")

    @property
    def user_id(self) -> int:
        if self._uid is None:
            self._uid = int(get_store().load()["user_id"])
        return self._uid

    def request(self, method, path, params=None, body=None, api_version=None):
        if not path.startswith("/"):
            raise MLError("La ruta debe comenzar con '/' (ej: /users/me).")
        if method != "GET" and self.read_only:
            raise MLError("Modo solo lectura activo (ML_READ_ONLY=1): escritura bloqueada.")

        token = self.access_token()
        refreshed = False
        r = None
        for attempt in range(5):
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            if api_version:
                headers["api-version"] = str(api_version)
            r = get_http().request(
                method, f"{API}{path}", params=params, json=body, headers=headers
            )
            if r.status_code == 401 and not refreshed:
                refreshed = True
                token = self.access_token(bad=token)
                continue
            if r.status_code in (429, 503) and attempt < 4:
                time.sleep(min(2**attempt, 10))
                continue
            break

        if r.status_code >= 400:
            hint = ""
            if r.status_code == 403:
                hint = (
                    " | 403: revisa los permisos funcionales/scopes de la app en el "
                    "DevCenter, o el recurso puede estar restringido por política de ML."
                )
            raise MLError(f"{method} {path} -> {r.status_code}: {r.text[:600]}{hint}", status=r.status_code)
        if not r.content:
            return {}
        try:
            return r.json()
        except ValueError:
            return {"raw": r.text[:2000]}

    def get(self, path, params=None, api_version=None):
        return self.request("GET", path, params=params, api_version=api_version)
