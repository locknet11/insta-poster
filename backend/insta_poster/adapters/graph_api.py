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
