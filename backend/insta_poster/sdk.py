from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from insta_poster.config import AppPaths
from insta_poster.models import PoolImageUpdate, PublicationCreate, PublicationUpdate
from insta_poster.service import InstaPosterService


class InstaPoster:
    """
    Ergonomic Python SDK designed for AI agents in Code Mode & automation scripts.

    Works in two modes:
      - `InstaPoster.local()` -> Direct SQLite/filesystem access (no server needed)
      - `InstaPoster.http("http://127.0.0.1:8000")` -> Calls the running local webapp REST API
    """

    def __init__(
        self,
        *,
        service: InstaPosterService | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        token: str | None = None,
        username: str | None = None,
        password: str | None = None,
        actor: str = "code-agent",
    ) -> None:
        self._service = service
        self._base_url = base_url.rstrip("/") if base_url else None
        self._api_key = api_key
        self._token = token
        self._username = username
        self._password = password
        self._actor = actor

    @classmethod
    def local(cls, data_dir: str | Path | None = None, *, actor: str = "code-agent") -> "InstaPoster":
        paths = AppPaths.from_data_dir(Path(data_dir) if data_dir else None)
        return cls(service=InstaPosterService(paths), actor=actor)

    @classmethod
    def http(
        cls,
        base_url: str = "http://127.0.0.1:8000",
        *,
        api_key: str | None = None,
        token: str | None = None,
        username: str | None = None,
        password: str | None = None,
        actor: str = "code-agent",
    ) -> "InstaPoster":
        return cls(
            base_url=base_url,
            api_key=api_key,
            token=token,
            username=username,
            password=password,
            actor=actor,
        )

    def _ensure_http_token(self) -> None:
        if self._token or self._api_key or not (self._username and self._password):
            return
        assert self._base_url is not None
        with httpx.Client(base_url=self._base_url, timeout=30.0) as client:
            resp = client.post(
                "/api/auth/login",
                json={"username": self._username, "password": self._password},
            )
            resp.raise_for_status()
            self._token = resp.json()["token"]

    def _headers(self) -> dict[str, str]:
        self._ensure_http_token()
        h = {"X-Actor": self._actor}
        if self._api_key:
            h["X-API-Key"] = self._api_key
        elif self._token:
            h["Authorization"] = f"Bearer {self._token}"
        return h

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        assert self._base_url is not None
        with httpx.Client(base_url=self._base_url, headers=self._headers(), timeout=30.0) as client:
            resp = client.request(method, path, **kwargs)
            resp.raise_for_status()
            return resp.json()

    # ------------------------------------------------------------------
    # Pool methods
    # ------------------------------------------------------------------
    def sync_folder(
        self,
        folder_path: str | Path,
        *,
        tags: list[str] | None = None,
        recursive: bool = True,
    ) -> dict[str, Any]:
        if self._service:
            return self._service.sync_folder(
                str(folder_path), tags=tags, recursive=recursive, actor=self._actor
            ).model_dump()
        return self._request(
            "POST",
            "/api/pool/sync",
            json={"folder_path": str(folder_path), "tags": tags or [], "recursive": recursive},
        )

    def list_pool(
        self,
        *,
        status: str | None = None,
        tag: str | None = None,
        suitable_for: str | None = None,
        unused_for: str | None = None,
        search: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if self._service:
            return [
                i.model_dump()
                for i in self._service.list_pool(
                    status=status,
                    tag=tag,
                    suitable_for=suitable_for,
                    unused_for=unused_for,
                    search=search,
                    limit=limit,
                )
            ]
        params = {
            k: v
            for k, v in {
                "status": status,
                "tag": tag,
                "suitable_for": suitable_for,
                "unused_for": unused_for,
                "search": search,
                "limit": limit,
            }.items()
            if v is not None
        }
        return self._request("GET", "/api/pool", params=params)

    def pick_images(
        self,
        *,
        pub_type: str = "post",
        count: int = 1,
        tag: str | None = None,
        unused_only: bool = True,
        randomize: bool = True,
    ) -> list[dict[str, Any]]:
        if self._service:
            return [
                i.model_dump()
                for i in self._service.pick_images(
                    pub_type=pub_type,
                    count=count,
                    tag=tag,
                    unused_only=unused_only,
                    randomize=randomize,
                )
            ]
        params: dict[str, Any] = {
            "pub_type": pub_type,
            "count": count,
            "unused_only": unused_only,
            "randomize": randomize,
        }
        if tag:
            params["tag"] = tag
        return self._request("GET", "/api/pool/pick", params=params)

    def update_image(
        self,
        image_id: str,
        *,
        tags: list[str] | None = None,
        notes: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        if self._service:
            return self._service.update_image(
                image_id,
                PoolImageUpdate(tags=tags, notes=notes, status=status),  # type: ignore[arg-type]
                actor=self._actor,
            ).model_dump()
        body = {k: v for k, v in {"tags": tags, "notes": notes, "status": status}.items() if v is not None}
        return self._request("PATCH", f"/api/pool/{image_id}", json=body)

    # ------------------------------------------------------------------
    # Publishing methods
    # ------------------------------------------------------------------
    def create_post(
        self,
        image_ids: str | list[str],
        *,
        caption: str = "",
        hashtags: list[str] | None = None,
        fit_mode: str = "auto",
        scheduled_at: str | None = None,
        publish_now: bool = False,
        adapter: str | None = None,
    ) -> dict[str, Any]:
        ids = [image_ids] if isinstance(image_ids, str) else list(image_ids)
        pub_type = "carousel" if len(ids) > 1 else "post"
        payload = PublicationCreate(
            pub_type=pub_type,  # type: ignore[arg-type]
            image_ids=ids,
            caption=caption,
            hashtags=hashtags or [],
            fit_mode=fit_mode,  # type: ignore[arg-type]
            adapter=adapter,  # type: ignore[arg-type]
            scheduled_at=scheduled_at,
            publish_now=publish_now,
            created_by=self._actor,
        )
        if self._service:
            return self._service.create_publication(payload).model_dump()
        return self._request("POST", "/api/publications", json=payload.model_dump())

    def create_story(
        self,
        image_id: str,
        *,
        fit_mode: str = "auto",
        scheduled_at: str | None = None,
        publish_now: bool = False,
        adapter: str | None = None,
    ) -> dict[str, Any]:
        payload = PublicationCreate(
            pub_type="story",
            image_ids=[image_id],
            fit_mode=fit_mode,  # type: ignore[arg-type]
            adapter=adapter,  # type: ignore[arg-type]
            scheduled_at=scheduled_at,
            publish_now=publish_now,
            created_by=self._actor,
        )
        if self._service:
            return self._service.create_publication(payload).model_dump()
        return self._request("POST", "/api/publications", json=payload.model_dump())

    def update_publication(
        self,
        pub_id: str,
        *,
        pub_type: str | None = None,
        image_ids: list[str] | None = None,
        caption: str | None = None,
        hashtags: list[str] | None = None,
        fit_mode: str | None = None,
        scheduled_at: str | None = None,
        clear_schedule: bool = False,
        adapter: str | None = None,
        publish_now: bool = False,
    ) -> dict[str, Any]:
        payload = PublicationUpdate(
            pub_type=pub_type,  # type: ignore[arg-type]
            image_ids=image_ids,
            caption=caption,
            hashtags=hashtags,
            fit_mode=fit_mode,  # type: ignore[arg-type]
            adapter=adapter,  # type: ignore[arg-type]
            scheduled_at=scheduled_at,
            clear_schedule=clear_schedule,
            publish_now=publish_now,
        )
        if self._service:
            return self._service.update_publication(pub_id, payload, actor=self._actor).model_dump()
        return self._request("PATCH", f"/api/publications/{pub_id}", json=payload.model_dump(exclude_none=True))

    def publish(self, pub_id: str, *, adapter: str | None = None) -> dict[str, Any]:
        if self._service:
            return self._service.publish_publication(
                pub_id, adapter_override=adapter, actor=self._actor
            ).model_dump()
        params = {"adapter": adapter} if adapter else {}
        return self._request("POST", f"/api/publications/{pub_id}/publish", params=params)

    def list_queue(
        self,
        *,
        status: str | None = None,
        pub_type: str | None = None,
    ) -> list[dict[str, Any]]:
        if self._service:
            return [p.model_dump() for p in self._service.list_publications(status=status, pub_type=pub_type)]
        params = {k: v for k, v in {"status": status, "pub_type": pub_type}.items() if v is not None}
        return self._request("GET", "/api/publications", params=params)

    def stats(self) -> dict[str, Any]:
        if self._service:
            return self._service.get_stats().model_dump()
        return self._request("GET", "/api/stats")

    # ------------------------------------------------------------------
    # Insights & analytics methods
    # ------------------------------------------------------------------
    def sync_insights(
        self,
        *,
        days: int = 7,
        include_account_media: bool = True,
        stories_only: bool = False,
    ) -> dict[str, Any]:
        if self._service:
            return self._service.sync_insights(
                days=days,
                include_account_media=include_account_media,
                stories_only=stories_only,
                actor=self._actor,
            ).model_dump()
        return self._request(
            "POST",
            "/api/insights/sync",
            params={
                "days": days,
                "include_account_media": str(include_account_media).lower(),
                "stories_only": str(stories_only).lower(),
            },
        )

    def get_insights(
        self,
        *,
        days: int = 7,
        media_product_type: str | None = None,
        sort_by: str = "published_at",
        limit: int = 100,
    ) -> dict[str, Any]:
        if self._service:
            return self._service.get_insights_overview(
                days=days,
                media_product_type=media_product_type,
                sort_by=sort_by,
                limit=limit,
            ).model_dump()
        params: dict[str, Any] = {"days": days, "sort_by": sort_by, "limit": limit}
        if media_product_type:
            params["media_product_type"] = media_product_type
        return self._request("GET", "/api/insights", params=params)

    def get_media_insight(self, identifier: str) -> dict[str, Any]:
        if self._service:
            return self._service.get_media_insight(identifier).model_dump()
        return self._request("GET", f"/api/insights/media/{identifier}")

    def get_media_insight_history(self, identifier: str, *, limit: int = 50) -> list[dict[str, Any]]:
        if self._service:
            return [s.model_dump() for s in self._service.get_media_insight_history(identifier, limit=limit)]
        return self._request("GET", f"/api/insights/media/{identifier}/history", params={"limit": limit})

    def sync_publication_insights(self, identifier: str) -> dict[str, Any]:
        if self._service:
            return self._service.sync_publication_insights(identifier, actor=self._actor).model_dump()
        return self._request("POST", f"/api/insights/media/{identifier}/sync")
