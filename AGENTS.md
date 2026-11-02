# Instrucciones para el asistente de IA: instalación del MCP de Mercado Libre

> Este archivo lo leen automáticamente Codex (`AGENTS.md`), Claude Code (vía `CLAUDE.md`) y Gemini CLI
> (vía `GEMINI.md`). En un chat sin acceso al repositorio, la persona puede subir este archivo.

Este repositorio es un servidor MCP que conecta un asistente de IA (Claude, Gemini o Codex) con una
cuenta de vendedor de Mercado Libre.
Cuando la persona pida ayuda para configurarlo ("ayúdame a configurar esto", "quiero conectar mi
Mercado Libre", "instálalo"), **tú eres su guía**, seas el asistente que seas. Asume que no es programadora y que no leerá
documentación: todo lo que necesita saber se lo dices tú, en el momento justo.

## Cómo guiar

- Responde en el idioma de la persona (por defecto, español neutro).
- **Un paso a la vez.** Explica qué hacer, dónde hacer clic y qué escribir. Espera a que confirme
  antes de seguir. Si manda una captura, revísala y dile exactamente qué marcar.
- Antes de empezar, muestra un mapa breve: "Son 5 etapas, unos 30 a 45 minutos".
- Usa un checklist (lista de tareas) con las etapas y ve marcando el avance.
- Verifica cada etapa antes de avanzar (ver "Verificaciones"). No des algo por hecho sin comprobarlo.
- Si algo falla, busca primero en "Errores conocidos" antes de improvisar.
- Celebra los hitos, pero sin relleno.

## Reglas de seguridad (obligatorias)

1. **Nunca pidas que pegue secretos en el chat**: Secret Key de Mercado Libre, clave `sb_secret_` de
   Supabase, `MCP_BEARER_TOKEN`, `AUTH_SETUP_KEY`, `CRON_SECRET` ni tokens. Indícale dónde pegarlos
   (Vercel, Supabase, el archivo `.env` en su editor, el "network secret" de Claude Code web o la configuración de su asistente).
2. Si igual pega un secreto en el chat (o en una captura), avísale con calma y pídele que lo cambie
   (genera uno nuevo y reemplázalo donde corresponda).
3. Para generar claves al azar, pide **solo letras y números**: `#`, `&`, `$` o `!` rompen URLs o comandos.
   Si tienes terminal, puedes sugerir `openssl rand -hex 32`. La persona copia el valor directo a su destino.
4. Nunca subas `.env` ni `tokens.json` a GitHub (ya están en `.gitignore`). El repositorio de la
   persona debe ser **privado**.
5. Las acciones de escritura del MCP (precio, stock, publicar, responder) siempre pasan por la vista
   previa con `codigo_confirmacion`. Muéstrale la vista previa y ejecuta solo con su aprobación explícita.

## Etapa 0: preguntas iniciales

Pregunta (de a una, o en un solo mensaje corto):
1. **País** donde vende (ver tabla de países).
2. **Camino**:
   - **Nube (recomendado):** funciona desde cualquier equipo (Claude Code web o móvil, Gemini CLI, Codex). Necesita
     cuentas gratuitas en GitHub, Vercel y Supabase.
   - **Local:** más simple, pero solo funciona con su computador encendido (Claude Code en terminal,
     Claude Desktop, Gemini CLI o Codex). Necesita Python 3.10 o superior.
3. **Qué asistente usará** con el MCP: Claude Code (web o terminal), Claude Desktop, Gemini CLI o Codex.
   Ver "Compatibilidad" más abajo: ChatGPT web y el chat de claude.ai todavía no son compatibles.
4. Si es su cuenta de vendedor **dueña** (no un usuario colaborador) y si tiene los **datos del titular
   validados** en Mercado Libre (en Chile es requisito para crear aplicaciones).

### Tabla de países

| País | `ML_SITE` | `ML_AUTH_HOST` | DevCenter |
|---|---|---|---|
| Chile | MLC | https://auth.mercadolibre.cl | developers.mercadolibre.cl/devcenter |
| Argentina | MLA | https://auth.mercadolibre.com.ar | developers.mercadolibre.com.ar/devcenter |
| México | MLM | https://auth.mercadolibre.com.mx | developers.mercadolibre.com.mx/devcenter |
| Colombia | MCO | https://auth.mercadolibre.com.co | developers.mercadolibre.com.co/devcenter |
| Perú | MPE | https://auth.mercadolibre.com.pe | developers.mercadolibre.com.pe/devcenter |
| Uruguay | MLU | https://auth.mercadolibre.com.uy | developers.mercadolibre.com.uy/devcenter |
| Brasil | MLB | https://auth.mercadolivre.com.br | developers.mercadolivre.com.br/devcenter |

## Etapa 1: crear la aplicación en el DevCenter de Mercado Libre

En el camino nube, haz primero la **Etapa 2 (Vercel)** para conocer la URL del proyecto y usarla como
URI de redirect. También puede crear la aplicación ahora con una URI provisional y editarla después.

Guíalo por el formulario "Crear nueva aplicación":
- **Nombre** único (sin "Mercado Libre"), **nombre corto**, **descripción** de hasta 150 caracteres y logo.
- **URI de redirect** exacta y en HTTPS, sin barra final:
  - Nube: `https://SU-PROYECTO.vercel.app/auth/callback`
  - Local: cualquier URL HTTPS que controle (por ejemplo, su sitio web). La página puede dar 404:
    solo se copia el código de la barra de direcciones.
- **Flujos OAuth:** ✅ Authorization Code, ✅ **Refresh Token** (sin este, deja de funcionar a las 6 horas),
  ⬜ Client Credentials.
- **PKCE:** ✅ activado.
- **Negocios:** ✅ Mercado Libre, ⬜ VIS (clasificados). Nada de Mercado Pago: desde el 30/08/2026 las
  aplicaciones de Mercado Libre y Mercado Pago deben ir separadas.
- **Permisos:**

  | Permiso | Nivel |
  |---|---|
  | Usuarios | Lectura y escritura (fijo) |
  | Comunicaciones pre y post ventas | Lectura y escritura |
  | Publicación y sincronización | Lectura y escritura |
  | Publicidad de un producto | Lectura (escritura solo si gestionará campañas) |
  | Facturación de una venta | Lectura |
  | Métricas del negocio | Lectura |
  | Promociones, cupones y descuentos | Lectura (o lectura y escritura si las gestionará) |
  | Venta y envíos de un producto | Lectura |

- **Tópicos** y **URL de notificaciones:** vacíos (este servidor no recibe webhooks).
- **Vista previa de scopes:** deben aparecer `read`, `write` y `offline_access`.
- Al crearla, verá el **App ID** (Client ID, no es secreto) y la **Secret Key** (secreta). La "Configuración
  de seguridad" con puntaje es una recomendación: que no restrinja por IP, porque Vercel usa IPs variables.

## Etapa 2 (nube): Supabase, GitHub y Vercel

### 2a. Supabase
1. Crear un proyecto gratuito. Sugiere la región más cercana (Sudamérica: São Paulo, `sa-east-1`).
2. En **SQL Editor**, pegar y ejecutar el contenido de `supabase/migrations/20261030000000_ml_tokens.sql`.
   Si tienes un conector o herramienta de Supabase disponible, puedes aplicar la migración tú, con su aprobación.
3. Copiar **Project URL** y la clave **secret** (`sb_secret_…`, en Project Settings → API Keys →
   "Publishable and secret API keys"). **No** la *publishable*.
4. Es normal que el revisor de seguridad de Supabase muestre "RLS enabled, no policy": así solo el
   servidor puede leer los tokens.

### 2b. GitHub
- Su copia de este repositorio debe ser **privada**. Opciones: "Use this template" → Private, o crear un
  repositorio privado y subir el código. Si tienes acceso a GitHub (CLI `gh` o conector), puedes hacerlo
  tú, con su aprobación.
- Edita `.mcp.json` y reemplaza `TU-PROYECTO` por el nombre real del proyecto en Vercel (si aún no lo
  conoce, hazlo después de 2c). Haz commit y push.

### 2c. Vercel
1. **Add New → Project**, importar el repositorio (Vercel detecta FastAPI). Anotar la URL de producción
   `https://SU-PROYECTO.vercel.app`.
2. Si el negocio no está en Sudamérica, cambia `regions` en `vercel.json` a una región cercana a su
   proyecto de Supabase (São Paulo es `gru1`).
3. **Settings → Environment Variables**, entorno **Production**. Las marcadas como Secret se crean con
   tipo **Secret**, y el valor no se puede volver a ver:

   | Variable | Valor | Tipo |
   |---|---|---|
   | `ML_CLIENT_ID` | App ID | Normal |
   | `ML_CLIENT_SECRET` | Secret Key | **Secret** |
   | `ML_REDIRECT_URI` | `https://SU-PROYECTO.vercel.app/auth/callback` | Normal |
   | `ML_SITE` | según su país | Normal |
   | `ML_AUTH_HOST` | según su país | Normal |
   | `ML_USE_PKCE` | `1` | Normal |
   | `ML_READ_ONLY` | `0` (o `1` si solo quiere analizar) | Normal |
   | `SUPABASE_URL` | Project URL | Normal |
   | `SUPABASE_SERVICE_KEY` | `sb_secret_…` | **Secret** |
   | `MCP_BEARER_TOKEN` | al azar, 40 o más caracteres, solo letras y números | **Secret** |
   | `AUTH_SETUP_KEY` | al azar, 20 o más caracteres, solo letras y números | **Secret** |
   | `CRON_SECRET` | al azar, 20 o más caracteres, solo letras y números | **Secret** |

   Recuérdale **guardar** `MCP_BEARER_TOKEN` y `AUTH_SETUP_KEY` en su gestor de contraseñas antes de
   pulsar Save. Si se le "pega" el guardado de una variable, puede crear las demás una por una.
4. **Deployments → último → ⋯ → Redeploy**, para que tome las variables.
5. **Deployment Protection:** dejar **Standard Protection**. Protege las URLs de prueba y deja abierto el
   dominio de producción, que es el que usa el asistente. **No** usar "All Deployments".

Si tienes conectores de Vercel o Supabase disponibles, puedes crear proyectos y cargar las variables
**no secretas** tú mismo, con su aprobación. Las secretas siempre las carga la persona.

## Etapa 3 (nube): autorizar la cuenta

1. Con la sesión de Mercado Libre abierta en su navegador (cuenta dueña), que abra:
   `https://SU-PROYECTO.vercel.app/auth/start?key=SU_AUTH_SETUP_KEY`
2. Acepta los permisos y debe ver **"Cuenta autorizada"** con un número de cuenta.
3. Que agregue ese número como `ML_EXPECTED_USER_ID` (Normal, Production) y haga Redeploy. Así el
   servidor solo acepta su cuenta.
4. Opcional: borrar `AUTH_SETUP_KEY`, lo que desactiva la página de autorización. Si algún día hay que
   reautorizar, se crea de nuevo.

## Etapa 4 (nube): conectar el asistente

Usa la sección del asistente que eligió. En todos los casos el servidor es
`https://SU-PROYECTO.vercel.app/mcp` y se autentica con `Authorization: Bearer SU_MCP_BEARER_TOKEN`.
Los comandos con el token real los escribe **la persona** en su terminal, no tú.

### Claude Code web (claude.ai/code), recomendado
1. `.mcp.json` ya apunta a `https://SU-PROYECTO.vercel.app/mcp`, sin claves (paso 2b).
2. Selector de entorno → editar su entorno (por ejemplo, "Default") → **Network secrets** → **Add secret**:
   - Secret type: **Bearer**
   - Allowed websites: `SU-PROYECTO.vercel.app`
   - Custom headers: Name `Authorization`, Prefix `Bearer`, Value = su `MCP_BEARER_TOKEN`
   La plataforma agrega el token sin que la sesión lo vea y permite ese dominio aunque la red esté en "Trusted".
3. Abrir una **sesión nueva** con ese entorno y su repositorio, y aprobar el servidor MCP del proyecto si
   se lo pregunta.

### Claude Code en terminal
```bash
claude mcp add --transport http mercadolibre https://SU-PROYECTO.vercel.app/mcp --scope user --header 'Authorization: Bearer SU_MCP_BEARER_TOKEN'
```
Verificar con `claude mcp list`.

### Gemini CLI
```bash
gemini mcp add --scope user --transport http -H 'Authorization: Bearer SU_MCP_BEARER_TOKEN' mercadolibre https://SU-PROYECTO.vercel.app/mcp
```
Queda guardado en `~/.gemini/settings.json`. Verificar con `gemini mcp list` (o `/mcp list` dentro de Gemini).
Sin `--scope user` se guardaría en el proyecto (`.gemini/settings.json`): que **no** suba ese archivo a GitHub,
porque contendría el token.

### Codex (CLI, app o extensión del IDE)
1. Agregar a `~/.codex/config.toml`:
   ```toml
   [mcp_servers.mercadolibre]
   url = "https://SU-PROYECTO.vercel.app/mcp"
   bearer_token_env_var = "ML_MCP_TOKEN"
   ```
2. Definir la variable `ML_MCP_TOKEN` con su `MCP_BEARER_TOKEN` en su perfil de terminal (por ejemplo
   `export ML_MCP_TOKEN='...'` en `~/.zshrc` o `~/.bashrc`; en Windows, variables de entorno de usuario).
   Abrir una terminal nueva.
3. Verificar con `codex mcp list`.

### Compatibilidad (decírselo con claridad)
| Asistente | Nube (Vercel) | Local |
|---|---|---|
| Claude Code (web y terminal) | ✅ | ✅ (terminal) |
| Claude Desktop | ⚠️ los conectores remotos personalizados piden OAuth: usar el camino local | ✅ |
| Gemini CLI | ✅ | ✅ |
| Codex (CLI/IDE) | ✅ | ✅ |
| ChatGPT web / chat de claude.ai | ❌ por ahora: solo aceptan conectores con OAuth o sin autenticación, y este servidor usa token | ❌ |

Si alguien necesita ChatGPT web, la mejora sería agregar OAuth al servidor (no está implementado).

## Camino local (en vez de las etapas 2 a 4)

Si tienes terminal en su computador, ejecuta tú los comandos (con su aprobación). Si no, dáselos uno a uno.
1. Crear el entorno:
   ```bash
   python -m venv .venv
   source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   cp .env.example .env             # Windows: copy .env.example .env
   ```
2. Que **la persona** abra `.env` en su editor y complete `ML_CLIENT_ID`, `ML_CLIENT_SECRET`,
   `ML_REDIRECT_URI`, `ML_SITE` y `ML_AUTH_HOST`. Las variables de Supabase quedan vacías: los tokens
   irán a `tokens.json`. Tú no leas ni muestres el contenido de `.env`.
3. Autorizar: `python auth_setup.py`. Abre la URL que imprime, acepta y pega en la terminal la URL
   completa a la que lo redirigió (aunque muestre 404). Debe imprimir "Listo. Cuenta (user_id)".
   Si no aparece `offline_access` en los scopes, falta el flujo Refresh Token en la aplicación.
4. Conectar el asistente (rutas absolutas; en Windows el Python es `.venv\Scripts\python.exe`):
   - Claude Code: `claude mcp add mercadolibre --scope user -- /RUTA/.venv/bin/python /RUTA/stdio_server.py`
   - Gemini CLI: `gemini mcp add --scope user mercadolibre /RUTA/.venv/bin/python /RUTA/stdio_server.py`
   - Codex: `codex mcp add mercadolibre -- /RUTA/.venv/bin/python /RUTA/stdio_server.py`
   - Claude Desktop: Configuración → Desarrollador → Editar configuración, con
     `"command": "/RUTA/.venv/bin/python"` y `"args": ["/RUTA/stdio_server.py"]`.
5. No usar dos clientes a la vez con el mismo `tokens.json`: el refresh token es de un solo uso.

## Etapa 5: primera prueba (todo en solo lectura)

En una sesión **nueva**, que pregunte "Muéstrame mi cuenta de Mercado Libre y mi reputación". Si
responde con su nickname y su reputación, funciona. Luego, de a una:
1. "Lista mis publicaciones activas con precio, stock y ventas."
2. "Hazme un resumen de ventas de los últimos 90 días."
3. "Corre el reporte de conversión de los últimos 30 días."
4. "¿Tengo preguntas sin responder?"
5. "Muéstrame mis campañas de Product Ads de los últimos 30 días."
6. "Muéstrame el resumen de facturación del último período."

Cierra con 3 o 4 ideas de uso según lo que viste (stock pausado, conversión baja, costos de Mercado Libre),
y recuérdale que el margen real necesita el **costo de cada producto**, que solo conoce la persona.

## Verificaciones

| Etapa | Cómo comprobar |
|---|---|
| Código | `pip install -r requirements.txt && python -m pytest -q tests` (todas deben pasar; `pytest` se instala aparte) |
| Vercel arriba | `https://SU-PROYECTO.vercel.app/health` responde `{"servicio":"mercadolibre-mcp","estado":"ok"}` |
| Página de autorización protegida | `/auth/start` sin `?key=` responde 404 (es lo esperado) |
| Cuenta autorizada | Mensaje "Cuenta autorizada"; en Supabase, `ml_tokens` tiene `access_token` no nulo (consulta solo si es nulo o no, nunca muestres el token) |
| MCP conectado | `claude mcp list` / `gemini mcp list` / `codex mcp list` lo muestra conectado; aparecen ~30 herramientas `ml_*` y `ml_mi_cuenta` responde |

## Errores conocidos

| Síntoma | Causa | Solución |
|---|---|---|
| 404 al abrir `/auth/start?key=…` | Clave con `#`, `&` u otro símbolo, o mal copiada | Nueva clave solo con letras y números → editar `AUTH_SETUP_KEY` → Redeploy |
| Error `redirect_uri` al autorizar | URI distinta entre DevCenter y `ML_REDIRECT_URI` | Deben ser idénticas, carácter por carácter |
| "Estado inválido o ya utilizado" | Se recargó el callback o pasaron más de 10 minutos | Abrir `/auth/start?key=…` de nuevo |
| "La cuenta autorizada no es la esperada" | `ML_EXPECTED_USER_ID` no coincide | Autorizar con la cuenta dueña correcta o corregir la variable |
| Deja de funcionar a las 6 horas | Falta el flujo Refresh Token / `offline_access` | Activarlo en la aplicación y reautorizar |
| 401 desde el asistente | Falta el token o no coincide con `MCP_BEARER_TOKEN` | Revisar el network secret, el header `-H` o la variable `ML_MCP_TOKEN` (en Codex, abrir una terminal nueva) |
| El asistente no llega al servidor | Vercel con "All Deployments" | Usar Standard Protection |
| Supabase rechaza la clave | Se usó la *publishable*, o la clave no es la `sb_secret_` | Usar la clave secret (el código ya la envía solo en `apikey`) |
| 403 en `ml_buscar_mercado` o `ml_mas_vendidos` | Restricción general de Mercado Libre | No tiene arreglo por permisos. Para competencia, usar IDs de publicaciones con `ml_publicaciones_competencia` |
| `ml_referencia_precio` sin datos | Productos sin equivalente de catálogo (kits propios) | Es normal. Usar comparación por IDs |
| `ModuleNotFoundError: mcp.server.fastmcp` | Se instaló `mcp` 2.x | `requirements.txt` fija `mcp<2`: reinstalar |
| Cambios de código no se ven | Vercel no redesplegó | Revisar Deployments; push a la rama principal |

## Mantenimiento (para cuando la persona vuelva)

- Reautorizar (etapa 3) si cambia su contraseña de Mercado Libre, cambia los permisos de la aplicación o el
  MCP responde "no autorizado".
- La tarea diaria `/cron/keepalive` (Vercel Cron, protegida con `CRON_SECRET`) evita que Supabase se
  pause y renueva el token. Revisar en Vercel → Cron Jobs.
- Para agregar herramientas: editar `ml_tools.py`, agregar pruebas en `tests/`, correr `pytest` y hacer push.
  Toda herramienta que escriba en la cuenta debe usar `_guarded_write` (vista previa con código firmado).

## Arquitectura (referencia rápida)

- `app.py`: servidor HTTP (FastAPI) para Vercel: `/mcp` (JSON-RPC, exige Bearer), `/auth/start`,
  `/auth/callback`, `/cron/keepalive`, `/health`.
- `ml_tools.py`: las herramientas MCP. `ml_client.py`: cliente de la API y almacén de tokens
  (Supabase o archivo). `oauth_flow.py`: autorización por navegador. `stdio_server.py` y
  `auth_setup.py`: uso local.
- El refresh token es de un solo uso. La renovación usa un "lease" en Supabase para que solicitudes
  simultáneas no lo pierdan.
