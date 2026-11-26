# Agent Guide — `insta-poster`

Este documento está diseñado para que cualquier agente de IA (Claude Code, Codex, Antigravity, scripts autónomos o Code Mode) pueda operar **Insta-Poster** mediante **CLI**, **REST API** o **Python SDK**.

---

## 1. Flujo recomendado para un agente

1. **Ver estado del sistema y adaptador activo:**
   ```bash
   uv run insta-poster status --json
   ```
2. **Elegir imágenes candidatas del pool (sin repetir):**
   ```bash
   # Para un post de feed
   uv run insta-poster pool pick --type post --unused --count 1 --json

   # Para una historia (9:16)
   uv run insta-poster pool pick --type story --unused --count 1 --json
   ```
   Cada resultado incluye `filepath` (ruta local absoluta), dimensiones (`width`, `height`), `tags` y `notes`. Si sos un agente multimodal, podés abrir `filepath` para analizar la imagen y redactar el caption.
3. **(Opcional) Guardar tu descripción/tags en el pool para futuras corridas:**
   ```bash
   uv run insta-poster pool update <IMAGE_ID> --notes "Café latte sobre mesa de madera" --tags "cafe,mañana" --json
   ```
4. **Publicar o programar un Post, Carrusel o Historia:**
   ```bash
   # Post inmediato
   uv run insta-poster post create --image <ID> --caption "Buen día ☕" --hashtags "#cafe,#mañana" --now --json

   # Carrusel programado (múltiples --image)
   uv run insta-poster post create --image <ID1> --image <ID2> --caption "Detrás de escena" --schedule "2026-11-25T18:00:00Z" --json

   # Historia inmediata (auto-adapta a 9:16 con fondo blur si hace falta)
   uv run insta-poster story create --image <ID> --fit pad_blur --now --json
   ```

5. **Consultar métricas (Insights) de cuenta, posts e historias:**
   ```bash
   # Sincronizar métricas desde Meta Graph API (incluye historias activas <24h)
   uv run insta-poster insights sync --days 7 --json

   # Ver ranking de contenido por vistas o interacciones
   uv run insta-poster insights overview --days 7 --sort views --json

   # Ver detalle e historial de snapshots de un post o historia
   uv run insta-poster insights show <PUB_OR_MEDIA_ID> --history --json
   ```

---

## 2. REST API (con el servidor corriendo en `http://127.0.0.1:8000`)

- Documentación interactiva: `GET /docs`
- Esquema OpenAPI: `GET /openapi.json`
- Resumen compacto para contexto: `GET /api/agent/schema`

Endpoints principales:
- `GET /api/pool?status=available&unused_for=post|story&tag=...`
- `GET /api/pool/pick?pub_type=post|story&count=1&unused_only=true`
- `POST /api/pool/sync` (`{"folder_path": "/ruta/carpeta", "tags": ["campaña"]}`)
- `POST /api/publications` (`{"pub_type": "post"|"carousel"|"story", "image_ids": [...], "caption": "...", "hashtags": [...], "fit_mode": "auto"|"pad_blur"|"crop"|"pad_solid", "publish_now": true}`)
- `POST /api/publications/{id}/publish`
- `GET /api/publications?status=scheduled`
- `GET /api/insights?days=7&media_product_type=FEED|STORY|REELS&sort_by=views`
- `POST /api/insights/sync?days=7&stories_only=false`
- `GET /api/insights/media/{identifier}/history`
- `POST /api/publications/{id}/insights/sync`

> **Autenticación REST API:** todos los endpoints `/api/*` (salvo `/api/health`) requieren autenticación. Podés autenticarte de dos formas:
> 1. **API Key (recomendado para agentes):** creá una clave (`ipk_...`) desde la Web UI (**Usuarios → API Keys**), por CLI (`insta-poster apikey create mi-agente`) o vía `INSTA_POSTER_API_KEY`, y enviala en `X-API-Key: <tu-clave>` o `Authorization: Bearer <tu-clave>`.
> 2. **Token de usuario:** hacé `POST /api/auth/login` con `{"username": "...", "password": "..."}` y enviá `Authorization: Bearer <token>`.
>
> También podés enviar `X-Actor: mi-agente` para sobrescribir el nombre registrado en el log de actividad (por defecto se usa el nombre de la API Key).

---

## 3. Code Mode (Python SDK)

```python
from insta_poster import InstaPoster

# Opción A: directo sobre SQLite local (sin servidor)
ip = InstaPoster.local()

# Opción B: contra el servidor local en ejecución
# ip = InstaPoster.http("http://127.0.0.1:8000")

# 1. Elegir imagen no usada para Story y otra para Post
story_img = ip.pick_images(pub_type="story", unused_only=True)[0]
post_img = ip.pick_images(pub_type="post", unused_only=True)[0]

# 2. Publicar historia y programar post
story = ip.create_story(story_img["id"], fit_mode="pad_blur", publish_now=True)
post = ip.create_post(
    post_img["id"],
    caption="Nuevo drop disponible hoy.",
    hashtags=["#novedad", "#instaposter"],
    scheduled_at="2026-11-26T20:00:00Z",
)
print(story["status"], post["id"])
```
