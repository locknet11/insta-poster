from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Any

import httpx

from insta_poster.adapters.base import AdapterCheckResult, InstagramAdapter, PublishResult
from insta_poster.auth import sign_media_filename

VALID_GRAPH_HOSTS = {"graph.instagram.com", "graph.facebook.com"}

ACCOUNT_TIMESERIES_METRICS = ("reach", "follower_count")
ACCOUNT_TOTAL_METRICS = (
    "views",
    "accounts_engaged",
    "total_interactions",
    "profile_views",
    "likes",
    "comments",
    "saves",
    "shares",
    "follows_and_unfollows",
    "profile_links_taps",
)
FEED_INSIGHT_METRICS = (
    "views",
    "reach",
    "saved",
    "likes",
    "comments",
    "shares",
    "total_interactions",
    "follows",
    "profile_visits",
    "profile_activity",
)
REELS_INSIGHT_METRICS = (
    "views",
    "reach",
    "saved",
    "likes",
    "comments",
    "shares",
    "total_interactions",
    "ig_reels_avg_watch_time",
    "ig_reels_video_view_total_time",
)
STORY_INSIGHT_METRICS = (
    "views",
    "reach",
    "replies",
    "shares",
    "total_interactions",
    "navigation",
    "follows",
    "profile_visits",
    "profile_activity",
)
MEDIA_LIST_FIELDS = (
    "id,caption,media_type,media_product_type,like_count,comments_count,"
    "timestamp,permalink,thumbnail_url,media_url"
)
STORY_LIST_FIELDS = "id,caption,media_type,media_product_type,timestamp,permalink,thumbnail_url,media_url"


def _extract_metric_int(item: dict[str, Any]) -> int | None:
    total_val = item.get("total_value")
    if isinstance(total_val, dict) and total_val.get("value") is not None:
        try:
            return int(total_val["value"])
        except (TypeError, ValueError):
            pass
    values = item.get("values")
    if isinstance(values, list) and values:
        first = values[0]
        if isinstance(first, dict) and first.get("value") is not None:
            try:
                return int(first["value"])
            except (TypeError, ValueError):
                pass
    return None


def _extract_navigation_breakdown(data: list[dict[str, Any]]) -> dict[str, int]:
    breakdown: dict[str, int] = {}
    for item in data:
        if item.get("name") != "navigation":
            continue
        total_val = item.get("total_value")
        if not isinstance(total_val, dict):
            continue
        for bd in total_val.get("breakdowns") or []:
            for res in bd.get("results") or []:
                dims = res.get("dimension_values") or []
                if not dims:
                    continue
                key = str(dims[0]).strip().lower()
                try:
                    breakdown[key] = int(res.get("value", 0))
                except (TypeError, ValueError):
                    continue
    return breakdown


def _parse_graph_error(status_code: int, payload: dict[str, Any], raw_text: str) -> tuple[int | None, int | None, str]:
    err = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(err, dict):
        return err.get("code"), err.get("error_subcode"), str(err.get("message") or raw_text)
    return None, None, raw_text or f"HTTP {status_code}"


class GraphAPIAdapter(InstagramAdapter):
    """
    Official Meta Instagram API adapter (Business / Creator accounts).

    Supports both:
      - `graph.instagram.com` (Instagram API with Instagram Login — no Facebook Page needed)
      - `graph.facebook.com` (Unified Login / Classic Instagram Graph API)

    Media delivery to Meta supports either:
      - `imgbb_api_key`: Uploads prepared JPEGs temporarily to ImgBB (auto-expires in 10 min; zero tunnels!)
      - `public_base_url`: Serves `/media/prepared/<file>` via a local HTTPS tunnel (cloudflared / localhost.run / ngrok)
    """

    name = "graph_api"
    label = "Instagram Graph API (Oficial)"
    supports = ["post", "carousel", "story"]

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        cfg = config or {}
        self.access_token = (cfg.get("access_token") or os.environ.get("META_ACCESS_TOKEN", "")).strip()
        self.ig_user_id = str(cfg.get("ig_user_id") or os.environ.get("META_IG_USER_ID", "")).strip()
        self.public_base_url = (
            cfg.get("public_base_url") or os.environ.get("PUBLIC_BASE_URL", "")
        ).strip().rstrip("/")
        self.imgbb_api_key = (cfg.get("imgbb_api_key") or os.environ.get("IMGBB_API_KEY", "")).strip()
        self.api_version = (cfg.get("api_version") or os.environ.get("META_GRAPH_API_VERSION") or "v21.0").strip()
        raw_host = (cfg.get("api_host") or os.environ.get("META_GRAPH_API_HOST") or "graph.instagram.com").strip()
        self.api_host = raw_host if raw_host in VALID_GRAPH_HOSTS else "graph.instagram.com"
        self.base_url = f"https://{self.api_host}/{self.api_version}"
        self._media_signing_secret = str(cfg.get("media_signing_secret") or "").strip()

    def _is_instagram_login(self) -> bool:
        return self.api_host == "graph.instagram.com"

    def _media_mode(self) -> str:
        if self.imgbb_api_key:
            return "imgbb"
        if self.public_base_url:
            return "tunnel"
        return "none"

    def _missing_fields(self) -> list[str]:
        missing = []
        if not self.access_token:
            missing.append("access_token")
        if not self.ig_user_id and not self._is_instagram_login():
            missing.append("ig_user_id")
        if not self.imgbb_api_key and not self.public_base_url:
            missing.append("public_base_url (o imgbb_api_key)")
        return missing

    def _resolve_user_id(self, client: httpx.Client) -> str:
        if self.ig_user_id:
            return self.ig_user_id
        if not self._is_instagram_login():
            raise RuntimeError("Falta 'ig_user_id' para graph.facebook.com.")
        resp = client.get(
            f"{self.base_url}/me",
            params={"fields": "user_id,id,username", "access_token": self.access_token},
        )
        data = resp.json()
        if resp.status_code >= 400 or "error" in data:
            err = data.get("error", {}).get("message", resp.text)
            raise RuntimeError(f"No se pudo resolver tu IG User ID desde /me: {err}")
        resolved = str(data.get("user_id") or data.get("id") or "").strip()
        if not resolved:
            raise RuntimeError(f"Meta /me no devolvió user_id: {data}")
        return resolved

    def check_health(self, verify_remote: bool = False) -> AdapterCheckResult:
        missing = self._missing_fields()
        if missing:
            return AdapterCheckResult(
                configured=False,
                available=True,
                message=f"Faltan campos de configuración: {', '.join(missing)}.",
                details={"missing": missing, "api_host": self.api_host, "api_version": self.api_version},
            )

        if not verify_remote:
            return AdapterCheckResult(
                configured=True,
                available=True,
                message=f"Credenciales cargadas ({self.api_host} · media vía {self._media_mode()}). Usá 'Probar conexión' para verificar.",
                details={
                    "ig_user_id": self.ig_user_id or "auto (/me)",
                    "media_mode": self._media_mode(),
                    "public_base_url": self.public_base_url,
                    "api_host": self.api_host,
                    "api_version": self.api_version,
                },
            )

        try:
            with httpx.Client(timeout=15.0) as client:
                if self._is_instagram_login():
                    target = self.ig_user_id or "me"
                    fields = "user_id,id,username,account_type" if target == "me" else "id,username,account_type"
                else:
                    target = self.ig_user_id
                    fields = "id,username,name"

                resp = client.get(
                    f"{self.base_url}/{target}",
                    params={"fields": fields, "access_token": self.access_token},
                )
                data = resp.json()
                if resp.status_code >= 400 or "error" in data:
                    err_msg = data.get("error", {}).get("message", resp.text)
                    return AdapterCheckResult(
                        configured=True,
                        available=False,
                        message=f"Error de Meta ({self.api_host}): {err_msg}",
                        details={"status_code": resp.status_code, "api_host": self.api_host},
                    )

                username = data.get("username") or self.ig_user_id or "cuenta"
                resolved_id = str(data.get("user_id") or data.get("id") or self.ig_user_id)

                delivery_note = ""
                if self.imgbb_api_key:
                    delivery_note = " · Hosting temporal ImgBB activo (sin túnel)."
                elif self.public_base_url:
                    try:
                        t_resp = client.get(f"{self.public_base_url}/api/health", timeout=8.0)
                        if t_resp.status_code != 200 or not t_resp.json().get("ok"):
                            delivery_note = (
                                f" (Advertencia: tu túnel {self.public_base_url}/api/health devolvió HTTP {t_resp.status_code}. "
                                "Reiniciá el túnel o configurá ImgBB API Key.)"
                            )
                    except Exception as t_exc:
                        delivery_note = f" (Advertencia: no se pudo alcanzar tu Public Base URL: {t_exc})"

                return AdapterCheckResult(
                    configured=True,
                    available=True,
                    message=(
                        f"Conectado a @{username} (ID {resolved_id}) vía {self.api_host}/{self.api_version}."
                        f"{delivery_note}"
                    ),
                    details={
                        **data,
                        "resolved_ig_user_id": resolved_id,
                        "api_host": self.api_host,
                        "media_mode": self._media_mode(),
                    },
                )
        except Exception as exc:
            return AdapterCheckResult(
                configured=True,
                available=False,
                message=f"No se pudo contactar a {self.api_host}: {exc}",
            )

    def _upload_to_imgbb(self, client: httpx.Client, image_path: Path) -> str:
        b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        resp = client.post(
            "https://api.imgbb.com/1/upload",
            params={"key": self.imgbb_api_key, "expiration": "600"},
            data={"image": b64, "name": image_path.stem},
            timeout=30.0,
        )
        data = resp.json()
        if resp.status_code >= 400 or not data.get("success"):
            err = data.get("error", {}).get("message") or resp.text
            raise RuntimeError(f"Error al subir imagen temporal a ImgBB: {err}")
        url = data.get("data", {}).get("url")
        if not url:
            raise RuntimeError(f"ImgBB no devolvió URL de imagen: {data}")
        return str(url)

    def _resolve_public_image_url(self, client: httpx.Client, image_path: Path) -> str:
        if self.imgbb_api_key:
            return self._upload_to_imgbb(client, image_path)

        if not self.public_base_url:
            raise RuntimeError(
                "Graph API requiere 'imgbb_api_key' (recomendado, sin túnel) o 'public_base_url' "
                "(túnel HTTPS apuntando a http://127.0.0.1:8000)."
            )

        url = f"{self.public_base_url}/media/prepared/{image_path.name}"
        if self._media_signing_secret:
            exp, sig = sign_media_filename(image_path.name, self._media_signing_secret, ttl_seconds=1800)
            url = f"{url}?exp={exp}&sig={sig}"
        try:
            probe = client.head(url, timeout=10.0, follow_redirects=True)
            ctype = probe.headers.get("content-type", "")
            if probe.status_code != 200 or not ctype.startswith("image/"):
                raise RuntimeError(
                    f"Tu túnel HTTPS ({url}) respondió HTTP {probe.status_code} ({ctype or 'sin content-type'}) "
                    "en vez de una imagen JPEG. Verificá que el túnel siga activo o configurá una ImgBB API Key gratuita."
                )
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"No se pudo acceder a la imagen a través de tu túnel ({url}): {exc}. "
                "Reiniciá el túnel o configurá ImgBB API Key."
            ) from exc
        return url

    def _post_graph(self, client: httpx.Client, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        payload = {**payload, "access_token": self.access_token}
        resp = client.post(f"{self.base_url}/{path.lstrip('/')}", data=payload)
        data = resp.json()
        if resp.status_code >= 400 or "error" in data:
            err = data.get("error", {})
            msg = err.get("message") or resp.text
            raise RuntimeError(f"Meta Graph API error ({resp.status_code}): {msg}")
        return data

    def _wait_container_ready(self, client: httpx.Client, creation_id: str, timeout_s: float = 45.0) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            resp = client.get(
                f"{self.base_url}/{creation_id}",
                params={"fields": "status_code,status", "access_token": self.access_token},
            )
            if resp.status_code == 200:
                data = resp.json()
                code = data.get("status_code")
                if code in (None, "FINISHED", "PUBLISHED"):
                    return
                if code in ("ERROR", "EXPIRED"):
                    raise RuntimeError(f"El contenedor de Meta falló ({code}): {data}")
            time.sleep(1.5)

    def _publish_container(self, client: httpx.Client, ig_user_id: str, creation_id: str) -> PublishResult:
        self._wait_container_ready(client, creation_id)
        published = self._post_graph(
            client,
            f"{ig_user_id}/media_publish",
            {"creation_id": creation_id},
        )
        media_id = str(published["id"])
        permalink = None
        try:
            perm_resp = client.get(
                f"{self.base_url}/{media_id}",
                params={"fields": "permalink", "access_token": self.access_token},
            )
            if perm_resp.status_code == 200:
                permalink = perm_resp.json().get("permalink")
        except Exception:
            pass
        return PublishResult(external_id=media_id, external_url=permalink, raw=published)

    def _ensure_ready(self) -> None:
        missing = self._missing_fields()
        if missing:
            raise RuntimeError(f"Graph API no está configurado. Faltan: {', '.join(missing)}")

    def publish_post(
        self,
        *,
        publication_id: str,
        image_path: Path,
        caption: str,
        metadata: dict[str, Any] | None = None,
    ) -> PublishResult:
        self._ensure_ready()
        with httpx.Client(timeout=30.0) as client:
            ig_user_id = self._resolve_user_id(client)
            image_url = self._resolve_public_image_url(client, image_path)
            container = self._post_graph(
                client,
                f"{ig_user_id}/media",
                {"image_url": image_url, "caption": caption},
            )
            return self._publish_container(client, ig_user_id, str(container["id"]))

    def publish_carousel(
        self,
        *,
        publication_id: str,
        image_paths: list[Path],
        caption: str,
        metadata: dict[str, Any] | None = None,
    ) -> PublishResult:
        self._ensure_ready()
        if len(image_paths) < 2:
            return self.publish_post(
                publication_id=publication_id,
                image_path=image_paths[0],
                caption=caption,
                metadata=metadata,
            )
        with httpx.Client(timeout=45.0) as client:
            ig_user_id = self._resolve_user_id(client)
            child_ids: list[str] = []
            for p in image_paths:
                image_url = self._resolve_public_image_url(client, p)
                child = self._post_graph(
                    client,
                    f"{ig_user_id}/media",
                    {"image_url": image_url, "is_carousel_item": "true"},
                )
                child_id = str(child["id"])
                self._wait_container_ready(client, child_id)
                child_ids.append(child_id)
            parent = self._post_graph(
                client,
                f"{ig_user_id}/media",
                {
                    "media_type": "CAROUSEL",
                    "children": ",".join(child_ids),
                    "caption": caption,
                },
            )
            return self._publish_container(client, ig_user_id, str(parent["id"]))

    def publish_story(
        self,
        *,
        publication_id: str,
        image_path: Path,
        metadata: dict[str, Any] | None = None,
    ) -> PublishResult:
        self._ensure_ready()
        with httpx.Client(timeout=30.0) as client:
            ig_user_id = self._resolve_user_id(client)
            image_url = self._resolve_public_image_url(client, image_path)
            container = self._post_graph(
                client,
                f"{ig_user_id}/media",
                {"media_type": "STORIES", "image_url": image_url},
            )
            return self._publish_container(client, ig_user_id, str(container["id"]))

    # ------------------------------------------------------------------
    # Insights & analytics
    # ------------------------------------------------------------------
    def can_fetch_insights(self) -> bool:
        return bool(self.access_token and (self.ig_user_id or self._is_instagram_login()))

    def _ensure_insights_ready(self) -> None:
        missing = []
        if not self.access_token:
            missing.append("access_token")
        if not self.ig_user_id and not self._is_instagram_login():
            missing.append("ig_user_id")
        if missing:
            raise RuntimeError(f"Graph API no configurado para insights. Faltan: {', '.join(missing)}")

    def _get_graph(
        self,
        client: httpx.Client,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any], str]:
        req_params = {**(params or {}), "access_token": self.access_token}
        resp = client.get(f"{self.base_url}/{path.lstrip('/')}", params=req_params)
        try:
            data = resp.json()
        except Exception:
            data = {}
        return resp.status_code, data if isinstance(data, dict) else {}, resp.text

    def fetch_account_insights(self, days: int = 7) -> dict[str, Any]:
        """Fetch account profile counters, rolling totals, and daily reach/follower time series."""
        self._ensure_insights_ready()
        clamped_days = max(1, min(int(days), 30))
        until_ts = int(time.time())
        since_ts = until_ts - clamped_days * 86400

        with httpx.Client(timeout=20.0) as client:
            ig_user_id = self._resolve_user_id(client)
            prof_status, prof_data, prof_raw = self._get_graph(
                client,
                ig_user_id,
                {"fields": "id,username,name,followers_count,follows_count,media_count"},
            )
            if prof_status >= 400 or "error" in prof_data:
                _, _, msg = _parse_graph_error(prof_status, prof_data, prof_raw)
                raise RuntimeError(f"Error al consultar perfil de Instagram: {msg}")

            totals: dict[str, int] = {}
            daily_map: dict[str, dict[str, int]] = {}
            error_messages: list[str] = []

            # 1. Daily time series (reach, follower_count)
            ts_status, ts_data, ts_raw = self._get_graph(
                client,
                f"{ig_user_id}/insights",
                {
                    "metric": ",".join(ACCOUNT_TIMESERIES_METRICS),
                    "period": "day",
                    "since": since_ts,
                    "until": until_ts,
                },
            )
            permission_missing = False
            if ts_status < 400 and "error" not in ts_data:
                for item in ts_data.get("data") or []:
                    mname = item.get("name")
                    for pt in item.get("values") or []:
                        end_time = str(pt.get("end_time") or "")
                        day_key = end_time[:10] if len(end_time) >= 10 else end_time
                        if not day_key:
                            continue
                        bucket = daily_map.setdefault(day_key, {"reach": 0, "follower_count": 0})
                        try:
                            val = int(pt.get("value", 0))
                        except (TypeError, ValueError):
                            val = 0
                        if mname in ("reach", "follower_count"):
                            bucket[mname] = val
            else:
                code, _, msg = _parse_graph_error(ts_status, ts_data, ts_raw)
                if code == 10:
                    permission_missing = True
                    error_messages.append(
                        "Falta el permiso instagram_manage_insights (o instagram_business_manage_insights) en el token."
                    )
                else:
                    error_messages.append(msg)

            # 2. Rolling total_value metrics
            if not permission_missing:
                tv_status, tv_data, tv_raw = self._get_graph(
                    client,
                    f"{ig_user_id}/insights",
                    {
                        "metric": ",".join(ACCOUNT_TOTAL_METRICS),
                        "period": "day",
                        "metric_type": "total_value",
                        "since": since_ts,
                        "until": until_ts,
                    },
                )
                if tv_status < 400 and "error" not in tv_data:
                    for item in tv_data.get("data") or []:
                        mname = item.get("name")
                        val = _extract_metric_int(item)
                        if mname and val is not None:
                            totals[mname] = val
                else:
                    code, _, msg = _parse_graph_error(tv_status, tv_data, tv_raw)
                    if code == 10:
                        error_messages.append(
                            "Falta el permiso instagram_manage_insights (o instagram_business_manage_insights) en el token."
                        )
                    else:
                        # Fallback per-metric so unsupported single metrics don't drop everything
                        for m in ACCOUNT_TOTAL_METRICS:
                            m_st, m_data, _ = self._get_graph(
                                client,
                                f"{ig_user_id}/insights",
                                {
                                    "metric": m,
                                    "period": "day",
                                    "metric_type": "total_value",
                                    "since": since_ts,
                                    "until": until_ts,
                                },
                            )
                            if m_st < 400 and "error" not in m_data:
                                for item in m_data.get("data") or []:
                                    val = _extract_metric_int(item)
                                    if val is not None:
                                        totals[m] = val

            daily_series = [
                {
                    "date": day_key,
                    "reach": daily_map[day_key]["reach"],
                    "follower_count": daily_map[day_key]["follower_count"],
                }
                for day_key in sorted(daily_map.keys())
            ]
            if daily_series:
                totals["reach"] = sum(pt["reach"] for pt in daily_series)
                totals["follower_delta"] = sum(pt["follower_count"] for pt in daily_series)

            return {
                "period_days": clamped_days,
                "ig_user_id": ig_user_id,
                "username": str(prof_data.get("username") or ""),
                "name": str(prof_data.get("name") or ""),
                "followers_count": prof_data.get("followers_count"),
                "follows_count": prof_data.get("follows_count"),
                "media_count": prof_data.get("media_count"),
                "totals": totals,
                "daily_series": daily_series,
                "error_message": "; ".join(dict.fromkeys(error_messages)) or None,
            }

    def fetch_account_media(self, limit: int = 50) -> list[dict[str, Any]]:
        """List recent posts, carousels, and reels from the Instagram account."""
        self._ensure_insights_ready()
        with httpx.Client(timeout=20.0) as client:
            ig_user_id = self._resolve_user_id(client)
            status, data, raw = self._get_graph(
                client,
                f"{ig_user_id}/media",
                {"fields": MEDIA_LIST_FIELDS, "limit": max(1, min(int(limit), 100))},
            )
            if status >= 400 or "error" in data:
                _, _, msg = _parse_graph_error(status, data, raw)
                raise RuntimeError(f"Error al listar publicaciones de Instagram: {msg}")
            return list(data.get("data") or [])

    def fetch_active_stories(self) -> list[dict[str, Any]]:
        """List currently active (<24h) stories from the Instagram account."""
        self._ensure_insights_ready()
        with httpx.Client(timeout=20.0) as client:
            ig_user_id = self._resolve_user_id(client)
            status, data, raw = self._get_graph(
                client,
                f"{ig_user_id}/stories",
                {"fields": STORY_LIST_FIELDS},
            )
            if status >= 400 or "error" in data:
                _, _, msg = _parse_graph_error(status, data, raw)
                raise RuntimeError(f"Error al listar historias activas de Instagram: {msg}")
            return list(data.get("data") or [])

    def fetch_media_insights(
        self,
        external_id: str,
        *,
        media_product_type: str = "FEED",
        media_type: str = "IMAGE",
        fallback_likes: int | None = None,
        fallback_comments: int | None = None,
        include_metadata: bool = False,
    ) -> dict[str, Any]:
        """Fetch lifetime insights (and story navigation breakdown) for a post, carousel, reel, or story."""
        self._ensure_insights_ready()
        product = (media_product_type or "FEED").upper()
        if product == "STORY":
            metric_list = STORY_INSIGHT_METRICS
        elif product == "REELS":
            metric_list = REELS_INSIGHT_METRICS
        else:
            metric_list = FEED_INSIGHT_METRICS

        metrics: dict[str, int] = {}
        extra_metrics: dict[str, Any] = {}
        navigation_breakdown: dict[str, int] = {}
        metadata: dict[str, Any] = {}
        expired = False
        error_message: str | None = None

        with httpx.Client(timeout=20.0) as client:
            if include_metadata:
                meta_fields = STORY_LIST_FIELDS if product == "STORY" else MEDIA_LIST_FIELDS
                m_st, m_data, m_raw = self._get_graph(client, external_id, {"fields": meta_fields})
                if m_st < 400 and "error" not in m_data:
                    metadata = m_data
                    if m_data.get("media_product_type"):
                        product = str(m_data["media_product_type"]).upper()
                        if product == "STORY":
                            metric_list = STORY_INSIGHT_METRICS
                        elif product == "REELS":
                            metric_list = REELS_INSIGHT_METRICS
                        else:
                            metric_list = FEED_INSIGHT_METRICS
                    if fallback_likes is None and m_data.get("like_count") is not None:
                        fallback_likes = int(m_data["like_count"])
                    if fallback_comments is None and m_data.get("comments_count") is not None:
                        fallback_comments = int(m_data["comments_count"])
                else:
                    code, subcode, msg = _parse_graph_error(m_st, m_data, m_raw)
                    if code == 100 and subcode == 33:
                        expired = True
                        error_message = (
                            "Historia expirada (>24h) o contenido eliminado en Instagram; se conservan los últimos datos."
                        )
                        return {
                            "metrics": {},
                            "extra_metrics": {},
                            "navigation_breakdown": {},
                            "metadata": {},
                            "expired": True,
                            "error_message": error_message,
                        }

            # Batch request for insights
            ins_st, ins_data, ins_raw = self._get_graph(
                client,
                f"{external_id}/insights",
                {"metric": ",".join(metric_list)},
            )
            if ins_st < 400 and "error" not in ins_data:
                for item in ins_data.get("data") or []:
                    mname = item.get("name")
                    val = _extract_metric_int(item)
                    if mname and val is not None:
                        metrics[mname] = val
            else:
                code, subcode, msg = _parse_graph_error(ins_st, ins_data, ins_raw)
                lower_msg = msg.lower()
                if code == 100 and subcode == 33:
                    expired = True
                    error_message = (
                        "Historia expirada (>24h) o contenido eliminado en Instagram; se conservan los últimos datos."
                    )
                elif code == 10 and "not enough viewers" in lower_msg:
                    error_message = "Aún no hay suficientes espectadores (<5) para mostrar insights en Meta."
                elif code == 10:
                    error_message = (
                        "Falta el permiso instagram_manage_insights (o instagram_business_manage_insights) en el token."
                    )
                else:
                    # Try individual metrics in case one metric isn't supported for this media subtype
                    for m in metric_list:
                        s_st, s_data, _ = self._get_graph(client, f"{external_id}/insights", {"metric": m})
                        if s_st < 400 and "error" not in s_data:
                            for item in s_data.get("data") or []:
                                val = _extract_metric_int(item)
                                if val is not None:
                                    metrics[m] = val
                    if not metrics:
                        error_message = msg

            # Story navigation breakdown (tap_forward, tap_back, tap_exit, swipe_forward)
            if product == "STORY" and not expired and "navigation" in metrics:
                bd_st, bd_data, _ = self._get_graph(
                    client,
                    f"{external_id}/insights",
                    {"metric": "navigation", "breakdown": "story_navigation_action_type"},
                )
                if bd_st < 400 and "error" not in bd_data:
                    navigation_breakdown = _extract_navigation_breakdown(list(bd_data.get("data") or []))

        # Keep extra Reels/non-standard metrics separately
        known_cols = {
            "views",
            "reach",
            "likes",
            "comments",
            "saved",
            "shares",
            "replies",
            "total_interactions",
            "follows",
            "profile_visits",
            "profile_activity",
            "navigation",
        }
        for k, v in list(metrics.items()):
            if k not in known_cols:
                extra_metrics[k] = v

        if "likes" not in metrics and fallback_likes is not None:
            metrics["likes"] = fallback_likes
        if "comments" not in metrics and fallback_comments is not None:
            metrics["comments"] = fallback_comments

        return {
            "metrics": {k: v for k, v in metrics.items() if k in known_cols},
            "extra_metrics": extra_metrics,
            "navigation_breakdown": navigation_breakdown,
            "metadata": metadata,
            "expired": expired,
            "error_message": error_message,
        }
