# mcp-mercadolibre: conecta Mercado Libre con tu asistente de IA

Conecta tu cuenta de vendedor de Mercado Libre con **Claude, Gemini o Codex**: ventas, publicaciones,
preguntas, publicidad, facturación y rentabilidad, en lenguaje natural. Funciona **en la nube**
(GitHub + Vercel + Supabase, gratis) o **en tu computador**.

Creado por **Brian Neumann** · [LinkedIn](https://www.linkedin.com/in/brian-neumann-andonie/)

## Empieza aquí: deja que tu asistente te guíe

No necesitas leer esta documentación:

1. Crea tu copia **privada** de este repositorio: **Use this template → Create a new repository → Private**.
2. Ábrela con tu asistente y escribe:
   > Ayúdame a configurar esto paso a paso.

   | Asistente | Lee automáticamente |
   |---|---|
   | Claude Code ([claude.ai/code](https://claude.ai/code) o terminal) | `CLAUDE.md` |
   | Gemini CLI | `GEMINI.md` |
   | Codex (CLI, app o IDE) | `AGENTS.md` |

   Los tres apuntan a la misma guía, [`AGENTS.md`](AGENTS.md), y el asistente te acompaña etapa por etapa,
   verificando cada una.
3. ¿Usas un chat sin acceso al repositorio? Descarga [`AGENTS.md`](AGENTS.md), súbelo al chat y escribe lo mismo.

Nunca pegues tus claves en el chat: el asistente te dirá dónde va cada una.

**Compatibilidad:** Claude Code, Gemini CLI y Codex funcionan en la nube y en local. Claude Desktop, solo en
local. ChatGPT web y el chat de claude.ai todavía no, porque sus conectores exigen OAuth y este servidor usa
token.

## Cómo funciona

```
Tu asistente (Claude / Gemini / Codex) ──HTTPS + Bearer──▶ Vercel (app.py /mcp) ──▶ API Mercado Libre
                                                        │
                                                        └──▶ Supabase (tokens + bitácora)
```

- **GitHub**: guarda tu copia del código (privada). Cada `git push` a `main` redespliega Vercel.
- **Vercel**: ejecuta el servidor (`app.py`, FastAPI). No guarda estado.
- **Supabase**: guarda los tokens de Mercado Libre y la bitácora de escrituras (`ml_actions`).

## Herramientas (30)

| Área | Herramientas |
|---|---|
| Cuenta y reputación | `ml_mi_cuenta`, `ml_reputacion` |
| Publicaciones | `ml_listar_publicaciones`, `ml_ver_publicacion`, `ml_modificar_publicacion`, `ml_cambiar_descripcion`, `ml_predecir_categoria`, `ml_atributos_categoria`, `ml_tipos_publicacion`, `ml_validar_publicacion`, `ml_crear_publicacion` |
| Ventas y conversión | `ml_ordenes`, `ml_resumen_ventas`, `ml_visitas`, `ml_reporte_conversion` |
| Preguntas | `ml_preguntas`, `ml_responder_pregunta` |
| Publicidad (lectura) | `ml_ads_anunciante`, `ml_ads_campanas`, `ml_ads_anuncios` |
| Competencia | `ml_referencia_precio` (precio de referencia y comparables oficiales), `ml_mas_vendidos` (ranking por categoría), `ml_publicaciones_competencia`, `ml_perfil_vendedor`, `ml_tendencias`, `ml_buscar_mercado` (Mercado Libre lo bloquea con 403 para la mayoría de las apps) |
| Finanzas (lectura) | `ml_facturacion_periodos`, `ml_facturacion_resumen` (cargos y bonificaciones de Mercado Libre por período), `ml_rentabilidad` (ingresos, comisión, envío y margen por publicación; el costo de tus productos lo entregas tú) |
| Exploración | `ml_api_get` (GET libre, solo lectura) |

## Seguridad

- El endpoint `/mcp` exige `Authorization: Bearer <MCP_BEARER_TOKEN>`. Sin ese secreto configurado, rechaza todo.
- `/auth/start` solo responde con `?key=<AUTH_SETUP_KEY>` (si no, devuelve 404).
- La autorización OAuth usa `state` de un solo uso, PKCE y caduca a los 10 minutos. Con `ML_EXPECTED_USER_ID` solo acepta tu cuenta.
- Los tokens nunca se muestran: viven en Supabase, en tablas con RLS activo y sin acceso para `anon`/`authenticated`. Solo la service key del servidor las lee.
- Toda escritura devuelve primero una vista previa con un `codigo_confirmacion` firmado por el servidor (HMAC). Solo se ejecuta si se repite con los mismos datos y ese código: no se puede saltar la vista previa ni cambiar los datos después de aprobarlos. Cada escritura queda en `ml_actions`. `ML_READ_ONLY=1` bloquea todas.
- Finalizar publicaciones (`closed`, irreversible) no está permitido desde el MCP; pausar sí.
- Tarea diaria `/cron/keepalive` (protegida con `CRON_SECRET`): evita que el proyecto gratuito de Supabase se pause por inactividad y renueva el token de Mercado Libre.
- El refresh token de Mercado Libre es de un solo uso: la renovación usa un arrendamiento (`lock_until`) para que solicitudes simultáneas no lo quemen.

## Despliegue paso a paso (referencia manual; `AGENTS.md` tiene la versión guiada, la tabla de países y la conexión de cada asistente)

### 1. Supabase
1. Usa un proyecto activo (o crea uno, región `sa-east-1` São Paulo).
2. Ejecuta `supabase/migrations/20261030000000_ml_tokens.sql` en **SQL Editor**.
3. En **Project Settings → API** copia la **Project URL** y la clave secreta de servidor (`service_role` o `sb_secret_...`).

### 2. GitHub
Sube esta carpeta a un **repositorio privado**. El `.gitignore` ya excluye `.env` y tokens.

### 3. Vercel
1. **Add New → Project** e importa el repositorio. Framework: detecta FastAPI (`app.py`).
2. En **Settings → Environment Variables** agrega las variables de `.env.example` (las secretas como *Sensitive*). Genera `MCP_BEARER_TOKEN`, `AUTH_SETUP_KEY` y `CRON_SECRET` con `openssl rand -hex 32` o un gestor de contraseñas.
3. Despliega y anota la URL de producción, p. ej. `https://ml-mcp-xxx.vercel.app`.
4. En **Deployment Protection** deja **Standard Protection**: protege las URLs de prueba y deja abierto el dominio de producción, que es el que usa tu asistente. **No** uses "All Deployments", o el asistente no podrá llegar al endpoint. La protección del servidor la da el Bearer token.

### 4. Mercado Libre (DevCenter)
1. En tu aplicación, define la **URI de redirect**: `https://TU-PROYECTO.vercel.app/auth/callback` (exacta, HTTPS).
2. Pon la misma URL en `ML_REDIRECT_URI` en Vercel y redespliega.

### 5. Autorizar la cuenta (una vez)
Abre en el navegador, con la sesión de la cuenta dueña del negocio:
```
https://TU-PROYECTO.vercel.app/auth/start?key=TU_AUTH_SETUP_KEY
```
Acepta en Mercado Libre. Verás «Cuenta autorizada». Después puedes borrar `AUTH_SETUP_KEY` de Vercel.

### 6. Conectar Claude Code

**Claude Code en la web (claude.ai/code), recomendado:**
1. El repositorio ya incluye `.mcp.json`, que apunta al servidor sin ningún secreto.
2. Edita tu entorno de nube (selector de entorno → **Default** → editar) → **Network secrets** → **Add secret**:
   - **Name:** `MCP Mercado Libre`
   - **Allowed websites:** `TU-PROYECTO.vercel.app`
   - **Custom headers:** Name `Authorization`, Prefix `Bearer`, Value = tu `MCP_BEARER_TOKEN`
   El proxy de Anthropic agrega el token a cada solicitud hacia ese dominio. El token nunca entra a la sesión y el dominio queda permitido aunque la red esté en **Trusted**.
3. Inicia una sesión con el repositorio **mercadolibre-mcp** y el entorno **Default**.

**Claude Code en tu computador (terminal):**
```bash
claude mcp add --transport http mercadolibre https://TU-PROYECTO.vercel.app/mcp \
  --scope user --header 'Authorization: Bearer TU_MCP_BEARER_TOKEN'
```
Prueba: «Muéstrame mi cuenta de Mercado Libre y mi reputación».

> **claude.ai web/móvil (chat)**: los conectores personalizados de claude.ai usan OAuth, no un header Bearer, así que este servidor funciona en Claude Code (web y terminal). Agregar OAuth para claude.ai es una mejora posible.

## Uso local (opcional)
`python stdio_server.py` expone las mismas herramientas por stdio. Con `SUPABASE_URL` y `SUPABASE_SERVICE_KEY` en `.env` usa los mismos tokens de la nube.

## Pruebas
```bash
pip install -r requirements.txt pytest
python -m pytest -q tests
```
Simulan Supabase y Mercado Libre (sin red): autenticación, protocolo MCP, renovación de tokens (incluida la concurrente), OAuth y guardas de escritura.

## Límites conocidos
- Rutas por validar con la cuenta real: `ml_preguntas`, `ml_ads_campanas`, `ml_buscar_mercado`. Mercado Libre puede responder 403 por permisos funcionales o por restricciones de la búsqueda pública.
- Plan Hobby de Vercel: máximo 300 s por solicitud (los reportes grandes usan límites de paginación).
- La publicidad es solo lectura por ahora.
