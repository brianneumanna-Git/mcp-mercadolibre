"""Servidor MCP remoto (HTTP) para Vercel.

Rutas:
  POST /mcp            Endpoint MCP (JSON-RPC sobre HTTP). Exige "Authorization: Bearer <MCP_BEARER_TOKEN>".
  GET  /auth/start     Inicia la autorización con Mercado Libre. Exige ?key=<AUTH_SETUP_KEY>.
  GET  /auth/callback  Vuelta de Mercado Libre (guarda los tokens en Supabase).
  GET  /health         Estado básico, sin datos sensibles.

Sin sesiones ni estado en memoria: cada solicitud es independiente (apto para serverless).
"""
import asyncio
import hmac
import html
import json
import os

import anyio
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import ml_tools
import oauth_flow
from ml_client import MLError

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

SUPPORTED_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]
SERVER_INFO = {"name": "mercadolibre-chile", "version": "2.0.0"}


# ------------------------------------------------------------ seguridad
def _secret_ok(provided: str, env_name: str, min_len: int) -> bool:
    expected = os.environ.get(env_name, "")
    if len(expected) < min_len:  # sin secreto configurado -> se rechaza todo
        return False
    return hmac.compare_digest(provided.encode(), expected.encode())


def _authorized(request: Request) -> bool:
    header = request.headers.get("authorization", "")
    return header.startswith("Bearer ") and _secret_ok(header[7:], "MCP_BEARER_TOKEN", 24)


# ------------------------------------------------------------- JSON-RPC
def _result_text(res) -> str:
    """Convierte el resultado de FastMCP en UN solo texto.

    FastMCP entrega una lista de herramienta como varios bloques (uno por elemento); si todos
    son JSON, se reúnen en una lista JSON válida en vez de textos pegados."""
    if isinstance(res, tuple):  # (contenido, estructurado)
        content, structured = res
        if isinstance(structured, dict) and set(structured) == {"result"}:
            return json.dumps(structured["result"], ensure_ascii=False, default=str)
        res = content
    if isinstance(res, dict):
        return json.dumps(res, ensure_ascii=False, default=str)
    parts = [getattr(block, "text", None) or json.dumps(block, default=str) for block in res]
    if len(parts) == 1:
        return parts[0]
    try:
        return json.dumps([json.loads(p) for p in parts], ensure_ascii=False)
    except ValueError:
        return "\n".join(parts)


async def _handle(msg: dict):
    method = msg.get("method")
    mid = msg.get("id")
    params = msg.get("params") or {}
    if mid is None:  # notificación: no se responde
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    if method == "initialize":
        asked = params.get("protocolVersion")
        version = asked if asked in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
        return ok(
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            }
        )
    if method == "ping":
        return ok({})
    if method == "tools/list":
        tools = await ml_tools.mcp.list_tools()
        return ok(
            {
                "tools": [
                    {"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
                    for t in tools
                ]
            }
        )
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            res = await ml_tools.mcp.call_tool(name, args)
            return ok({"content": [{"type": "text", "text": _result_text(res)}], "isError": False})
        except Exception as e:  # los errores de herramienta se devuelven como resultado
            return ok({"content": [{"type": "text", "text": str(e)}], "isError": True})
    return err(-32601, f"Método no soportado: {method}")


def _process(payload):
    """Se ejecuta en un hilo: las herramientas usan httpx síncrono."""
    if isinstance(payload, list):
        outs = [asyncio.run(_handle(m)) for m in payload if isinstance(m, dict)]
        outs = [o for o in outs if o is not None]
        return outs or None
    if isinstance(payload, dict):
        return asyncio.run(_handle(payload))
    return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Solicitud inválida"}}


@app.post("/mcp")
async def mcp_post(request: Request):
    if not _authorized(request):
        return JSONResponse(
            {"error": "no autorizado"}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
        )
    try:
        payload = json.loads(await request.body())
    except ValueError:
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON inválido"}},
            status_code=400,
        )
    out = await anyio.to_thread.run_sync(_process, payload)
    if out is None:
        return Response(status_code=202)
    return JSONResponse(out)


@app.api_route("/mcp", methods=["GET", "DELETE"])
async def mcp_other(request: Request):
    # Sin streams SSE ni sesiones: este servidor es "stateless".
    return Response(status_code=405, headers={"Allow": "POST"})


# ----------------------------------------------------- autorización OAuth
def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    page = (
        "<!doctype html><html lang='es'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font-family:system-ui,sans-serif;max-width:32rem;margin:4rem auto;padding:0 1rem}</style>"
        f"</head><body><h1>{html.escape(title)}</h1><p>{html.escape(body)}</p></body></html>"
    )
    return HTMLResponse(page, status_code=status)


@app.get("/auth/start")
def auth_start(key: str = ""):
    if not _secret_ok(key, "AUTH_SETUP_KEY", 16):
        return Response(status_code=404)  # no revelar que la ruta existe
    try:
        return RedirectResponse(oauth_flow.start(), status_code=302)
    except MLError as e:
        return _page("No se pudo iniciar", str(e), 500)


@app.get("/auth/callback")
def auth_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        return _page("Autorización cancelada", f"Mercado Libre respondió: {error}", 400)
    try:
        info = oauth_flow.finish(code, state)
    except MLError as e:
        return _page("No se pudo autorizar", str(e), 400)
    return _page(
        "Cuenta autorizada",
        f"Cuenta {info.get('user_id')} conectada. Ya puedes cerrar esta ventana y usar el MCP desde Claude.",
    )


@app.get("/cron/keepalive")
def cron_keepalive(request: Request):
    """Tarea diaria de Vercel Cron (ver vercel.json). Vercel envía Authorization: Bearer <CRON_SECRET>.
    1) Consulta Supabase: el plan gratuito pausa proyectos sin actividad.
    2) Renueva el token de Mercado Libre si está por vencer, manteniendo viva la cadena de refresh."""
    header = request.headers.get("authorization", "")
    if not (header.startswith("Bearer ") and _secret_ok(header[7:], "CRON_SECRET", 16)):
        return Response(status_code=401)
    estado = {"supabase": "ok", "mercadolibre": "ok"}
    try:
        from ml_client import MLClient, get_store

        get_store().load()
        MLClient().access_token()
    except MLError as e:
        msg = str(e)
        if msg.startswith("Supabase"):
            estado["supabase"] = "error"
        estado["mercadolibre"] = "sin autorizar" if "no está autorizada" in msg else "error"
    except Exception:  # noqa: BLE001 - nunca exponer detalles
        estado["supabase"] = "error"
    return estado


@app.get("/health")
def health():
    return {"servicio": "mercadolibre-mcp", "estado": "ok"}


@app.get("/")
def root():
    return {"servicio": "mercadolibre-mcp", "estado": "ok"}
