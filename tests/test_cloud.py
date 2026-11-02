"""Pruebas de punta a punta con Supabase y Mercado Libre simulados (sin red)."""
import json
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

import app as app_mod
import ml_client
import ml_tools

BEARER = "b" * 40
SETUP = "s" * 24


# ------------------------------------------------------------ Supabase falso
class FakeSupabase:
    def __init__(self):
        self.row = {"id": 1}
        self.actions = []
        self.lock = threading.Lock()

    @staticmethod
    def _ts(v):
        return datetime.fromisoformat(re.sub(r"\.\d+", "", v).replace("Z", "+00:00"))

    def handler(self, req: httpx.Request) -> httpx.Response:
        assert req.headers["apikey"] == "service-key"
        path = req.url.path
        with self.lock:
            if path.endswith("/ml_tokens") and req.method == "GET":
                return httpx.Response(200, json=[dict(self.row)])
            if path.endswith("/ml_tokens") and req.method == "PATCH":
                cond = req.url.params.get("or")
                if cond:  # (lock_until.is.null,lock_until.lt.TS)
                    m = re.search(r'lock_until\.lt\."([^"]+)"', cond)
                    assert m, f"valor sin comillas en filtro or: {cond}"
                    ts = self._ts(m.group(1))
                    cur = self.row.get("lock_until")
                    if cur is not None and self._ts(cur) >= ts:
                        return httpx.Response(200, json=[])
                self.row.update(json.loads(req.content))
                rep = "representation" in req.headers.get("prefer", "")
                return httpx.Response(200, json=[dict(self.row)] if rep else None)
            if path.endswith("/ml_actions") and req.method == "POST":
                self.actions.append(json.loads(req.content))
                return httpx.Response(201)
        return httpx.Response(404, json={"msg": "ruta falsa"})


# -------------------------------------------------- Mercado Libre falso
class FakeML:
    def __init__(self):
        self.valid_access = set()
        self.valid_refresh = set()
        self.refresh_calls = 0
        self.writes = []
        self.multi_visits_fail = False
        self.no_suggestions = False
        self.n = 0
        self.lock = threading.Lock()

    def issue(self):
        self.n += 1
        a, r = f"APP_USR-{self.n}", f"TG-{self.n}"
        self.valid_access.add(a)
        self.valid_refresh.add(r)
        return {"access_token": a, "refresh_token": r, "expires_in": 21600,
                "user_id": 777, "scope": "offline_access read write"}

    def handler(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/oauth/token":
            form = parse_qs(req.content.decode())
            with self.lock:
                if form["grant_type"][0] == "authorization_code":
                    if form["code"][0] != "CODE-OK":
                        return httpx.Response(400, json={"error": "invalid_grant"})
                    if "code_verifier" not in form:
                        return httpx.Response(400, json={"error": "pkce requerido"})
                    return httpx.Response(200, json=self.issue())
                rt = form["refresh_token"][0]
                self.refresh_calls += 1
                time.sleep(0.05)
                if rt not in self.valid_refresh:  # un solo uso
                    return httpx.Response(400, json={"error": "invalid_grant"})
                self.valid_refresh.discard(rt)
                return httpx.Response(200, json=self.issue())
        tok = req.headers.get("authorization", "")[7:]
        if tok not in self.valid_access:
            return httpx.Response(401, json={"message": "invalid_token"})
        if path == "/users/me":
            return httpx.Response(200, json={"id": 777, "nickname": "EMPRESA_TEST", "country_id": "CL",
                                             "seller_reputation": {"level_id": "5_green"}})
        if path == "/billing/integration/monthly/periods":
            q = req.url.params
            assert q["group"] == "ML" and q["document_type"] == "BILL"
            return httpx.Response(200, json={"results": [
                {"key": "2026-11-01", "amount": 52000, "unpaid_amount": 52000, "period_status": "OPEN",
                 "period": {"date_from": "2026-10-19", "date_to": "2026-11-18"}, "expiration_date": None},
                {"key": "2026-10-01", "amount": 81000, "unpaid_amount": 0, "period_status": "CLOSED",
                 "period": {"date_from": "2026-09-19", "date_to": "2026-10-18"}, "expiration_date": "2026-10-24"}]})
        if path == "/billing/integration/periods/key/2026-11-01/summary/details":
            if req.url.params.get("document_type") != "BILL":  # la API real responde 422 sin este parámetro
                return httpx.Response(422, json={"message": "document_type is required"})
            return httpx.Response(200, json={
                "period": {"key": "2026-11-01"},
                "bill_includes": {"total_amount": 52000, "total_perceptions": 0,
                    "charges": [{"label": "Cargo por venta", "amount": 30000, "type": "CV"},
                                {"label": "Campañas de publicidad - Product Ads", "amount": 25000, "type": "PADS"}],
                    "bonuses": [{"label": "Bonificación del cargo por venta", "amount": 3000, "type": "BXD"}]},
                "payment_collected": {"total_collected": 0, "total_debt": 52000}, "errors": []})
        if path == "/orders/search":
            return httpx.Response(200, json={"paging": {"total": 3}, "results": [
                {"id": 1, "status": "paid", "date_created": "2026-10-20T10:00:00.000-03:00", "total_amount": 31980,
                 "shipping": {"id": 501}, "order_items": [
                    {"item": {"id": "MLCA", "title": "Kit A"}, "quantity": 2, "unit_price": 15990, "sale_fee": 2000}]},
                {"id": 2, "status": "paid", "date_created": "2026-10-21T10:00:00.000-03:00", "total_amount": 24990,
                 "shipping": {"id": 502}, "order_items": [
                    {"item": {"id": "MLCB", "title": "Kit B"}, "quantity": 1, "unit_price": 24990, "sale_fee": 3500}]},
                {"id": 3, "status": "cancelled", "date_created": "2026-10-22T10:00:00.000-03:00", "total_amount": 9990,
                 "shipping": {"id": 503}, "order_items": [
                    {"item": {"id": "MLCA", "title": "Kit A"}, "quantity": 1, "unit_price": 9990, "sale_fee": 1000}]}]})
        if path == "/shipments/501/costs":
            return httpx.Response(200, json={"senders": [{"user_id": 777, "cost": 3000}]})
        if path == "/shipments/502/costs":
            return httpx.Response(404, json={"message": "not found"})
        if path == "/sites/MLC/search":
            return httpx.Response(403, json={"code": "PA_UNAUTHORIZED_RESULT_FROM_POLICIES", "status": 403})
        if path == "/suggestions/user/777/items":
            if self.no_suggestions:
                return httpx.Response(404, json={"message": "user has no items with price suggestion"})
            return httpx.Response(200, json={"total": 2, "items": ["MLC1", "MLCSINREF"]})
        if path == "/suggestions/items/MLCSINREF/details":
            return httpx.Response(404, json={"message": "not found"})
        if path.startswith("/suggestions/items/"):
            return httpx.Response(200, json={
                "item_id": "MLC1", "status": "with_benchmark_high", "currency_id": "CLP",
                "current_price": {"amount": 15990}, "suggested_price": {"amount": 13990},
                "lowest_price": {"amount": 11990}, "internal_price": {"amount": 14500},
                "costs": {"selling_fees": 2100, "shipping_fees": 3200}, "applicable_suggestion": True,
                "percent_difference": 14, "metadata": {"compared_values": 2, "graph": [
                    {"price": {"amount": 14990}, "info": {"title": "Kit B", "sold_quantity": 30}},
                    {"price": {"amount": 11990}, "info": {"title": "Kit A", "sold_quantity": 50}}]}})
        if path == "/highlights/MLC/category/MLCBLOQ":
            return httpx.Response(403, json={"code": "PA_UNAUTHORIZED_RESULT_FROM_POLICIES"})
        if path == "/highlights/MLC/category/MLC123":
            return httpx.Response(200, json={"query_data": {"highlight_type": "BEST_SELLER"}, "content": [
                {"id": "MLCP9", "position": 2, "type": "PRODUCT"},
                {"id": "MLC55", "position": 1, "type": "ITEM"},
                {"id": "MLCU7", "position": 3, "type": "USER_PRODUCT"}]})
        if path == "/items/MLC55":
            return httpx.Response(200, json={"title": "Top 1", "price": 9990, "sold_quantity": 500, "seller_id": 1})
        if path == "/products/MLCP9":
            return httpx.Response(200, json={"name": "Producto 2", "buy_box_winner": {"price": 12990, "seller_id": 2}})
        if path == "/user-products/MLCU7":
            return httpx.Response(404, json={"message": "not found"})
        if path == "/items/visits/time_window":
            q = req.url.params
            assert q["unit"] == "day" and "date_from" not in q
            if self.multi_visits_fail:
                return httpx.Response(400, json={"message": "multiget no soportado"})
            return httpx.Response(200, json=[{"item_id": i, "total_visits": 10} for i in q["ids"].split(",")])
        if path.endswith("/visits/time_window"):
            item = path.split("/")[2]
            return httpx.Response(200, json={"item_id": item, "total_visits": 7})
        if path.endswith("/description") and req.method == "GET":
            if "SINDESC" in path:
                return httpx.Response(404, json={"message": "not found"})
            if "CAIDO" in path:
                return httpx.Response(500, json={"message": "error interno"})
            return httpx.Response(200, json={"plain_text": "hola"})
        if req.method in ("POST", "PUT") and path.endswith("/description"):
            self.writes.append((req.method, path, json.loads(req.content)))
            return httpx.Response(200, json={"ok": True})
        if req.method == "PUT" and path.startswith("/items/"):
            self.writes.append((path, json.loads(req.content)))
            return httpx.Response(200, json={"id": path.split("/")[-1], **json.loads(req.content)})
        return httpx.Response(404, json={"message": "no simulado"})


# -------------------------------------------------------------- fixture
@pytest.fixture
def env(monkeypatch):
    for k, v in {
        "MCP_BEARER_TOKEN": BEARER, "AUTH_SETUP_KEY": SETUP,
        "ML_CLIENT_ID": "123", "ML_CLIENT_SECRET": "sec",
        "ML_REDIRECT_URI": "https://x.vercel.app/auth/callback", "ML_USE_PKCE": "1",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("ML_EXPECTED_USER_ID", raising=False)
    sb, ml = FakeSupabase(), FakeML()
    ml_client.set_store(ml_client.SupabaseStore(
        "https://ref.supabase.co", "service-key", transport=httpx.MockTransport(sb.handler)))
    ml_client.set_http(httpx.Client(transport=httpx.MockTransport(ml.handler)))
    ml_tools.ml._uid = None
    ml_tools.ml.read_only = False
    yield sb, ml, TestClient(app_mod.app)
    ml_client.set_store(None)
    ml_client.set_http(None)


def authorize(client, ml):
    r = client.get(f"/auth/start?key={SETUP}", follow_redirects=False)
    assert r.status_code == 302
    q = parse_qs(urlparse(r.headers["location"]).query)
    assert q["code_challenge_method"] == ["S256"]
    r = client.get(f"/auth/callback?code=CODE-OK&state={q['state'][0]}")
    assert r.status_code == 200, r.text
    return q["state"][0]


def rpc(client, method, params=None, mid=1, token=BEARER):
    body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if mid is not None:
        body["id"] = mid
    return client.post("/mcp", json=body, headers={"Authorization": f"Bearer {token}"})


def call_raw(client, name, args):
    return rpc(client, "tools/call", {"name": name, "arguments": args}).json()["result"]


def call(client, name, args):
    res = call_raw(client, name, args)
    assert res["isError"] is False, res
    return json.loads(res["content"][0]["text"])


# --------------------------------------------------------------- pruebas
def test_mcp_exige_bearer(env):
    _, _, c = env
    assert c.post("/mcp", json={}).status_code == 401
    assert rpc(c, "ping", token="incorrecto").status_code == 401


def test_bearer_corto_o_vacio_rechaza_todo(env, monkeypatch):
    _, _, c = env
    monkeypatch.setenv("MCP_BEARER_TOKEN", "")
    assert rpc(c, "ping", token="").status_code == 401


def test_auth_start_oculto_sin_clave(env):
    _, _, c = env
    assert c.get("/auth/start", follow_redirects=False).status_code == 404
    assert c.get("/auth/start?key=mala", follow_redirects=False).status_code == 404


def test_oauth_guarda_tokens_y_state_es_de_un_uso(env):
    sb, ml, c = env
    state = authorize(c, ml)
    assert sb.row["access_token"] == "APP_USR-1" and sb.row["user_id"] == 777
    assert sb.row["pending_state"] is None
    assert "APP_USR" not in c.get(f"/auth/callback?code=CODE-OK&state={state}").text
    assert c.get(f"/auth/callback?code=CODE-OK&state={state}").status_code == 400


def test_oauth_rechaza_state_falso(env):
    _, _, c = env
    c.get(f"/auth/start?key={SETUP}", follow_redirects=False)
    assert c.get("/auth/callback?code=CODE-OK&state=falso").status_code == 400


def test_oauth_rechaza_otra_cuenta(env, monkeypatch):
    sb, ml, c = env
    monkeypatch.setenv("ML_EXPECTED_USER_ID", "999")
    r = c.get(f"/auth/start?key={SETUP}", follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    assert c.get(f"/auth/callback?code=CODE-OK&state={state}").status_code == 400
    assert not sb.row.get("access_token")


def test_protocolo_mcp(env):
    sb, ml, c = env
    authorize(c, ml)
    init = rpc(c, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "t", "version": "1"}}).json()
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert rpc(c, "notifications/initialized", mid=None).status_code == 202
    tools = rpc(c, "tools/list").json()["result"]["tools"]
    assert len(tools) == 30 and all("inputSchema" in t for t in tools)
    res = rpc(c, "tools/call", {"name": "ml_mi_cuenta", "arguments": {}}).json()["result"]
    assert res["isError"] is False
    assert json.loads(res["content"][0]["text"])["nickname"] == "EMPRESA_TEST"
    assert rpc(c, "metodo/raro").json()["error"]["code"] == -32601


def test_sin_autorizar_devuelve_error_legible(env):
    _, _, c = env
    res = rpc(c, "tools/call", {"name": "ml_mi_cuenta", "arguments": {}}).json()["result"]
    assert res["isError"] and "no está autorizada" in res["content"][0]["text"]


def test_renueva_token_vencido_y_guarda_el_nuevo(env):
    sb, ml, c = env
    authorize(c, ml)
    sb.row["expires_at"] = "2000-01-01T00:00:00Z"
    res = rpc(c, "tools/call", {"name": "ml_mi_cuenta", "arguments": {}}).json()["result"]
    assert res["isError"] is False
    assert ml.refresh_calls == 1 and sb.row["refresh_token"] == "TG-2"
    assert sb.row["lock_until"] is None


def test_renueva_si_la_api_responde_401(env):
    sb, ml, c = env
    authorize(c, ml)
    ml.valid_access.clear()  # token revocado antes de vencer
    res = rpc(c, "tools/call", {"name": "ml_mi_cuenta", "arguments": {}}).json()["result"]
    assert res["isError"] is False and ml.refresh_calls == 1


def test_concurrencia_renueva_una_sola_vez(env):
    sb, ml, c = env
    authorize(c, ml)
    sb.row["expires_at"] = "2000-01-01T00:00:00Z"
    errors = []

    def call():
        try:
            ml_client.MLClient().get("/users/me")
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=call) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors
    assert ml.refresh_calls == 1  # el refresh token de un solo uso no se quema


def test_escritura_vista_previa_y_confirmada(env):
    sb, ml, c = env
    authorize(c, ml)
    args = {"item_id": "MLC1", "precio": 9990}
    prev = call(c, "ml_modificar_publicacion", args)
    assert prev["modo"].startswith("VISTA PREVIA") and ml.writes == [] and sb.actions == []
    done = call(c, "ml_modificar_publicacion", {**args, "codigo_confirmacion": prev["codigo_confirmacion"]})
    assert done["ejecutado"] is True
    assert ml.writes == [("/items/MLC1", {"price": 9990})]
    assert sb.actions[0]["tool"] == "modificar_publicacion"


def test_no_se_puede_saltar_la_vista_previa(env):
    sb, ml, c = env
    authorize(c, ml)
    res = call_raw(c, "ml_modificar_publicacion", {"item_id": "MLC1", "precio": 1, "codigo_confirmacion": "inventado"})
    assert res["isError"] and "no corresponde" in res["content"][0]["text"]
    assert ml.writes == []


def test_codigo_no_sirve_si_cambian_los_datos(env):
    sb, ml, c = env
    authorize(c, ml)
    prev = call(c, "ml_modificar_publicacion", {"item_id": "MLC1", "precio": 9990})
    res = call_raw(c, "ml_modificar_publicacion",
                   {"item_id": "MLC1", "precio": 10, "codigo_confirmacion": prev["codigo_confirmacion"]})
    assert res["isError"] and ml.writes == []


def test_finalizar_publicacion_bloqueado(env):
    sb, ml, c = env
    authorize(c, ml)
    res = call_raw(c, "ml_modificar_publicacion", {"item_id": "MLC1", "estado": "closed"})
    assert res["isError"] and "irreversible" in res["content"][0]["text"]
    assert call_raw(c, "ml_modificar_publicacion", {"item_id": "MLC1", "precio": -5})["isError"]


def test_descripcion_post_solo_si_no_existe(env):
    sb, ml, c = env
    authorize(c, ml)
    prev = call(c, "ml_cambiar_descripcion", {"item_id": "MLCSINDESC", "texto": "nueva"})
    assert prev["request"]["method"] == "POST"
    prev = call(c, "ml_cambiar_descripcion", {"item_id": "MLC1", "texto": "nueva"})
    assert prev["request"]["method"] == "PUT"
    res = call_raw(c, "ml_cambiar_descripcion", {"item_id": "MLCCAIDO", "texto": "nueva"})
    assert res["isError"]  # un error 500 no debe interpretarse como "sin descripción"


def test_respuesta_vacia_o_larga_rechazada(env):
    sb, ml, c = env
    authorize(c, ml)
    assert call_raw(c, "ml_responder_pregunta", {"question_id": 1, "texto": "  "})["isError"]
    assert call_raw(c, "ml_responder_pregunta", {"question_id": 1, "texto": "x" * 2001})["isError"]


def test_herramientas_de_escritura_documentan_el_codigo(env):
    _, _, c = env
    tools = {t["name"]: t for t in rpc(c, "tools/list").json()["result"]["tools"]}
    for name in ("ml_modificar_publicacion", "ml_cambiar_descripcion", "ml_crear_publicacion", "ml_responder_pregunta"):
        assert "codigo_confirmacion" in tools[name]["inputSchema"]["properties"]
        assert "VISTA PREVIA" in tools[name]["description"].upper() or "vista previa" in tools[name]["description"]
        assert "confirmar" not in tools[name]["inputSchema"]["properties"]


def test_keepalive_exige_secreto_y_renueva(env, monkeypatch):
    sb, ml, c = env
    assert c.get("/cron/keepalive").status_code == 401
    monkeypatch.setenv("CRON_SECRET", "c" * 32)
    assert c.get("/cron/keepalive", headers={"Authorization": "Bearer malo"}).status_code == 401
    ok = c.get("/cron/keepalive", headers={"Authorization": "Bearer " + "c" * 32}).json()
    assert ok == {"supabase": "ok", "mercadolibre": "sin autorizar"}
    authorize(c, ml)
    sb.row["expires_at"] = "2000-01-01T00:00:00Z"
    ok = c.get("/cron/keepalive", headers={"Authorization": "Bearer " + "c" * 32}).json()
    assert ok == {"supabase": "ok", "mercadolibre": "ok"} and ml.refresh_calls == 1


def test_modo_solo_lectura_bloquea(env):
    sb, ml, c = env
    authorize(c, ml)
    ml_tools.ml.read_only = True
    res = rpc(c, "tools/call", {"name": "ml_modificar_publicacion",
                                "arguments": {"item_id": "MLC1", "stock": 1, "codigo_confirmacion": "x"}}).json()["result"]
    assert res["isError"] and "solo lectura" in res["content"][0]["text"]
    assert ml.writes == []


def test_health_no_expone_datos(env):
    _, _, c = env
    assert c.get("/health").json() == {"servicio": "mercadolibre-mcp", "estado": "ok"}


def test_headers_supabase_segun_tipo_de_clave():
    nueva = ml_client.SupabaseStore("https://ref.supabase.co", "sb_secret_abc123")
    assert nueva.h["apikey"] == "sb_secret_abc123" and "Authorization" not in nueva.h
    antigua = ml_client.SupabaseStore("https://ref.supabase.co", "eyJhbGciOi.xxx.yyy")
    assert antigua.h["Authorization"] == "Bearer eyJhbGciOi.xxx.yyy"


def test_visitas_usan_ventana_de_tiempo(env):
    sb, ml, c = env
    authorize(c, ml)
    assert call(c, "ml_visitas", {"item_ids": ["MLC1", "MLC2", "MLC1"], "dias": 30}) == {"MLC1": 10, "MLC2": 10}
    ml.multi_visits_fail = True  # si la consulta múltiple falla, se consulta una por una
    assert call(c, "ml_visitas", {"item_ids": ["MLC1", "MLC2"], "dias": 30}) == {"MLC1": 7, "MLC2": 7}


def test_busqueda_403_da_alternativas(env):
    sb, ml, c = env
    authorize(c, ml)
    res = call_raw(c, "ml_buscar_mercado", {"consulta": "kit capibara"})
    assert res["isError"] and "ml_referencia_precio" in res["content"][0]["text"]


def test_referencia_precio(env):
    sb, ml, c = env
    authorize(c, ml)
    r = call(c, "ml_referencia_precio", {})
    ref = r["referencias"][0]
    assert ref["precio_actual"] == 15990 and ref["precio_referencia"] == 13990
    assert ref["costos_ml"] == {"comision": 2100, "envio": 3200}
    assert [x["titulo"] for x in ref["comparables"]] == ["Kit A", "Kit B"]  # ordenados por precio
    assert "ENCIMA" in ref["lectura"]
    assert r["sin_referencia"] == {"MLCSINREF": "sin referencia de precio"}


def test_mas_vendidos(env):
    sb, ml, c = env
    authorize(c, ml)
    r = call(c, "ml_mas_vendidos", {"category_id": "MLC123"})
    assert [x["posicion"] for x in r["ranking"]] == [1, 2, 3]
    assert r["ranking"][0]["titulo"] == "Top 1" and r["ranking"][1]["precio"] == 12990
    assert r["ranking"][2]["detalle"] == "no disponible"


def test_sin_referencias_ni_ranking_da_mensajes_claros(env):
    sb, ml, c = env
    authorize(c, ml)
    ml.no_suggestions = True
    r = call(c, "ml_referencia_precio", {})
    assert r["referencias"] == [] and "ml_publicaciones_competencia" in r["aviso"]
    res = call_raw(c, "ml_mas_vendidos", {"category_id": "MLCBLOQ"})
    assert res["isError"] and "ml_publicaciones_competencia" in res["content"][0]["text"]


def test_facturacion_periodos_y_resumen(env):
    sb, ml, c = env
    authorize(c, ml)
    per = call(c, "ml_facturacion_periodos", {})
    assert per[0]["key"] == "2026-11-01" and per[1]["vencimiento"] == "2026-10-24"
    r = call(c, "ml_facturacion_resumen", {})  # sin key usa el período más reciente
    assert r["cargos"][0]["concepto"] == "Cargo por venta"
    assert r["total_cargos"] == 55000 and r["total_bonificaciones"] == 3000
    assert r["neto_cargos_menos_bonificaciones"] == 52000
    assert call_raw(c, "ml_facturacion_resumen", {"grupo": "XX"})["isError"]


def test_rentabilidad(env):
    sb, ml, c = env
    authorize(c, ml)
    r = call(c, "ml_rentabilidad", {"dias": 30, "costos_unitarios": {"MLCA": 6000}})
    assert r["ordenes_pagadas"] == 2  # la cancelada no cuenta
    a, b = r["por_publicacion"][0], r["por_publicacion"][1]
    assert a["item_id"] == "MLCA" and a["ingresos"] == 31980 and a["comision"] == 4000
    assert a["envio_vendedor"] == 3000 and a["neto_despues_de_ml"] == 24980
    assert a["costo_producto"] == 12000 and a["margen_neto"] == 12980 and a["margen_pct"] == 40.6
    assert b["item_id"] == "MLCB" and b["ordenes_sin_dato_envio"] == 1 and "margen_neto" not in b
    assert r["faltan_costos_de"] == ["MLCB"]
    assert r["totales"]["ingresos"] == 56970 and r["totales"]["comision_pct"] == 13.2


def test_listas_llegan_como_json_valido(env):
    sb, ml, c = env
    authorize(c, ml)
    per = call(c, "ml_facturacion_periodos", {"cantidad": 2})
    assert isinstance(per, list) and len(per) == 2
    vacia = call(c, "ml_visitas", {"item_ids": []})
    assert vacia == {}
