"""Herramientas MCP de Mercado Libre Chile (compartidas por app.py y stdio_server.py).

Seguridad:
- Toda herramienta que ESCRIBE (precio, stock, textos, publicaciones, respuestas) devuelve
  primero una VISTA PREVIA con un código de confirmación firmado por el servidor; solo se
  ejecuta si se repite con los mismos datos y ese código.
- Finalizar publicaciones (irreversible) no está permitido.
- Todo lo escrito queda registrado en la bitácora (Supabase ml_actions o acciones.jsonl).
- ML_READ_ONLY=1 bloquea cualquier escritura.
"""
import hashlib
import hmac
import json
import os
import secrets
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP

from ml_client import MLClient, MLError, log_action

mcp = FastMCP("mercadolibre-chile")
ml = MLClient()
TZ = ZoneInfo("America/Santiago")

ADS_METRICS = (
    "clicks,prints,ctr,cost,cpc,acos,cvr,roas,sov,"
    "direct_units_quantity,indirect_units_quantity,units_quantity,"
    "direct_amount,indirect_amount,total_amount,"
    "organic_units_quantity,organic_units_amount"
)
ITEM_FIELDS = (
    "id,title,price,original_price,available_quantity,sold_quantity,status,"
    "permalink,listing_type_id,health,catalog_product_id,seller_id,condition"
)


# ======================================================================
# Utilidades internas
# ======================================================================
def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


def _range(days: int):
    end = datetime.now(TZ)
    start = (end - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, end


def _fetch_orders(days: int, cap: int = 2000):
    start, end = _range(days)
    orders, offset = [], 0
    while True:
        data = ml.get(
            "/orders/search",
            {
                "seller": ml.user_id,
                "order.date_created.from": _iso(start),
                "order.date_created.to": _iso(end),
                "sort": "date_desc",
                "limit": 50,
                "offset": offset,
            },
        )
        results = data.get("results", [])
        orders += results
        total = data.get("paging", {}).get("total", 0)
        offset += 50
        if not results or offset >= total or offset >= cap:
            break
    return orders, (offset < total if results else False)


def _units_by_item(orders):
    agg = defaultdict(lambda: {"titulo": "", "unidades": 0, "ventas": 0.0, "comision": 0.0})
    for o in orders:
        if o.get("status") != "paid":
            continue
        for oi in o.get("order_items", []):
            it = oi.get("item", {})
            q = oi.get("quantity", 0) or 0
            a = agg[it.get("id")]
            a["titulo"] = it.get("title", "")
            a["unidades"] += q
            a["ventas"] += (oi.get("unit_price", 0) or 0) * q
            a["comision"] += (oi.get("sale_fee", 0) or 0) * q
    return agg


def _active_item_ids(status="active", cap=500):
    ids, offset = [], 0
    while len(ids) < cap:
        data = ml.get(
            f"/users/{ml.user_id}/items/search",
            {"status": status, "limit": 100, "offset": offset},
        )
        res = data.get("results", [])
        ids += res
        offset += 100
        if not res or offset >= data.get("paging", {}).get("total", 0):
            break
    return ids[:cap]


def _items_detail(ids):
    out = []
    for i in range(0, len(ids), 20):
        chunk = ids[i : i + 20]
        data = ml.get("/items", {"ids": ",".join(chunk), "attributes": ITEM_FIELDS})
        for row in data:
            if row.get("code") == 200:
                out.append(row["body"])
    return out


def _visits(ids, days):
    """Visitas por publicación en los últimos `days` días.

    Usa la ventana de tiempo (last=N&unit=day), que no depende del formato de fechas
    (el endpoint con date_from/date_to rechazaba el offset -00:00). Primero intenta la
    consulta múltiple; si la API la rechaza, consulta publicación por publicación."""
    days = max(1, min(int(days), 150))
    out = {}
    ids = [i for i in dict.fromkeys(ids) if i]
    for i in range(0, len(ids), 50):
        chunk = ids[i : i + 50]
        try:
            data = ml.get(
                "/items/visits/time_window",
                {"ids": ",".join(chunk), "last": days, "unit": "day"},
            )
            rows = data if isinstance(data, list) else [data]
            for row in rows:
                if isinstance(row, dict) and row.get("item_id"):
                    out[row["item_id"]] = row.get("total_visits", 0) or 0
        except MLError:
            pass  # se completa abajo, una por una
        for item_id in chunk:
            if item_id not in out:
                row = ml.get(f"/items/{item_id}/visits/time_window", {"last": days, "unit": "day"})
                out[item_id] = row.get("total_visits", 0) or 0
    return out


_LOCAL_KEY = secrets.token_bytes(32)  # respaldo para uso local sin MCP_BEARER_TOKEN


def _confirmation_code(req: dict) -> str:
    """Código atado EXACTAMENTE a esta solicitud. Solo el servidor puede calcularlo,
    así que no se puede ejecutar una escritura sin haber pasado por la vista previa,
    ni cambiar los datos entre la vista previa y la ejecución."""
    key = os.environ.get("MCP_BEARER_TOKEN", "").encode() or _LOCAL_KEY
    msg = json.dumps(req, sort_keys=True, ensure_ascii=False, default=str).encode()
    return hmac.new(key, msg, hashlib.sha256).hexdigest()[:16]


def _guarded_write(accion, method, path, body, codigo_confirmacion, params=None, api_version=None):
    if ml.read_only:
        raise MLError("Modo solo lectura activo (ML_READ_ONLY=1): no se permiten cambios en la cuenta.")
    req = {"accion": accion, "method": method, "path": path, "params": params, "body": body}
    expected = _confirmation_code(req)
    if not codigo_confirmacion:
        return {
            "modo": "VISTA PREVIA - no se ejecutó nada en tu cuenta",
            "accion": accion,
            "request": req,
            "codigo_confirmacion": expected,
            "siguiente_paso": (
                "Muestra este cambio al usuario. Solo si lo aprueba explícitamente, repite la llamada "
                "con los MISMOS datos y codigo_confirmacion igual a este código."
            ),
        }
    if not hmac.compare_digest(str(codigo_confirmacion), expected):
        raise MLError(
            "El código de confirmación no corresponde a esta solicitud (¿cambiaron los datos?). "
            "Pide una nueva vista previa sin codigo_confirmacion."
        )
    result = ml.request(method, path, params=params, body=body, api_version=api_version)
    log_action(accion, req, result)
    return {"ejecutado": True, "accion": accion, "respuesta": result}


_CONFIRM_DOC = (
    " Primero llama SIN codigo_confirmacion: devuelve una vista previa y un código. "
    "Muestra la vista previa al usuario y, solo si la aprueba, repite con los mismos datos y ese código."
)


# ======================================================================
# Cuenta y reputación
# ======================================================================
@mcp.tool()
def ml_mi_cuenta() -> dict:
    """Datos de la cuenta autenticada: id, nickname, país y nivel de reputación resumido."""
    u = ml.get("/users/me")
    rep = u.get("seller_reputation", {})
    return {
        "user_id": u.get("id"),
        "nickname": u.get("nickname"),
        "pais": u.get("country_id"),
        "tipo": u.get("user_type"),
        "nivel_reputacion": rep.get("level_id"),
        "mercadolider": rep.get("power_seller_status"),
    }


@mcp.tool()
def ml_reputacion() -> dict:
    """Reputación completa del vendedor: nivel, ventas, reclamos, demoras y cancelaciones (últimos 60 días)."""
    u = ml.get("/users/me")
    return u.get("seller_reputation", {})


# ======================================================================
# Publicaciones
# ======================================================================
@mcp.tool()
def ml_listar_publicaciones(estado: str = "active", maximo: int = 100) -> list:
    """Lista tus publicaciones con precio, stock, ventas y estado.
    estado: active | paused | closed | under_review | inactive."""
    ids = _active_item_ids(estado, cap=max(1, min(maximo, 500)))
    return _items_detail(ids)


@mcp.tool()
def ml_ver_publicacion(item_id: str) -> dict:
    """Detalle completo de una publicación (tuya o de la competencia) incluida su descripción."""
    item = ml.get(f"/items/{item_id}")
    try:
        item["descripcion"] = ml.get(f"/items/{item_id}/description").get("plain_text")
    except MLError:
        item["descripcion"] = None
    return item


@mcp.tool(description=(
    "Modifica precio, stock, estado (active = activar | paused = pausar) o título de UNA publicación. "
    "Finalizar (closed) NO está permitido porque es irreversible. "
    "El título no se puede cambiar si ya tiene ventas." + _CONFIRM_DOC
))
def ml_modificar_publicacion(
    item_id: str,
    precio: float | None = None,
    stock: int | None = None,
    estado: str | None = None,
    titulo: str | None = None,
    codigo_confirmacion: str = "",
) -> dict:
    """Modifica precio, stock, estado (active = activar | paused = pausar) o título de UNA publicación.
    Finalizar (closed) NO está permitido aquí porque es irreversible: hazlo desde Mercado Libre.
    El título no se puede cambiar si ya tiene ventas."""
    body = {}
    if precio is not None:
        if precio <= 0:
            raise MLError("El precio debe ser mayor que 0.")
        body["price"] = precio
    if stock is not None:
        if stock < 0:
            raise MLError("El stock no puede ser negativo.")
        body["available_quantity"] = stock
    if estado is not None:
        if estado not in ("active", "paused"):
            raise MLError("estado solo admite 'active' o 'paused'. Finalizar es irreversible y no se permite desde el MCP.")
        body["status"] = estado
    if titulo is not None:
        body["title"] = titulo
    if not body:
        raise MLError("Indica al menos un campo a modificar.")
    return _guarded_write("modificar_publicacion", "PUT", f"/items/{item_id}", body, codigo_confirmacion)


@mcp.tool(description="Reemplaza (o crea) la descripción en texto plano de una publicación." + _CONFIRM_DOC)
def ml_cambiar_descripcion(item_id: str, texto: str, codigo_confirmacion: str = "") -> dict:
    try:
        ml.get(f"/items/{item_id}/description")
        method = "PUT"
    except MLError as e:
        if e.status != 404:  # solo "no existe descripción" justifica crearla
            raise
        method = "POST"
    return _guarded_write(
        "cambiar_descripcion",
        method,
        f"/items/{item_id}/description",
        {"plain_text": texto},
        codigo_confirmacion,
        params={"api_version": 2},
    )


@mcp.tool()
def ml_predecir_categoria(titulo: str) -> list:
    """Sugiere la categoría (y dominio) para un producto a partir de su título."""
    return ml.get(f"/sites/{ml.site}/domain_discovery/search", {"limit": 5, "q": titulo})


@mcp.tool()
def ml_atributos_categoria(category_id: str) -> list:
    """Atributos (especificaciones) de una categoría; los obligatorios llevan la etiqueta 'required'."""
    return ml.get(f"/categories/{category_id}/attributes")


@mcp.tool()
def ml_tipos_publicacion() -> list:
    """Tipos de publicación del sitio (gratuita, clásica, premium) con sus nombres."""
    return ml.get(f"/sites/{ml.site}/listing_types")


@mcp.tool()
def ml_validar_publicacion(payload: dict) -> dict:
    """Valida el JSON de una publicación nueva SIN publicarla. Úsalo siempre antes de ml_crear_publicacion."""
    return ml.request("POST", "/items/validate", body=payload)


@mcp.tool(description=(
    "Publica un producto nuevo. Queda EN VIVO y puede vender de inmediato. "
    "Valida antes con ml_validar_publicacion." + _CONFIRM_DOC
))
def ml_crear_publicacion(payload: dict, codigo_confirmacion: str = "") -> dict:
    return _guarded_write("crear_publicacion", "POST", "/items", payload, codigo_confirmacion)


# ======================================================================
# Ventas y conversión
# ======================================================================
@mcp.tool()
def ml_ordenes(dias: int = 7, maximo: int = 50) -> list:
    """Órdenes recientes (fecha, estado, total, productos). Para totales usa ml_resumen_ventas."""
    orders, _ = _fetch_orders(dias, cap=max(1, min(maximo, 500)))
    return [
        {
            "id": o.get("id"),
            "fecha": o.get("date_created"),
            "estado": o.get("status"),
            "total": o.get("total_amount"),
            "items": [
                {
                    "id": i["item"]["id"],
                    "titulo": i["item"]["title"],
                    "cantidad": i["quantity"],
                    "precio_unit": i["unit_price"],
                }
                for i in o.get("order_items", [])
            ],
        }
        for o in orders[:maximo]
    ]


@mcp.tool()
def ml_resumen_ventas(dias: int = 30) -> dict:
    """Resumen de ventas: órdenes, unidades, ventas brutas, comisión estimada, ticket promedio,
    ventas por día y top productos. No incluye costo de envío ni impuestos."""
    orders, truncado = _fetch_orders(dias)
    paid = [o for o in orders if o.get("status") == "paid"]
    by_item = _units_by_item(orders)
    por_dia = defaultdict(float)
    for o in paid:
        por_dia[o["date_created"][:10]] += o.get("total_amount", 0) or 0
    bruto = sum(o.get("total_amount", 0) or 0 for o in paid)
    comision = sum(v["comision"] for v in by_item.values())
    top = sorted(by_item.items(), key=lambda kv: kv[1]["ventas"], reverse=True)[:15]
    return {
        "periodo_dias": dias,
        "ordenes_pagadas": len(paid),
        "ordenes_otros_estados": len(orders) - len(paid),
        "unidades": sum(v["unidades"] for v in by_item.values()),
        "ventas_brutas": round(bruto),
        "comision_estimada": round(comision),
        "ventas_menos_comision": round(bruto - comision),
        "ticket_promedio": round(bruto / len(paid)) if paid else 0,
        "ventas_por_dia": dict(sorted(por_dia.items())),
        "top_productos": [{"item_id": k, **{a: (round(b) if isinstance(b, float) else b) for a, b in v.items()}} for k, v in top],
        "aviso": "Resultado truncado a 2000 órdenes" if truncado else None,
    }


@mcp.tool()
def ml_visitas(item_ids: list[str], dias: int = 30) -> dict:
    """Visitas de una o varias publicaciones en los últimos N días."""
    return _visits(item_ids, dias)


@mcp.tool()
def ml_reporte_conversion(dias: int = 30, maximo_items: int = 200) -> dict:
    """Cruza visitas y ventas por publicación activa: conversión = unidades / visitas.
    Detecta publicaciones con tráfico que no venden (problema de precio/fotos/título) y las que
    venden con poco tráfico (candidatas a publicidad)."""
    ids = _active_item_ids("active", cap=maximo_items)
    detail = {d["id"]: d for d in _items_detail(ids)}
    visits = _visits(ids, dias)
    orders, _ = _fetch_orders(dias)
    units = _units_by_item(orders)
    rows = []
    for i in ids:
        d, v, u = detail.get(i, {}), visits.get(i, 0), units.get(i, {}).get("unidades", 0)
        rows.append(
            {
                "item_id": i,
                "titulo": d.get("title"),
                "precio": d.get("price"),
                "stock": d.get("available_quantity"),
                "visitas": v,
                "unidades": u,
                "conversion_pct": round(100 * u / v, 2) if v else None,
            }
        )
    rows.sort(key=lambda r: r["visitas"], reverse=True)
    return {
        "periodo_dias": dias,
        "con_visitas_sin_ventas": [r for r in rows if r["visitas"] >= 30 and r["unidades"] == 0][:30],
        "todas": rows,
    }


# ======================================================================
# Preguntas
# ======================================================================
@mcp.tool()
def ml_preguntas(estado: str = "UNANSWERED", maximo: int = 50) -> dict:
    """Preguntas recibidas. estado: UNANSWERED | ANSWERED. (Endpoint por validar con tu cuenta.)"""
    return ml.get(
        "/questions/search",
        {"seller_id": ml.user_id, "status": estado, "limit": min(maximo, 50), "api_version": 4},
    )


@mcp.tool(description=(
    "Responde una pregunta de un comprador. Se publica con el nombre de la tienda y no se puede editar "
    "(máx. 2000 caracteres)." + _CONFIRM_DOC
))
def ml_responder_pregunta(question_id: int, texto: str, codigo_confirmacion: str = "") -> dict:
    texto = texto.strip()
    if not texto:
        raise MLError("La respuesta no puede estar vacía.")
    if len(texto) > 2000:
        raise MLError(f"La respuesta tiene {len(texto)} caracteres; el máximo es 2000.")
    return _guarded_write(
        "responder_pregunta", "POST", "/answers", {"question_id": question_id, "text": texto},
        codigo_confirmacion,
    )


# ======================================================================
# Publicidad (Mercado Ads - Product Ads), solo lectura en esta versión
# ======================================================================
@mcp.tool()
def ml_ads_anunciante() -> dict:
    """Devuelve tu advertiser_id de Product Ads (necesario para las demás herramientas de publicidad).
    Requiere tener Publicidad activada en Mercado Libre > Mi perfil > Publicidad."""
    return ml.get("/advertising/advertisers", {"product_id": "PADS"})


@mcp.tool()
def ml_ads_campanas(advertiser_id: int, desde: str, hasta: str) -> dict:
    """Campañas de Product Ads con métricas (inversión, clics, ACOS, ROAS, ventas por publicidad).
    Fechas AAAA-MM-DD. (Ruta por validar con tu cuenta; si falla usa ml_api_get.)"""
    return ml.get(
        f"/advertising/{ml.site}/advertisers/{advertiser_id}/product_ads/campaigns/search",
        {"limit": 50, "offset": 0, "date_from": desde, "date_to": hasta, "metrics": ADS_METRICS},
        api_version=2,
    )


@mcp.tool()
def ml_ads_anuncios(advertiser_id: int, desde: str, hasta: str, limite: int = 50, offset: int = 0) -> dict:
    """Métricas por anuncio (publicación) de Product Ads: inversión, clics, ACOS, ROAS, ventas directas e indirectas.
    Fechas AAAA-MM-DD. Sirve para encontrar anuncios que gastan sin vender."""
    return ml.get(
        f"/advertising/{ml.site}/advertisers/{advertiser_id}/product_ads/ads/search",
        {"limit": limite, "offset": offset, "date_from": desde, "date_to": hasta, "metrics": ADS_METRICS},
        api_version=2,
    )


# ======================================================================
# Competencia y mercado
# ======================================================================
@mcp.tool()
def ml_buscar_mercado(consulta: str, limite: int = 20, orden: str = "relevance", categoria: str | None = None) -> list:
    """Busca publicaciones en Mercado Libre Chile por texto. AVISO: Mercado Libre bloquea este recurso (403)
    para la mayoría de las apps. Para analizar competencia prefiere ml_referencia_precio (comparables de una
    publicación tuya) y ml_mas_vendidos (ranking de una categoría). orden: relevance | price_asc | price_desc."""
    params = {"q": consulta, "limit": min(limite, 50), "sort": orden}
    if categoria:
        params["category"] = categoria
    try:
        data = ml.get(f"/sites/{ml.site}/search", params)
    except MLError as e:
        if e.status == 403:
            raise MLError(
                "Mercado Libre bloquea la búsqueda pública para esta app (403), una restricción general de la "
                "plataforma. Alternativas oficiales: ml_referencia_precio (precio sugerido y publicaciones "
                "comparables de una publicación tuya), ml_mas_vendidos (más vendidos de una categoría) o "
                "ml_publicaciones_competencia con IDs MLC... que entregue el usuario.",
                status=403,
            ) from e
        raise
    return [
        {
            "id": r.get("id"),
            "titulo": r.get("title"),
            "precio": r.get("price"),
            "precio_original": r.get("original_price"),
            "vendidos": r.get("sold_quantity"),
            "stock": r.get("available_quantity"),
            "condicion": r.get("condition"),
            "tipo_publicacion": r.get("listing_type_id"),
            "envio_gratis": (r.get("shipping") or {}).get("free_shipping"),
            "tienda_oficial": r.get("official_store_id"),
            "vendedor_id": (r.get("seller") or {}).get("id"),
            "vendedor": (r.get("seller") or {}).get("nickname"),
            "link": r.get("permalink"),
        }
        for r in data.get("results", [])
    ]


@mcp.tool()
def ml_publicaciones_competencia(item_ids: list[str]) -> list:
    """Ficha de publicaciones de otros vendedores por ID (precio, vendidos, stock, tipo, estado)."""
    return _items_detail(item_ids)


@mcp.tool()
def ml_perfil_vendedor(seller_id: int) -> dict:
    """Perfil público de otro vendedor: antigüedad, reputación y volumen de transacciones."""
    u = ml.get(f"/users/{seller_id}")
    rep = u.get("seller_reputation", {})
    return {
        "id": u.get("id"),
        "nickname": u.get("nickname"),
        "registro": u.get("registration_date"),
        "nivel": rep.get("level_id"),
        "mercadolider": rep.get("power_seller_status"),
        "transacciones": rep.get("transactions"),
        "metricas": rep.get("metrics"),
    }


def _amount(obj):
    return (obj or {}).get("amount") if isinstance(obj, dict) else obj


_REF_STATUS = {
    "with_benchmark_highest": "Tu precio es MÁS ALTO que la referencia y que todos los comparables",
    "with_benchmark_high": "Tu precio está por ENCIMA de la referencia",
    "no_benchmark_ok": "Tu precio está EN LÍNEA con la referencia",
    "no_benchmark_lowest": "Tu precio está POR DEBAJO de la referencia",
    "not_optin_applied": "Hay una promoción sugerida que no has activado",
    "promotion_scheduled": "Promoción sugerida programada",
    "promotion_active": "Promoción sugerida activa",
}


def _price_reference(item_id: str) -> dict:
    d = ml.get(f"/suggestions/items/{item_id}/details")
    meta = d.get("metadata") or {}
    comparables = [
        {
            "titulo": (g.get("info") or {}).get("title"),
            "precio": _amount(g.get("price")),
            "vendidos": (g.get("info") or {}).get("sold_quantity"),
        }
        for g in (meta.get("graph") or [])
    ]
    comparables.sort(key=lambda c: (c["precio"] is None, c["precio"] or 0))
    costs = d.get("costs") or {}
    return {
        "item_id": d.get("item_id", item_id),
        "estado": d.get("status"),
        "lectura": _REF_STATUS.get(d.get("status"), d.get("status")),
        "precio_actual": _amount(d.get("current_price")),
        "precio_referencia": _amount(d.get("suggested_price")),
        "precio_mas_bajo": _amount(d.get("lowest_price")),
        "precio_referencia_interna": _amount(d.get("internal_price")),
        "diferencia_pct": d.get("percent_difference"),
        "sugerencia_aplicable": d.get("applicable_suggestion"),
        "costos_ml": {"comision": costs.get("selling_fees"), "envio": costs.get("shipping_fees")},
        "comparables": comparables,
        "cantidad_comparados": meta.get("compared_values"),
        "promocion_sugerida": d.get("promotion_detail") or meta.get("promotion_detail"),
        "actualizado": d.get("last_updated") or meta.get("last_updated"),
    }


@mcp.tool()
def ml_referencia_precio(item_ids: list[str] | None = None, maximo: int = 20) -> dict:
    """Competencia de precios OFICIAL de Mercado Libre para tus publicaciones: precio de referencia,
    precio más bajo, publicaciones comparables (título, precio y vendidos), costos de comisión y envío
    y si conviene ajustar. Sin item_ids, revisa las publicaciones tuyas que Mercado Libre tiene con
    referencia de precio. Solo funciona con publicaciones propias."""
    if not item_ids:
        try:
            data = ml.get(f"/suggestions/user/{ml.user_id}/items")
        except MLError as e:
            if e.status != 404:
                raise
            data = {}
        item_ids = data.get("items") or []
        if not item_ids:
            return {"aviso": (
                        "Mercado Libre no tiene referencias de precio para tus publicaciones. Las calcula solo "
                        "cuando hay productos equivalentes (marca, modelo o código de barras); los kits armados "
                        "por el vendedor normalmente no tienen. Alternativa: comparar con publicaciones de la "
                        "competencia por ID usando ml_publicaciones_competencia."),
                    "referencias": []}
    out, errores = [], {}
    for item_id in list(dict.fromkeys(item_ids))[: max(1, min(maximo, 50))]:
        try:
            out.append(_price_reference(item_id))
        except MLError as e:
            errores[item_id] = "sin referencia de precio" if e.status == 404 else str(e)[:200]
    return {"referencias": out, "sin_referencia": errores}


def _highlight_detail(entry: dict) -> dict:
    kind, hid = entry.get("type"), entry.get("id")
    row = {"posicion": entry.get("position"), "tipo": kind, "id": hid}
    try:
        if kind == "ITEM":
            it = ml.get(f"/items/{hid}", {"attributes": "title,price,sold_quantity,seller_id,permalink,listing_type_id"})
            row.update(titulo=it.get("title"), precio=it.get("price"), vendidos=it.get("sold_quantity"),
                       vendedor_id=it.get("seller_id"), link=it.get("permalink"))
        elif kind == "PRODUCT":
            p = ml.get(f"/products/{hid}")
            win = p.get("buy_box_winner") or {}
            row.update(titulo=p.get("name"), precio=win.get("price"), vendedor_id=win.get("seller_id"),
                       link=p.get("permalink"))
        elif kind == "USER_PRODUCT":
            up = ml.get(f"/user-products/{hid}")
            row.update(titulo=up.get("name"), vendedor_id=up.get("user_id"))
    except MLError:
        row["detalle"] = "no disponible"
    return row


@mcp.tool()
def ml_mas_vendidos(category_id: str, detalle: bool = True, maximo: int = 20) -> dict:
    """Ranking OFICIAL de los más vendidos de una categoría en Mercado Libre Chile (hasta 20), con título,
    precio y vendedor cuando la API los entrega. Usa el category_id de tu publicación (ml_ver_publicacion)
    para ver contra quién compites."""
    try:
        data = ml.get(f"/highlights/{ml.site}/category/{category_id}")
    except MLError as e:
        if e.status == 403:
            raise MLError(
                "Mercado Libre bloquea el ranking de más vendidos para esta app (403), por política de la "
                "plataforma. Alternativa: comparar con publicaciones de la competencia por ID usando "
                "ml_publicaciones_competencia.",
                status=403,
            ) from e
        raise
    content = sorted(data.get("content") or [], key=lambda c: c.get("position") or 0)[: max(1, min(maximo, 20))]
    rows = [_highlight_detail(c) if detalle else
            {"posicion": c.get("position"), "tipo": c.get("type"), "id": c.get("id")} for c in content]
    return {"categoria": category_id, "criterio": (data.get("query_data") or {}).get("highlight_type"),
            "ranking": rows}


@mcp.tool()
def ml_tendencias(category_id: str | None = None) -> list:
    """Búsquedas en tendencia en Mercado Libre Chile (general o por categoría)."""
    path = f"/trends/{ml.site}" + (f"/{category_id}" if category_id else "")
    return ml.get(path)


# ======================================================================
# Finanzas: facturación de Mercado Libre y rentabilidad (solo lectura)
# ======================================================================
def _check_group(grupo: str) -> str:
    grupo = (grupo or "ML").upper()
    if grupo not in ("ML", "MP"):
        raise MLError("grupo debe ser 'ML' (Mercado Libre) o 'MP' (Mercado Pago).")
    return grupo


def _billing_periods(grupo: str, cantidad: int) -> list:
    data = ml.get(
        "/billing/integration/monthly/periods",
        {"group": grupo, "document_type": "BILL", "offset": 0, "limit": max(1, min(cantidad, 12))},
    )
    return data.get("results") or []


@mcp.tool()
def ml_facturacion_periodos(cantidad: int = 6, grupo: str = "ML") -> list:
    """Períodos de facturación mensual de Mercado Libre (hasta 12): rango de fechas, monto facturado,
    monto impago, vencimiento y estado (OPEN/CLOSED). La 'key' de cada período se usa en
    ml_facturacion_resumen. grupo: ML (cargos de Mercado Libre) o MP (Mercado Pago)."""
    grupo = _check_group(grupo)
    return [
        {
            "key": p.get("key"),
            "desde": (p.get("period") or {}).get("date_from"),
            "hasta": (p.get("period") or {}).get("date_to"),
            "monto_facturado": p.get("amount"),
            "monto_impago": p.get("unpaid_amount"),
            "vencimiento": p.get("debt_expiration_date") or p.get("expiration_date"),
            "estado": p.get("period_status"),
        }
        for p in _billing_periods(grupo, cantidad)
    ]


def _lines(rows):
    out = [{"concepto": r.get("label"), "tipo": r.get("type"), "monto": r.get("amount") or 0} for r in rows or []]
    return sorted(out, key=lambda r: -abs(r["monto"]))


@mcp.tool()
def ml_facturacion_resumen(key: str | None = None, grupo: str = "ML") -> dict:
    """Resumen de facturación de un período: cargos de Mercado Libre desglosados (comisión por venta,
    envíos, Product Ads, percepciones, etc.), bonificaciones/reintegros, total y lo cobrado o adeudado.
    key: primer día del mes del período (AAAA-MM-01, ver ml_facturacion_periodos). Sin key usa el
    período más reciente. Mercado Libre actualiza este resumen una vez al día."""
    grupo = _check_group(grupo)
    if not key:
        periods = _billing_periods(grupo, 1)
        if not periods:
            return {"aviso": "Mercado Libre no informa períodos de facturación para esta cuenta."}
        key = periods[0].get("key")
    data = ml.get(f"/billing/integration/periods/key/{key}/summary/details", {"group": grupo, "document_type": "BILL"})
    bill = data.get("bill_includes") or {}
    cargos, bonos = _lines(bill.get("charges")), _lines(bill.get("bonuses"))
    total_cargos = sum(c["monto"] for c in cargos)
    total_bonos = sum(b["monto"] for b in bonos)
    return {
        "periodo": data.get("period") or {"key": key},
        "cargos": cargos,
        "bonificaciones": bonos,
        "total_cargos": round(total_cargos, 2),
        "total_bonificaciones": round(total_bonos, 2),
        "neto_cargos_menos_bonificaciones": round(total_cargos - total_bonos, 2),
        "total_factura": bill.get("total_amount"),
        "percepciones": bill.get("total_perceptions"),
        "cobro": data.get("payment_collected"),
        "errores": data.get("errors") or None,
    }


def _shipping_cost_for_seller(shipping_id) -> float | None:
    """Costo de envío que paga el vendedor en una orden (None si la API no lo informa)."""
    if not shipping_id:
        return None
    try:
        c = ml.get(f"/shipments/{shipping_id}/costs")
    except MLError:
        return None
    senders = c.get("senders") or []
    for s in senders:
        if str(s.get("user_id")) == str(ml.user_id):
            return float(s.get("cost") or 0)
    return float(senders[0].get("cost") or 0) if senders else None


@mcp.tool()
def ml_rentabilidad(
    dias: int = 30,
    costos_unitarios: dict[str, float] | None = None,
    incluir_envios: bool = True,
) -> dict:
    """Rentabilidad por publicación en las órdenes pagadas de los últimos N días: ingresos, comisión de
    Mercado Libre (sale_fee por unidad × cantidad), costo de envío a cargo del vendedor (si la API lo
    informa) y, si entregas costos_unitarios {item_id: costo del producto por unidad}, el margen neto.
    Mercado Libre NO conoce el costo de tus productos: pídeselo al usuario para calcular el margen real.
    La publicidad no se asigna por producto aquí; mírala en ml_facturacion_resumen o ml_ads_anuncios."""
    costos_unitarios = costos_unitarios or {}
    orders, truncado = _fetch_orders(max(1, min(dias, 365)))
    paid = [o for o in orders if o.get("status") == "paid"]
    agg = defaultdict(lambda: {"titulo": "", "unidades": 0, "ingresos": 0.0, "comision": 0.0,
                               "envio_vendedor": 0.0, "ordenes_sin_dato_envio": 0})
    envios_consultados = 0
    for o in paid:
        items = o.get("order_items") or []
        envio = None
        if incluir_envios and envios_consultados < 300:
            envio = _shipping_cost_for_seller((o.get("shipping") or {}).get("id"))
            envios_consultados += 1
        total_units = sum((oi.get("quantity") or 0) for oi in items) or 1
        for oi in items:
            it = oi.get("item") or {}
            q = oi.get("quantity") or 0
            a = agg[it.get("id")]
            a["titulo"] = it.get("title", "")
            a["unidades"] += q
            a["ingresos"] += (oi.get("unit_price") or 0) * q
            a["comision"] += (oi.get("sale_fee") or 0) * q
            if envio is None:
                a["ordenes_sin_dato_envio"] += 1
            else:  # el envío de la orden se reparte según unidades
                a["envio_vendedor"] += envio * q / total_units

    rows = []
    for item_id, a in agg.items():
        neto_ml = a["ingresos"] - a["comision"] - a["envio_vendedor"]
        row = {"item_id": item_id, **{k: (round(v) if isinstance(v, float) else v) for k, v in a.items()},
               "neto_despues_de_ml": round(neto_ml)}
        costo = costos_unitarios.get(item_id)
        if costo is not None:
            costo_total = costo * a["unidades"]
            row["costo_producto"] = round(costo_total)
            row["margen_neto"] = round(neto_ml - costo_total)
            row["margen_pct"] = round(100 * (neto_ml - costo_total) / a["ingresos"], 1) if a["ingresos"] else None
        rows.append(row)
    rows.sort(key=lambda r: -r["ingresos"])

    tot = {k: round(sum(r.get(k, 0) or 0 for r in rows)) for k in
           ("unidades", "ingresos", "comision", "envio_vendedor", "neto_despues_de_ml")}
    if tot["ingresos"]:
        tot["comision_pct"] = round(100 * tot["comision"] / tot["ingresos"], 1)
        tot["envio_pct"] = round(100 * tot["envio_vendedor"] / tot["ingresos"], 1)
    sin_costo = [r["item_id"] for r in rows if "costo_producto" not in r]
    return {
        "periodo_dias": dias,
        "ordenes_pagadas": len(paid),
        "totales": tot,
        "por_publicacion": rows,
        "faltan_costos_de": sin_costo or None,
        "aviso": "Resultado truncado a 2000 órdenes" if truncado else None,
    }


# ======================================================================
# Exploración libre (solo lectura)
# ======================================================================
@mcp.tool()
def ml_api_get(ruta: str, parametros: dict | None = None, api_version: int | None = None) -> dict | list:
    """GET libre a la API de Mercado Libre (solo lectura), p. ej. ruta='/users/me'.
    Útil para probar endpoints nuevos de la documentación (publicidad, promociones, envíos, etc.).
    En {user_id} usa el id real obtenido con ml_mi_cuenta."""
    return ml.get(ruta, parametros, api_version=api_version)

