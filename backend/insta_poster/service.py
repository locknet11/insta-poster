from __future__ import annotations

import hmac
import json
import random
import re
import secrets
import shutil
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from insta_poster.adapters import (
    DryRunAdapter,
    GraphAPIAdapter,
    InstagramAdapter,
)
from insta_poster.auth import (
    generate_api_key,
    generate_session_token,
    hash_api_key,
    hash_password,
    hash_session_token,
    normalize_api_key_name,
    normalize_username,
    verify_dummy_password,
    verify_password,
)
from insta_poster.config import (
    AppPaths,
    SUPPORTED_IMAGE_EXTENSIONS,
    get_init_user_credentials,
    get_secret_key_env,
    get_session_ttl_days,
)
from insta_poster.db import Database, utcnow_iso
from insta_poster.image_prep import (
    compute_sha256,
    generate_thumbnail,
    inspect_image,
    prepare_for_instagram,
)
from insta_poster.models import (
    AccountDailyPoint,
    AccountInsights,
    ActivityLogEntry,
    AdapterConfigUpdate,
    AdapterStatus,
    ApiKeyCreatedResponse,
    ApiKeyPublic,
    DashboardStats,
    FolderSyncResponse,
    InsightsOverview,
    InsightsSummary,
    InsightsSyncResponse,
    MediaInsight,
    MediaInsightSnapshot,
    MediaInsightSummary,
    PoolImage,
    PoolImageUpdate,
    Publication,
    PublicationCreate,
    PublicationUpdate,
    UserPublic,
)


def _normalize_tags(tags: list[str] | None) -> list[str]:
    if not tags:
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for t in tags:
        val = t.strip().lstrip("#").lower()
        if val and val not in seen:
            seen.add(val)
            cleaned.append(val)
    return cleaned


def _normalize_hashtags(hashtags: list[str] | None) -> list[str]:
    if not hashtags:
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in hashtags:
        for piece in re.split(r"[\s,]+", raw.strip()):
            token = piece.strip().lstrip("#")
            if token and token.lower() not in seen:
                seen.add(token.lower())
                cleaned.append(f"#{token}")
    return cleaned


def _compose_full_caption(caption: str, hashtags: list[str]) -> str:
    cap = caption.strip()
    tags_str = " ".join(_normalize_hashtags(hashtags))
    if cap and tags_str:
        return f"{cap}\n\n{tags_str}"
    return cap or tags_str


def _parse_iso(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _safe_filename(name: str) -> str:
    stem = re.sub(r"[^a-zA-Z0-9._-]+", "_", Path(name).stem).strip("._") or "image"
    suffix = Path(name).suffix.lower() or ".jpg"
    return f"{stem[:60]}{suffix}"


class InstaPosterService:
    """Core domain service shared by the REST API, CLI, Scheduler, and Python SDK."""

    def __init__(self, paths: AppPaths | None = None) -> None:
        self.paths = paths or AppPaths.from_data_dir()
        self.db = Database(self.paths.db_path)
        self._init_defaults()

    def _init_defaults(self) -> None:
        if self.db.get_setting("default_adapter") is None:
            self.db.set_setting("default_adapter", "dry_run")
        if self.db.get_setting("scheduler_enabled") is None:
            self.db.set_setting("scheduler_enabled", True)
        if self.db.get_setting("insights_auto_sync_minutes") is None:
            self.db.set_setting("insights_auto_sync_minutes", 30)
        if not self.db.get_setting("media_signing_secret"):
            self.db.set_setting("media_signing_secret", secrets.token_hex(32))
        self.bootstrap_users_from_env()

    def get_media_signing_secret(self) -> str:
        env_secret = get_secret_key_env()
        if env_secret:
            return env_secret
        secret = self.db.get_setting("media_signing_secret")
        if not secret:
            secret = secrets.token_hex(32)
            self.db.set_setting("media_signing_secret", secret)
        return str(secret)

    # ------------------------------------------------------------------
    # Users & authentication
    # ------------------------------------------------------------------
    def _row_to_user(self, row: Any) -> UserPublic:
        return UserPublic(
            id=row["id"],
            username=row["username"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_login_at=row["last_login_at"],
        )

    def count_users(self) -> int:
        with self.db.connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
        return int(row["c"]) if row else 0

    def needs_setup(self) -> bool:
        return self.count_users() == 0

    def bootstrap_users_from_env(self) -> UserPublic | None:
        creds = get_init_user_credentials()
        if not creds:
            return None
        username, password = creds
        if not self.needs_setup():
            return None
        try:
            return self.setup_first_user(username, password, actor="env-bootstrap")
        except PermissionError:
            return None

    def list_users(self) -> list[UserPublic]:
        with self.db.connection() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY created_at ASC").fetchall()
        return [self._row_to_user(r) for r in rows]

    def get_user(self, user_id: str) -> UserPublic:
        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise KeyError(f"Usuario no encontrado: {user_id}")
        return self._row_to_user(row)

    def get_user_by_username(self, username: str) -> UserPublic | None:
        cleaned = username.strip().lower()
        if not cleaned:
            return None
        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (cleaned,)).fetchone()
        return self._row_to_user(row) if row else None

    def setup_first_user(self, username: str, password: str, *, actor: str = "setup") -> UserPublic:
        clean_user = normalize_username(username)
        pw_hash = hash_password(password)
        user_id = uuid.uuid4().hex[:12]
        now = utcnow_iso()

        with self.db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            count_row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
            if count_row and int(count_row["c"]) > 0:
                raise PermissionError("La configuración inicial ya fue completada.")
            conn.execute(
                """
                INSERT INTO users (id, username, password_hash, created_at, updated_at, last_login_at)
                VALUES (?, ?, ?, ?, ?, NULL)
                """,
                (user_id, clean_user, pw_hash, now, now),
            )
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

        self.db.log_activity(
            "auth.setup",
            actor=actor,
            entity_type="user",
            entity_id=user_id,
            details={"username": clean_user},
        )
        return self._row_to_user(row)

    def create_user(self, username: str, password: str, *, actor: str = "system") -> UserPublic:
        clean_user = normalize_username(username)
        pw_hash = hash_password(password)
        user_id = uuid.uuid4().hex[:12]
        now = utcnow_iso()

        try:
            with self.db.connection() as conn:
                conn.execute(
                    """
                    INSERT INTO users (id, username, password_hash, created_at, updated_at, last_login_at)
                    VALUES (?, ?, ?, ?, ?, NULL)
                    """,
                    (user_id, clean_user, pw_hash, now, now),
                )
                row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"El usuario '{clean_user}' ya existe.") from exc

        self.db.log_activity(
            "users.create",
            actor=actor,
            entity_type="user",
            entity_id=user_id,
            details={"username": clean_user},
        )
        return self._row_to_user(row)

    def authenticate_user(self, username: str, password: str) -> UserPublic | None:
        cleaned = username.strip().lower()
        if not cleaned or not password:
            verify_dummy_password(password or "invalid")
            return None

        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (cleaned,)).fetchone()

        if not row:
            verify_dummy_password(password)
            return None

        if not verify_password(password, row["password_hash"]):
            return None

        now = utcnow_iso()
        with self.db.connection() as conn:
            conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now, row["id"]))
            updated = conn.execute("SELECT * FROM users WHERE id = ?", (row["id"],)).fetchone()
        return self._row_to_user(updated)

    def create_session(
        self,
        user_id: str,
        *,
        ttl_days: int | None = None,
        user_agent: str = "",
    ) -> tuple[str, str]:
        days = ttl_days if ttl_days is not None else get_session_ttl_days()
        now_dt = datetime.now(timezone.utc).replace(microsecond=0)
        now = now_dt.isoformat()
        expires_at = (now_dt + timedelta(days=max(1, days))).isoformat()
        raw_token = generate_session_token()
        token_hash = hash_session_token(raw_token)

        with self.db.connection() as conn:
            conn.execute("DELETE FROM user_sessions WHERE expires_at <= ?", (now,))
            conn.execute(
                """
                INSERT INTO user_sessions (token_hash, user_id, created_at, expires_at, last_seen_at, user_agent)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (token_hash, user_id, now, expires_at, now, user_agent[:250]),
            )
        return raw_token, expires_at

    def verify_session_token(self, raw_token: str) -> UserPublic | None:
        if not raw_token:
            return None
        token_hash = hash_session_token(raw_token)
        now = utcnow_iso()

        with self.db.connection() as conn:
            row = conn.execute(
                """
                SELECT u.*, s.expires_at AS session_expires_at, s.last_seen_at AS session_last_seen_at
                FROM user_sessions s
                JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = ?
                """,
                (token_hash,),
            ).fetchone()

            if not row:
                return None

            if row["session_expires_at"] <= now:
                conn.execute("DELETE FROM user_sessions WHERE token_hash = ?", (token_hash,))
                return None

            if row["session_last_seen_at"] != now:
                conn.execute(
                    "UPDATE user_sessions SET last_seen_at = ? WHERE token_hash = ?",
                    (now, token_hash),
                )

        return self._row_to_user(row)

    def revoke_session_token(self, raw_token: str) -> None:
        if not raw_token:
            return
        token_hash = hash_session_token(raw_token)
        with self.db.connection() as conn:
            conn.execute("DELETE FROM user_sessions WHERE token_hash = ?", (token_hash,))

    def change_own_password(
        self,
        user_id: str,
        current_password: str,
        new_password: str,
        *,
        keep_token: str | None = None,
    ) -> UserPublic:
        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            raise KeyError(f"Usuario no encontrado: {user_id}")
        if not verify_password(current_password, row["password_hash"]):
            raise PermissionError("La contraseña actual es incorrecta.")
        return self.set_user_password(
            user_id,
            new_password,
            actor=row["username"],
            keep_token=keep_token,
        )

    def set_user_password(
        self,
        user_id: str,
        new_password: str,
        *,
        actor: str = "user",
        keep_token: str | None = None,
    ) -> UserPublic:
        user = self.get_user(user_id)
        pw_hash = hash_password(new_password)
        now = utcnow_iso()
        keep_hash = hash_session_token(keep_token) if keep_token else None

        with self.db.connection() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (pw_hash, now, user_id),
            )
            if keep_hash:
                conn.execute(
                    "DELETE FROM user_sessions WHERE user_id = ? AND token_hash != ?",
                    (user_id, keep_hash),
                )
            else:
                conn.execute("DELETE FROM user_sessions WHERE user_id = ?", (user_id,))
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()

        self.db.log_activity(
            "users.password_change",
            actor=actor,
            entity_type="user",
            entity_id=user_id,
            details={"username": user.username},
        )
        return self._row_to_user(row)

    def delete_user(
        self,
        user_id: str,
        *,
        actor: str = "user",
        current_user_id: str | None = None,
        allow_delete_last: bool = False,
    ) -> None:
        user = self.get_user(user_id)
        if current_user_id and user_id == current_user_id:
            raise ValueError("No podés eliminar tu propio usuario activo.")

        with self.db.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            count_row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
            if not allow_delete_last and count_row and int(count_row["c"]) <= 1:
                raise ValueError("No podés eliminar el único usuario del sistema.")
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))

        self.db.log_activity(
            "users.delete",
            actor=actor,
            entity_type="user",
            entity_id=user_id,
            details={"username": user.username},
        )

    # ------------------------------------------------------------------
    # API Keys (for AI agents & automation)
    # ------------------------------------------------------------------
    def _row_to_api_key(self, row: Any) -> ApiKeyPublic:
        return ApiKeyPublic(
            id=row["id"],
            name=row["name"],
            key_prefix=row["key_prefix"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            last_used_at=row["last_used_at"],
        )

    def list_api_keys(self) -> list[ApiKeyPublic]:
        with self.db.connection() as conn:
            rows = conn.execute("SELECT * FROM api_keys ORDER BY created_at DESC").fetchall()
        return [self._row_to_api_key(r) for r in rows]

    def create_api_key(self, name: str, *, created_by: str = "user") -> ApiKeyCreatedResponse:
        clean_name = normalize_api_key_name(name)
        raw_key, key_prefix, key_hash = generate_api_key()
        key_id = uuid.uuid4().hex[:12]
        now = utcnow_iso()

        with self.db.connection() as conn:
            conn.execute(
                """
                INSERT INTO api_keys (id, name, key_prefix, key_hash, created_by, created_at, last_used_at)
                VALUES (?, ?, ?, ?, ?, ?, NULL)
                """,
                (key_id, clean_name, key_prefix, key_hash, created_by, now),
            )
            row = conn.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,)).fetchone()

        self.db.log_activity(
            "api_keys.create",
            actor=created_by,
            entity_type="api_key",
            entity_id=key_id,
            details={"name": clean_name, "key_prefix": key_prefix},
        )
        pub = self._row_to_api_key(row)
        return ApiKeyCreatedResponse(**pub.model_dump(), api_key=raw_key)

    def verify_api_key(self, raw_key: str) -> ApiKeyPublic | None:
        cleaned = (raw_key or "").strip()
        if not cleaned:
            return None
        key_hash = hash_api_key(cleaned)
        now = utcnow_iso()

        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM api_keys WHERE key_hash = ?", (key_hash,)).fetchone()
            if not row or not hmac.compare_digest(row["key_hash"], key_hash):
                return None
            if row["last_used_at"] != now:
                conn.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (now, row["id"]))
                row = conn.execute("SELECT * FROM api_keys WHERE id = ?", (row["id"],)).fetchone()
        return self._row_to_api_key(row)

    def revoke_api_key(self, key_id: str, *, actor: str = "user") -> None:
        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM api_keys WHERE id = ?", (key_id,)).fetchone()
            if not row:
                raise KeyError(f"API Key no encontrada: {key_id}")
            conn.execute("DELETE FROM api_keys WHERE id = ?", (key_id,))

        self.db.log_activity(
            "api_keys.revoke",
            actor=actor,
            entity_type="api_key",
            entity_id=key_id,
            details={"name": row["name"], "key_prefix": row["key_prefix"]},
        )

    # ------------------------------------------------------------------
    # Row mappers
    # ------------------------------------------------------------------
    def _resolve_pool_filepath(self, stored_path: str, filename: str) -> str:
        if stored_path and Path(stored_path).exists():
            return stored_path
        candidate = self.paths.pool_dir / filename
        if candidate.exists():
            return str(candidate)
        return stored_path or str(candidate)

    def _resolve_prepared_file(self, stored_path: str) -> str:
        if stored_path and Path(stored_path).exists():
            return stored_path
        candidate = self.paths.prepared_dir / Path(stored_path).name
        if candidate.exists():
            return str(candidate)
        return stored_path

    def _row_to_pool_image(self, row: Any) -> PoolImage:
        thumb_name = Path(row["thumbnail_path"]).name if row["thumbnail_path"] else row["filename"]
        return PoolImage(
            id=row["id"],
            filename=row["filename"],
            original_filename=row["original_filename"],
            filepath=self._resolve_pool_filepath(row["filepath"], row["filename"]),
            thumbnail_url=f"/media/thumbnails/{thumb_name}",
            image_url=f"/media/pool/{row['filename']}",
            source_path=row["source_path"],
            sha256=row["sha256"],
            width=row["width"],
            height=row["height"],
            aspect_ratio=row["aspect_ratio"],
            file_size=row["file_size"],
            mime_type=row["mime_type"],
            status=row["status"],
            tags=json.loads(row["tags"] or "[]"),
            notes=row["notes"] or "",
            suitable_for=json.loads(row["suitable_for"] or '["post","story"]'),
            times_used_post=row["times_used_post"],
            times_used_story=row["times_used_story"],
            last_used_at=row["last_used_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _compute_engagement_rate(
        self,
        *,
        reach: int | None,
        total_interactions: int | None,
        likes: int | None = None,
        comments: int | None = None,
        saved: int | None = None,
        shares: int | None = None,
        replies: int | None = None,
    ) -> float | None:
        if not reach or reach <= 0:
            return None
        if total_interactions is not None:
            interactions = total_interactions
        else:
            parts = [v for v in (likes, comments, saved, shares, replies) if v is not None]
            if not parts:
                return None
            interactions = sum(parts)
        return round((interactions / reach) * 100.0, 2)

    def _row_to_media_insight_summary(self, row: Any) -> MediaInsightSummary:
        nav_bd = json.loads(row["navigation_breakdown"] or "{}")
        return MediaInsightSummary(
            external_id=row["external_id"],
            views=row["views"],
            reach=row["reach"],
            likes=row["likes"],
            comments=row["comments"],
            saved=row["saved"],
            shares=row["shares"],
            replies=row["replies"],
            total_interactions=row["total_interactions"],
            follows=row["follows"],
            profile_visits=row["profile_visits"],
            navigation=row["navigation"],
            navigation_breakdown=nav_bd,
            engagement_rate=self._compute_engagement_rate(
                reach=row["reach"],
                total_interactions=row["total_interactions"],
                likes=row["likes"],
                comments=row["comments"],
                saved=row["saved"],
                shares=row["shares"],
                replies=row["replies"],
            ),
            is_active=bool(row["is_active"]),
            error_message=row["error_message"],
            synced_at=row["synced_at"],
        )

    def _row_to_publication(self, row: Any, insight_row: Any | None = None) -> Publication:
        raw_prepared = json.loads(row["prepared_files"] or "[]")
        prepared_files = [self._resolve_prepared_file(p) for p in raw_prepared]
        hashtags = json.loads(row["hashtags"] or "[]")
        caption = row["caption"] or ""
        return Publication(
            id=row["id"],
            pub_type=row["pub_type"],
            status=row["status"],
            adapter=row["adapter"],
            image_ids=json.loads(row["image_ids"] or "[]"),
            prepared_files=prepared_files,
            prepared_urls=[f"/media/prepared/{Path(p).name}" for p in prepared_files],
            caption=caption,
            hashtags=hashtags,
            full_caption=_compose_full_caption(caption, hashtags),
            fit_mode=row["fit_mode"] or "auto",
            scheduled_at=row["scheduled_at"],
            published_at=row["published_at"],
            external_id=row["external_id"],
            external_url=row["external_url"],
            error_message=row["error_message"],
            metadata=json.loads(row["metadata"] or "{}"),
            created_by=row["created_by"] or "user",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            insights=self._row_to_media_insight_summary(insight_row) if insight_row is not None else None,
        )

    # ------------------------------------------------------------------
    # Pool management
    # ------------------------------------------------------------------
    def add_image_file(
        self,
        source: Path,
        *,
        original_filename: str | None = None,
        tags: list[str] | None = None,
        notes: str = "",
        actor: str = "user",
    ) -> tuple[PoolImage, bool]:
        """Add an image to the local pool. Returns (image, created_bool)."""
        src = Path(source).expanduser().resolve()
        if not src.exists() or not src.is_file():
            raise FileNotFoundError(f"Imagen no encontrada: {src}")
        if src.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            raise ValueError(f"Formato no soportado: {src.suffix}")

        sha = compute_sha256(src)
        with self.db.connection() as conn:
            existing = conn.execute("SELECT * FROM pool_images WHERE sha256 = ?", (sha,)).fetchone()
            if existing:
                # Self-heal if the DB record exists but the file was missing on disk
                dst_existing = self.paths.pool_dir / existing["filename"]
                if not dst_existing.exists():
                    shutil.copy2(src, dst_existing)
                thumb_existing = self.paths.thumbnails_dir / f"{existing['id']}.jpg"
                if not thumb_existing.exists():
                    generate_thumbnail(dst_existing, thumb_existing)
                return self._row_to_pool_image(existing), False

        image_id = uuid.uuid4().hex[:12]
        orig_name = original_filename or src.name
        stored_name = f"{image_id}_{_safe_filename(orig_name)}"
        dst = self.paths.pool_dir / stored_name
        shutil.copy2(src, dst)

        info = inspect_image(dst)
        thumb_dst = self.paths.thumbnails_dir / f"{image_id}.jpg"
        generate_thumbnail(dst, thumb_dst)

        now = utcnow_iso()
        clean_tags = _normalize_tags(tags)

        with self.db.connection() as conn:
            conn.execute(
                """
                INSERT INTO pool_images (
                    id, filename, original_filename, filepath, thumbnail_path, source_path,
                    sha256, width, height, aspect_ratio, file_size, mime_type,
                    status, tags, notes, suitable_for,
                    times_used_post, times_used_story, last_used_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'available', ?, ?, ?, 0, 0, NULL, ?, ?)
                """,
                (
                    image_id,
                    stored_name,
                    orig_name,
                    str(dst),
                    str(thumb_dst),
                    str(src),
                    sha,
                    info["width"],
                    info["height"],
                    info["aspect_ratio"],
                    info["file_size"],
                    info["mime_type"],
                    json.dumps(clean_tags, ensure_ascii=False),
                    notes.strip(),
                    json.dumps(info["suitable_for"]),
                    now,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM pool_images WHERE id = ?", (image_id,)).fetchone()

        self.db.log_activity(
            "pool.add",
            actor=actor,
            entity_type="pool_image",
            entity_id=image_id,
            details={"filename": orig_name, "tags": clean_tags},
        )
        return self._row_to_pool_image(row), True

    def add_image_bytes(
        self,
        data: bytes,
        original_filename: str,
        *,
        tags: list[str] | None = None,
        notes: str = "",
        actor: str = "user",
    ) -> tuple[PoolImage, bool]:
        tmp_name = f"tmp_{uuid.uuid4().hex}_{_safe_filename(original_filename)}"
        tmp_path = self.paths.data_dir / tmp_name
        try:
            tmp_path.write_bytes(data)
            return self.add_image_file(
                tmp_path,
                original_filename=original_filename,
                tags=tags,
                notes=notes,
                actor=actor,
            )
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def sync_folder(
        self,
        folder_path: str,
        *,
        tags: list[str] | None = None,
        recursive: bool = True,
        actor: str = "user",
    ) -> FolderSyncResponse:
        folder = Path(folder_path).expanduser().resolve()
        if not folder.exists() or not folder.is_dir():
            raise FileNotFoundError(f"Carpeta no encontrada: {folder}")

        pattern = "**/*" if recursive else "*"
        added: list[PoolImage] = []
        skipped = 0
        errors: list[str] = []

        for path in sorted(folder.glob(pattern)):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
                continue
            try:
                img, created = self.add_image_file(path, tags=tags, actor=actor)
                if created:
                    added.append(img)
                else:
                    skipped += 1
            except Exception as exc:
                errors.append(f"{path.name}: {exc}")

        self.db.log_activity(
            "pool.sync",
            actor=actor,
            entity_type="folder",
            details={
                "folder": str(folder),
                "added": len(added),
                "skipped_duplicates": skipped,
                "errors": len(errors),
            },
        )
        return FolderSyncResponse(
            folder_path=str(folder),
            added=added,
            skipped_duplicates=skipped,
            errors=errors,
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
    ) -> list[PoolImage]:
        clauses: list[str] = []
        params: list[Any] = []

        if status:
            clauses.append("status = ?")
            params.append(status)
        if unused_for == "post":
            clauses.append("times_used_post = 0")
        elif unused_for == "story":
            clauses.append("times_used_story = 0")
        elif unused_for == "any":
            clauses.append("(times_used_post = 0 AND times_used_story = 0)")
        if search:
            like = f"%{search.strip().lower()}%"
            clauses.append("(LOWER(original_filename) LIKE ? OR LOWER(notes) LIKE ? OR LOWER(tags) LIKE ?)")
            params.extend([like, like, like])

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM pool_images {where} ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(limit, 1000)))

        with self.db.connection() as conn:
            rows = conn.execute(sql, params).fetchall()

        images = [self._row_to_pool_image(r) for r in rows]
        if tag:
            wanted = tag.strip().lstrip("#").lower()
            images = [img for img in images if wanted in img.tags]
        if suitable_for:
            wanted_fmt = suitable_for.strip().lower()
            images = [img for img in images if wanted_fmt in img.suitable_for]
        return images

    def get_image(self, image_id: str) -> PoolImage:
        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM pool_images WHERE id = ?", (image_id,)).fetchone()
        if not row:
            raise KeyError(f"Imagen no encontrada: {image_id}")
        return self._row_to_pool_image(row)

    def update_image(self, image_id: str, payload: PoolImageUpdate, actor: str = "user") -> PoolImage:
        current = self.get_image(image_id)
        new_tags = _normalize_tags(payload.tags) if payload.tags is not None else current.tags
        new_notes = payload.notes.strip() if payload.notes is not None else current.notes
        new_status = payload.status if payload.status is not None else current.status
        now = utcnow_iso()

        with self.db.connection() as conn:
            conn.execute(
                """
                UPDATE pool_images
                SET tags = ?, notes = ?, status = ?, updated_at = ?
                WHERE id = ?
                """,
                (json.dumps(new_tags, ensure_ascii=False), new_notes, new_status, now, image_id),
            )
            row = conn.execute("SELECT * FROM pool_images WHERE id = ?", (image_id,)).fetchone()

        self.db.log_activity(
            "pool.update",
            actor=actor,
            entity_type="pool_image",
            entity_id=image_id,
            details={"tags": new_tags, "status": new_status},
        )
        return self._row_to_pool_image(row)

    def delete_image(self, image_id: str, actor: str = "user") -> None:
        img = self.get_image(image_id)
        with self.db.connection() as conn:
            conn.execute("DELETE FROM pool_images WHERE id = ?", (image_id,))
        for p in (Path(img.filepath), self.paths.thumbnails_dir / f"{image_id}.jpg"):
            if p.exists():
                p.unlink()
        self.db.log_activity("pool.delete", actor=actor, entity_type="pool_image", entity_id=image_id)

    def pick_images(
        self,
        *,
        pub_type: str = "post",
        count: int = 1,
        tag: str | None = None,
        unused_only: bool = True,
        prefer_native_aspect: bool = True,
        randomize: bool = True,
    ) -> list[PoolImage]:
        """Smart helper for agents to pick candidate images from the pool."""
        candidates = self.list_pool(
            status="available",
            tag=tag,
            unused_for=pub_type if unused_only else None,
            limit=500,
        )
        if not candidates and unused_only:
            # Fallback to any non-archived image if all have been used for this type
            candidates = [
                img
                for img in self.list_pool(tag=tag, limit=500)
                if img.status in ("available", "published")
            ]

        if prefer_native_aspect:
            native = [img for img in candidates if pub_type in img.suitable_for]
            if len(native) >= count:
                candidates = native

        if randomize:
            random.shuffle(candidates)
        else:
            key_fn = (
                (lambda x: (x.times_used_story, x.created_at))
                if pub_type == "story"
                else (lambda x: (x.times_used_post, x.created_at))
            )
            candidates.sort(key=key_fn)

        return candidates[: max(1, count)]

    # ------------------------------------------------------------------
    # Adapters & settings
    # ------------------------------------------------------------------
    def get_default_adapter_name(self) -> str:
        val = str(self.db.get_setting("default_adapter", "dry_run"))
        return val if val in ("dry_run", "graph_api") else "dry_run"

    def is_scheduler_enabled(self) -> bool:
        return bool(self.db.get_setting("scheduler_enabled", True))

    def get_adapter(self, name: str | None = None) -> InstagramAdapter:
        adapter_name = name or self.get_default_adapter_name()
        if adapter_name == "dry_run":
            return DryRunAdapter(self.paths.dry_run_dir)
        if adapter_name == "graph_api":
            cfg = dict(self.db.get_setting("adapter_graph_api", {}))
            cfg["media_signing_secret"] = self.get_media_signing_secret()
            return GraphAPIAdapter(cfg)
        raise ValueError(f"Adaptador desconocido: {adapter_name}")

    def list_adapters(self, verify_remote: bool = False) -> list[AdapterStatus]:
        default_name = self.get_default_adapter_name()
        statuses: list[AdapterStatus] = []
        for name in ("dry_run", "graph_api"):
            adapter = self.get_adapter(name)
            check = adapter.check_health(verify_remote=verify_remote)
            statuses.append(
                AdapterStatus(
                    name=adapter.name,
                    label=adapter.label,
                    configured=check.configured,
                    available=check.available,
                    is_default=(adapter.name == default_name),
                    supports=list(adapter.supports),
                    message=check.message,
                    details=check.details,
                )
            )
        return statuses

    def check_adapter(self, name: str, verify_remote: bool = True) -> AdapterStatus:
        adapter = self.get_adapter(name)
        check = adapter.check_health(verify_remote=verify_remote)
        return AdapterStatus(
            name=adapter.name,
            label=adapter.label,
            configured=check.configured,
            available=check.available,
            is_default=(adapter.name == self.get_default_adapter_name()),
            supports=list(adapter.supports),
            message=check.message,
            details=check.details,
        )

    def get_sanitized_settings(self) -> dict[str, Any]:
        graph_cfg = dict(self.db.get_setting("adapter_graph_api", {}))

        if graph_cfg.get("access_token"):
            tok = str(graph_cfg["access_token"])
            graph_cfg["access_token_masked"] = f"{tok[:6]}...{tok[-4:]}" if len(tok) > 12 else "***"
            graph_cfg["has_access_token"] = True
            graph_cfg.pop("access_token", None)
        else:
            graph_cfg["has_access_token"] = False

        if graph_cfg.get("imgbb_api_key"):
            key = str(graph_cfg["imgbb_api_key"])
            graph_cfg["imgbb_api_key_masked"] = f"{key[:6]}...{key[-4:]}" if len(key) > 12 else "***"
            graph_cfg["has_imgbb_api_key"] = True
            graph_cfg.pop("imgbb_api_key", None)
        else:
            graph_cfg["has_imgbb_api_key"] = False

        return {
            "default_adapter": self.get_default_adapter_name(),
            "scheduler_enabled": self.is_scheduler_enabled(),
            "graph_api": graph_cfg,
            "data_dir": str(self.paths.data_dir),
        }

    def update_settings(self, payload: AdapterConfigUpdate, actor: str = "user") -> dict[str, Any]:
        if payload.default_adapter is not None:
            self.db.set_setting("default_adapter", payload.default_adapter)
        if payload.scheduler_enabled is not None:
            self.db.set_setting("scheduler_enabled", payload.scheduler_enabled)
        if payload.graph_api is not None:
            existing = dict(self.db.get_setting("adapter_graph_api", {}))
            for k, v in payload.graph_api.items():
                if v is None:
                    continue
                if k in ("access_token", "imgbb_api_key") and str(v).strip() == "":
                    continue
                existing[k] = v
            self.db.set_setting("adapter_graph_api", existing)

        self.db.log_activity("settings.update", actor=actor, details={"default_adapter": self.get_default_adapter_name()})
        return self.get_sanitized_settings()

    # ------------------------------------------------------------------
    # Publications (Posts, Carousels, Stories)
    # ------------------------------------------------------------------
    def _prepare_publication_assets(
        self,
        pub_id: str,
        pub_type: str,
        image_ids: list[str],
        fit_mode: str,
    ) -> list[str]:
        prepared_paths: list[str] = []
        for idx, image_id in enumerate(image_ids):
            img = self.get_image(image_id)
            out_name = f"{pub_id}_{idx + 1:02d}_{pub_type}.jpg"
            out_path = self.paths.prepared_dir / out_name
            prepare_for_instagram(Path(img.filepath), out_path, pub_type=pub_type, fit_mode=fit_mode)
            prepared_paths.append(str(out_path))
        return prepared_paths

    def create_publication(self, payload: PublicationCreate) -> Publication:
        if payload.pub_type == "story" and len(payload.image_ids) != 1:
            raise ValueError("Una historia (story) requiere exactamente 1 imagen.")
        if payload.pub_type == "post" and len(payload.image_ids) != 1:
            raise ValueError("Un post simple requiere exactamente 1 imagen (usá 'carousel' para múltiples).")
        if payload.pub_type == "carousel" and len(payload.image_ids) < 2:
            raise ValueError("Un carrusel requiere al menos 2 imágenes.")

        # Validate images exist
        for iid in payload.image_ids:
            self.get_image(iid)

        pub_id = uuid.uuid4().hex[:12]
        adapter_name = payload.adapter or self.get_default_adapter_name()
        hashtags = _normalize_hashtags(payload.hashtags)
        scheduled_iso: str | None = None
        if payload.scheduled_at:
            scheduled_iso = _parse_iso(payload.scheduled_at).isoformat()

        initial_status = "scheduled" if scheduled_iso else "draft"
        prepared = self._prepare_publication_assets(pub_id, payload.pub_type, payload.image_ids, payload.fit_mode)
        now = utcnow_iso()

        with self.db.connection() as conn:
            conn.execute(
                """
                INSERT INTO publications (
                    id, pub_type, status, adapter, image_ids, prepared_files,
                    caption, hashtags, fit_mode, scheduled_at, published_at,
                    external_id, external_url, error_message, metadata,
                    created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?, ?, ?)
                """,
                (
                    pub_id,
                    payload.pub_type,
                    initial_status,
                    adapter_name,
                    json.dumps(payload.image_ids),
                    json.dumps(prepared),
                    payload.caption.strip(),
                    json.dumps(hashtags, ensure_ascii=False),
                    payload.fit_mode,
                    scheduled_iso,
                    json.dumps(payload.metadata, ensure_ascii=False),
                    payload.created_by,
                    now,
                    now,
                ),
            )
            if initial_status == "scheduled":
                for iid in payload.image_ids:
                    conn.execute(
                        "UPDATE pool_images SET status = 'queued', updated_at = ? WHERE id = ? AND status = 'available'",
                        (now, iid),
                    )
            row = conn.execute("SELECT * FROM publications WHERE id = ?", (pub_id,)).fetchone()

        self.db.log_activity(
            "publication.create",
            actor=payload.created_by,
            entity_type="publication",
            entity_id=pub_id,
            details={
                "pub_type": payload.pub_type,
                "status": initial_status,
                "adapter": adapter_name,
                "image_ids": payload.image_ids,
            },
        )
        pub = self._row_to_publication(row)
        if payload.publish_now:
            return self.publish_publication(pub.id, adapter_override=adapter_name, actor=payload.created_by)
        return pub

    def get_publication(self, pub_id: str) -> Publication:
        with self.db.connection() as conn:
            row = conn.execute("SELECT * FROM publications WHERE id = ?", (pub_id,)).fetchone()
            if not row:
                raise KeyError(f"Publicación no encontrada: {pub_id}")
            insight_row = None
            if row["external_id"]:
                insight_row = conn.execute(
                    "SELECT * FROM media_insights WHERE external_id = ?",
                    (row["external_id"],),
                ).fetchone()
        return self._row_to_publication(row, insight_row)

    def list_publications(
        self,
        *,
        status: str | None = None,
        pub_type: str | None = None,
        limit: int = 100,
    ) -> list[Publication]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if pub_type:
            clauses.append("pub_type = ?")
            params.append(pub_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM publications {where} ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(limit, 500)))
        with self.db.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
            ext_ids = [r["external_id"] for r in rows if r["external_id"]]
            insights_by_ext: dict[str, Any] = {}
            if ext_ids:
                placeholders = ",".join("?" for _ in ext_ids)
                ins_rows = conn.execute(
                    f"SELECT * FROM media_insights WHERE external_id IN ({placeholders})",
                    ext_ids,
                ).fetchall()
                insights_by_ext = {ir["external_id"]: ir for ir in ins_rows}
        return [self._row_to_publication(r, insights_by_ext.get(r["external_id"])) for r in rows]

    def update_publication(self, pub_id: str, payload: PublicationUpdate, actor: str = "user") -> Publication:
        pub = self.get_publication(pub_id)
        if pub.status == "published":
            raise ValueError("No se puede editar una publicación ya publicada.")

        pub_type = payload.pub_type or pub.pub_type
        image_ids = payload.image_ids if payload.image_ids is not None else pub.image_ids

        if pub_type == "story" and len(image_ids) != 1:
            raise ValueError("Una historia (story) requiere exactamente 1 imagen.")
        if pub_type == "post" and len(image_ids) != 1:
            raise ValueError("Un post simple requiere exactamente 1 imagen (usá 'carousel' para múltiples).")
        if pub_type == "carousel" and len(image_ids) < 2:
            raise ValueError("Un carrusel requiere al menos 2 imágenes.")

        for iid in image_ids:
            self.get_image(iid)

        caption = payload.caption.strip() if payload.caption is not None else pub.caption
        hashtags = _normalize_hashtags(payload.hashtags) if payload.hashtags is not None else pub.hashtags
        if pub_type == "story":
            caption = ""
            hashtags = []

        fit_mode = payload.fit_mode or pub.fit_mode
        adapter = payload.adapter or pub.adapter

        if payload.clear_schedule:
            scheduled_at = None
        elif payload.scheduled_at is not None:
            scheduled_at = _parse_iso(payload.scheduled_at).isoformat() if payload.scheduled_at.strip() else None
        else:
            scheduled_at = pub.scheduled_at

        if payload.status is not None:
            status = payload.status
        elif scheduled_at:
            status = "scheduled"
        elif payload.clear_schedule or pub.status == "failed":
            status = "draft"
        else:
            status = pub.status

        metadata = payload.metadata if payload.metadata is not None else pub.metadata

        assets_changed = (
            pub_type != pub.pub_type
            or image_ids != pub.image_ids
            or fit_mode != pub.fit_mode
        )
        prepared = pub.prepared_files
        if assets_changed:
            for old_file in pub.prepared_files:
                old_path = Path(old_file)
                if old_path.exists():
                    old_path.unlink()
            prepared = self._prepare_publication_assets(pub_id, pub_type, image_ids, fit_mode)

        now = utcnow_iso()
        with self.db.connection() as conn:
            conn.execute(
                """
                UPDATE publications
                SET pub_type = ?, image_ids = ?, caption = ?, hashtags = ?, fit_mode = ?, adapter = ?,
                    scheduled_at = ?, status = ?, prepared_files = ?, metadata = ?, error_message = NULL, updated_at = ?
                WHERE id = ?
                """,
                (
                    pub_type,
                    json.dumps(image_ids),
                    caption,
                    json.dumps(hashtags, ensure_ascii=False),
                    fit_mode,
                    adapter,
                    scheduled_at,
                    status,
                    json.dumps(prepared),
                    json.dumps(metadata, ensure_ascii=False),
                    now,
                    pub_id,
                ),
            )
            # Keep pool_images 'queued' status aligned
            removed_ids = set(pub.image_ids) - set(image_ids)
            if removed_ids or status == "draft":
                release_ids = set(pub.image_ids) if status == "draft" else removed_ids
                for iid in release_ids:
                    conn.execute(
                        "UPDATE pool_images SET status = 'available', updated_at = ? WHERE id = ? AND status = 'queued'",
                        (now, iid),
                    )
            if status == "scheduled":
                for iid in image_ids:
                    conn.execute(
                        "UPDATE pool_images SET status = 'queued', updated_at = ? WHERE id = ? AND status = 'available'",
                        (now, iid),
                    )
            row = conn.execute("SELECT * FROM publications WHERE id = ?", (pub_id,)).fetchone()

        self.db.log_activity(
            "publication.update",
            actor=actor,
            entity_type="publication",
            entity_id=pub_id,
            details={"pub_type": pub_type, "status": status, "image_ids": image_ids},
        )
        updated_pub = self._row_to_publication(row)
        if payload.publish_now:
            return self.publish_publication(updated_pub.id, adapter_override=adapter, actor=actor)
        return updated_pub

    def delete_publication(self, pub_id: str, actor: str = "user") -> None:
        pub = self.get_publication(pub_id)
        with self.db.connection() as conn:
            conn.execute("DELETE FROM publications WHERE id = ?", (pub_id,))
            if pub.status in ("scheduled", "draft"):
                now = utcnow_iso()
                for iid in pub.image_ids:
                    conn.execute(
                        "UPDATE pool_images SET status = 'available', updated_at = ? WHERE id = ? AND status = 'queued'",
                        (now, iid),
                    )
        for p in pub.prepared_files:
            path = Path(p)
            if path.exists():
                path.unlink()
        self.db.log_activity("publication.delete", actor=actor, entity_type="publication", entity_id=pub_id)

    def publish_publication(
        self,
        pub_id: str,
        *,
        adapter_override: str | None = None,
        actor: str = "user",
    ) -> Publication:
        pub = self.get_publication(pub_id)
        adapter_name = adapter_override or pub.adapter or self.get_default_adapter_name()
        adapter = self.get_adapter(adapter_name)

        # Ensure prepared files exist
        prepared = pub.prepared_files
        if not prepared or not all(Path(p).exists() for p in prepared):
            prepared = self._prepare_publication_assets(pub.id, pub.pub_type, pub.image_ids, pub.fit_mode)

        now = utcnow_iso()
        with self.db.connection() as conn:
            conn.execute(
                "UPDATE publications SET status = 'publishing', adapter = ?, prepared_files = ?, error_message = NULL, updated_at = ? WHERE id = ?",
                (adapter_name, json.dumps(prepared), now, pub_id),
            )

        try:
            paths = [Path(p) for p in prepared]
            if pub.pub_type == "story":
                result = adapter.publish_story(
                    publication_id=pub.id,
                    image_path=paths[0],
                    metadata=pub.metadata,
                )
            elif pub.pub_type == "carousel":
                result = adapter.publish_carousel(
                    publication_id=pub.id,
                    image_paths=paths,
                    caption=pub.full_caption,
                    metadata=pub.metadata,
                )
            else:
                result = adapter.publish_post(
                    publication_id=pub.id,
                    image_path=paths[0],
                    caption=pub.full_caption,
                    metadata=pub.metadata,
                )

            done_at = utcnow_iso()
            with self.db.connection() as conn:
                conn.execute(
                    """
                    UPDATE publications
                    SET status = 'published', published_at = ?, external_id = ?,
                        external_url = ?, error_message = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (done_at, result.external_id, result.external_url, done_at, pub_id),
                )
                counter_col = "times_used_story" if pub.pub_type == "story" else "times_used_post"
                for iid in pub.image_ids:
                    conn.execute(
                        f"""
                        UPDATE pool_images
                        SET {counter_col} = {counter_col} + 1,
                            last_used_at = ?,
                            status = CASE WHEN status IN ('available', 'queued') THEN 'published' ELSE status END,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (done_at, done_at, iid),
                    )
                row = conn.execute("SELECT * FROM publications WHERE id = ?", (pub_id,)).fetchone()

            self.db.log_activity(
                "publication.published",
                actor=actor,
                entity_type="publication",
                entity_id=pub_id,
                details={
                    "adapter": adapter_name,
                    "pub_type": pub.pub_type,
                    "external_id": result.external_id,
                },
            )
            return self._row_to_publication(row)

        except Exception as exc:
            err_at = utcnow_iso()
            with self.db.connection() as conn:
                conn.execute(
                    """
                    UPDATE publications
                    SET status = 'failed', error_message = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (str(exc), err_at, pub_id),
                )
                row = conn.execute("SELECT * FROM publications WHERE id = ?", (pub_id,)).fetchone()
            self.db.log_activity(
                "publication.failed",
                actor=actor,
                entity_type="publication",
                entity_id=pub_id,
                details={"adapter": adapter_name, "error": str(exc)},
            )
            return self._row_to_publication(row)

    def run_due_scheduled(self) -> list[Publication]:
        """Publish all scheduled items whose scheduled_at <= now UTC."""
        if not self.is_scheduler_enabled():
            return []
        now = datetime.now(timezone.utc)
        scheduled = self.list_publications(status="scheduled", limit=100)
        processed: list[Publication] = []
        for pub in scheduled:
            if not pub.scheduled_at:
                continue
            if _parse_iso(pub.scheduled_at) <= now:
                processed.append(self.publish_publication(pub.id, actor="scheduler"))
        return processed

    # ------------------------------------------------------------------
    # Insights & analytics (Meta Graph API + SQLite snapshots)
    # ------------------------------------------------------------------
    def get_insights_auto_sync_minutes(self) -> int:
        raw = self.db.get_setting("insights_auto_sync_minutes", 30)
        try:
            return max(5, min(int(raw), 1440))
        except (TypeError, ValueError):
            return 30

    def _row_to_account_insights(self, row: Any) -> AccountInsights:
        raw_series = json.loads(row["daily_series"] or "[]")
        return AccountInsights(
            period_days=int(row["period_days"]),
            ig_user_id=row["ig_user_id"] or "",
            username=row["username"] or "",
            name=row["name"] or "",
            followers_count=row["followers_count"],
            follows_count=row["follows_count"],
            media_count=row["media_count"],
            totals=json.loads(row["totals"] or "{}"),
            daily_series=[AccountDailyPoint(**pt) for pt in raw_series if isinstance(pt, dict)],
            error_message=row["error_message"],
            synced_at=row["synced_at"],
        )

    def _row_to_media_insight(
        self,
        row: Any,
        *,
        pub_type: str | None = None,
        local_thumb_url: str | None = None,
        snapshots_count: int = 0,
    ) -> MediaInsight:
        nav_bd = json.loads(row["navigation_breakdown"] or "{}")
        extra = json.loads(row["extra_metrics"] or "{}")
        media_product = (row["media_product_type"] or "FEED").upper()
        media_type = (row["media_type"] or "IMAGE").upper()
        inferred_pub_type = pub_type
        if not inferred_pub_type:
            if media_product == "STORY":
                inferred_pub_type = "story"
            elif media_type == "CAROUSEL_ALBUM":
                inferred_pub_type = "carousel"
            elif media_product == "REELS":
                inferred_pub_type = "reel"
            else:
                inferred_pub_type = "post"
        return MediaInsight(
            external_id=row["external_id"],
            publication_id=row["publication_id"],
            pub_type=inferred_pub_type,
            media_type=media_type,
            media_product_type=media_product,
            caption=row["caption"] or "",
            permalink=row["permalink"],
            thumbnail_url=local_thumb_url or row["thumbnail_url"],
            published_at=row["published_at"],
            is_active=bool(row["is_active"]),
            views=row["views"],
            reach=row["reach"],
            likes=row["likes"],
            comments=row["comments"],
            saved=row["saved"],
            shares=row["shares"],
            replies=row["replies"],
            total_interactions=row["total_interactions"],
            follows=row["follows"],
            profile_visits=row["profile_visits"],
            profile_activity=row["profile_activity"],
            navigation=row["navigation"],
            navigation_breakdown=nav_bd,
            engagement_rate=self._compute_engagement_rate(
                reach=row["reach"],
                total_interactions=row["total_interactions"],
                likes=row["likes"],
                comments=row["comments"],
                saved=row["saved"],
                shares=row["shares"],
                replies=row["replies"],
            ),
            extra_metrics=extra,
            error_message=row["error_message"],
            synced_at=row["synced_at"],
            snapshots_count=snapshots_count,
        )

    def _upsert_media_insight(
        self,
        conn: sqlite3.Connection,
        *,
        external_id: str,
        publication_id: str | None,
        media_type: str,
        media_product_type: str,
        caption: str,
        permalink: str | None,
        thumbnail_url: str | None,
        published_at: str | None,
        is_active: bool,
        metrics: dict[str, int],
        navigation_breakdown: dict[str, int],
        extra_metrics: dict[str, Any],
        error_message: str | None,
        now: str,
    ) -> None:
        existing = conn.execute(
            "SELECT * FROM media_insights WHERE external_id = ?",
            (external_id,),
        ).fetchone()

        def pick_int(col: str) -> int | None:
            if col in metrics and metrics[col] is not None:
                return int(metrics[col])
            return existing[col] if existing is not None else None

        views = pick_int("views")
        reach = pick_int("reach")
        likes = pick_int("likes")
        comments = pick_int("comments")
        saved = pick_int("saved")
        shares = pick_int("shares")
        replies = pick_int("replies")
        total_interactions = pick_int("total_interactions")
        follows = pick_int("follows")
        profile_visits = pick_int("profile_visits")
        profile_activity = pick_int("profile_activity")
        navigation = pick_int("navigation")

        merged_nav = navigation_breakdown or (
            json.loads(existing["navigation_breakdown"] or "{}") if existing is not None else {}
        )
        merged_extra = {
            **(json.loads(existing["extra_metrics"] or "{}") if existing is not None else {}),
            **(extra_metrics or {}),
        }

        final_pub_id = publication_id or (existing["publication_id"] if existing is not None else None)
        final_caption = caption or (existing["caption"] if existing is not None else "")
        final_permalink = permalink or (existing["permalink"] if existing is not None else None)
        final_thumb = thumbnail_url or (existing["thumbnail_url"] if existing is not None else None)
        final_published_at = published_at or (existing["published_at"] if existing is not None else None)
        final_media_type = (media_type or (existing["media_type"] if existing is not None else "IMAGE")).upper()
        final_product = (
            media_product_type or (existing["media_product_type"] if existing is not None else "FEED")
        ).upper()
        created_at = existing["created_at"] if existing is not None else now

        conn.execute(
            """
            INSERT INTO media_insights (
                external_id, publication_id, media_type, media_product_type, caption, permalink,
                thumbnail_url, published_at, is_active,
                views, reach, likes, comments, saved, shares, replies,
                total_interactions, follows, profile_visits, profile_activity,
                navigation, navigation_breakdown, extra_metrics, error_message,
                synced_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(external_id) DO UPDATE SET
                publication_id = excluded.publication_id,
                media_type = excluded.media_type,
                media_product_type = excluded.media_product_type,
                caption = excluded.caption,
                permalink = excluded.permalink,
                thumbnail_url = excluded.thumbnail_url,
                published_at = excluded.published_at,
                is_active = excluded.is_active,
                views = excluded.views,
                reach = excluded.reach,
                likes = excluded.likes,
                comments = excluded.comments,
                saved = excluded.saved,
                shares = excluded.shares,
                replies = excluded.replies,
                total_interactions = excluded.total_interactions,
                follows = excluded.follows,
                profile_visits = excluded.profile_visits,
                profile_activity = excluded.profile_activity,
                navigation = excluded.navigation,
                navigation_breakdown = excluded.navigation_breakdown,
                extra_metrics = excluded.extra_metrics,
                error_message = excluded.error_message,
                synced_at = excluded.synced_at,
                updated_at = excluded.updated_at
            """,
            (
                external_id,
                final_pub_id,
                final_media_type,
                final_product,
                final_caption,
                final_permalink,
                final_thumb,
                final_published_at,
                1 if is_active else 0,
                views,
                reach,
                likes,
                comments,
                saved,
                shares,
                replies,
                total_interactions,
                follows,
                profile_visits,
                profile_activity,
                navigation,
                json.dumps(merged_nav, ensure_ascii=False),
                json.dumps(merged_extra, ensure_ascii=False),
                error_message,
                now,
                created_at,
                now,
            ),
        )

        # Record a historical snapshot if we got real metrics and values changed (or >= 30 min elapsed)
        has_any_metric = any(
            v is not None for v in (views, reach, likes, comments, saved, shares, replies, total_interactions)
        )
        if not has_any_metric:
            return

        last_snap = conn.execute(
            "SELECT * FROM media_insight_snapshots WHERE external_id = ? ORDER BY id DESC LIMIT 1",
            (external_id,),
        ).fetchone()

        should_snapshot = last_snap is None
        if last_snap is not None:
            changed = any(
                (
                    last_snap["views"] != views,
                    last_snap["reach"] != reach,
                    last_snap["likes"] != likes,
                    last_snap["comments"] != comments,
                    last_snap["saved"] != saved,
                    last_snap["shares"] != shares,
                    last_snap["replies"] != replies,
                    last_snap["total_interactions"] != total_interactions,
                    last_snap["navigation"] != navigation,
                )
            )
            elapsed_ok = False
            try:
                elapsed_ok = (_parse_iso(now) - _parse_iso(last_snap["captured_at"])) >= timedelta(minutes=30)
            except Exception:
                elapsed_ok = True
            should_snapshot = changed or elapsed_ok

        if should_snapshot:
            conn.execute(
                """
                INSERT INTO media_insight_snapshots (
                    external_id, views, reach, likes, comments, saved, shares, replies,
                    total_interactions, follows, profile_visits, navigation,
                    navigation_breakdown, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    external_id,
                    views,
                    reach,
                    likes,
                    comments,
                    saved,
                    shares,
                    replies,
                    total_interactions,
                    follows,
                    profile_visits,
                    navigation,
                    json.dumps(merged_nav, ensure_ascii=False),
                    now,
                ),
            )

    def _get_graph_adapter_for_insights(self) -> GraphAPIAdapter:
        adapter = self.get_adapter("graph_api")
        assert isinstance(adapter, GraphAPIAdapter)
        if not adapter.can_fetch_insights():
            raise ValueError(
                "El adaptador graph_api no está configurado para consultar métricas "
                "(falta Access Token o IG User ID en Configuración)."
            )
        return adapter

    def sync_insights(
        self,
        *,
        days: int = 7,
        include_account_media: bool = True,
        stories_only: bool = False,
        actor: str = "user",
    ) -> InsightsSyncResponse:
        """Synchronize account, feed/carousel/reel, and active 24h story insights from Meta Graph API."""
        adapter = self._get_graph_adapter_for_insights()
        clamped_days = max(1, min(int(days), 30))
        now = utcnow_iso()
        errors: list[str] = []
        account_model: AccountInsights | None = None

        # 1. Account insights
        if not stories_only:
            try:
                acc = adapter.fetch_account_insights(days=clamped_days)
                with self.db.connection() as conn:
                    conn.execute(
                        """
                        INSERT INTO account_insights (
                            period_days, ig_user_id, username, name,
                            followers_count, follows_count, media_count,
                            totals, daily_series, error_message, synced_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(period_days) DO UPDATE SET
                            ig_user_id = excluded.ig_user_id,
                            username = excluded.username,
                            name = excluded.name,
                            followers_count = excluded.followers_count,
                            follows_count = excluded.follows_count,
                            media_count = excluded.media_count,
                            totals = excluded.totals,
                            daily_series = excluded.daily_series,
                            error_message = excluded.error_message,
                            synced_at = excluded.synced_at
                        """,
                        (
                            acc["period_days"],
                            acc["ig_user_id"],
                            acc["username"],
                            acc["name"],
                            acc["followers_count"],
                            acc["follows_count"],
                            acc["media_count"],
                            json.dumps(acc["totals"], ensure_ascii=False),
                            json.dumps(acc["daily_series"], ensure_ascii=False),
                            acc["error_message"],
                            now,
                        ),
                    )
                    acc_row = conn.execute(
                        "SELECT * FROM account_insights WHERE period_days = ?",
                        (acc["period_days"],),
                    ).fetchone()
                account_model = self._row_to_account_insights(acc_row)
                if acc.get("error_message"):
                    errors.append(f"Cuenta: {acc['error_message']}")
            except Exception as exc:
                errors.append(f"Cuenta: {exc}")

        # Build local publication lookup by external_id
        with self.db.connection() as conn:
            pub_rows = conn.execute(
                "SELECT * FROM publications WHERE status = 'published' AND adapter = 'graph_api' AND external_id IS NOT NULL"
            ).fetchall()
        pubs_by_ext = {str(r["external_id"]): r for r in pub_rows if r["external_id"]}

        synced_stories = 0
        synced_media = 0
        active_story_ids: set[str] = set()

        # 2. Active stories (<24h) — capture snapshots before Meta expires them
        try:
            active_stories = adapter.fetch_active_stories()
            for st in active_stories:
                ext_id = str(st.get("id") or "").strip()
                if not ext_id:
                    continue
                active_story_ids.add(ext_id)
                local_pub = pubs_by_ext.get(ext_id)
                try:
                    res = adapter.fetch_media_insights(
                        ext_id,
                        media_product_type="STORY",
                        media_type=str(st.get("media_type") or "IMAGE"),
                    )
                    thumb = st.get("thumbnail_url") or st.get("media_url")
                    with self.db.connection() as conn:
                        self._upsert_media_insight(
                            conn,
                            external_id=ext_id,
                            publication_id=local_pub["id"] if local_pub else None,
                            media_type=str(st.get("media_type") or "IMAGE"),
                            media_product_type="STORY",
                            caption=str(st.get("caption") or ""),
                            permalink=st.get("permalink") or (local_pub["external_url"] if local_pub else None),
                            thumbnail_url=thumb,
                            published_at=st.get("timestamp") or (local_pub["published_at"] if local_pub else None),
                            is_active=not res.get("expired", False),
                            metrics=res["metrics"],
                            navigation_breakdown=res["navigation_breakdown"],
                            extra_metrics=res["extra_metrics"],
                            error_message=res["error_message"],
                            now=now,
                        )
                    synced_stories += 1
                    if res.get("error_message"):
                        errors.append(f"Story {ext_id}: {res['error_message']}")
                except Exception as exc:
                    errors.append(f"Story {ext_id}: {exc}")

            # Mark previously active stories no longer live as archived/expired (preserving their metrics)
            with self.db.connection() as conn:
                if active_story_ids:
                    placeholders = ",".join("?" for _ in active_story_ids)
                    conn.execute(
                        f"""
                        UPDATE media_insights
                        SET is_active = 0, updated_at = ?
                        WHERE media_product_type = 'STORY' AND is_active = 1
                          AND external_id NOT IN ({placeholders})
                        """,
                        [now, *active_story_ids],
                    )
                else:
                    conn.execute(
                        """
                        UPDATE media_insights
                        SET is_active = 0, updated_at = ?
                        WHERE media_product_type = 'STORY' AND is_active = 1
                        """,
                        (now,),
                    )
        except Exception as exc:
            errors.append(f"Historias activas: {exc}")

        # 3. Feed posts, carousels, and reels
        seen_media_ids: set[str] = set(active_story_ids)
        if not stories_only:
            if include_account_media:
                try:
                    media_list = adapter.fetch_account_media(limit=50)
                    for item in media_list:
                        ext_id = str(item.get("id") or "").strip()
                        if not ext_id:
                            continue
                        seen_media_ids.add(ext_id)
                        local_pub = pubs_by_ext.get(ext_id)
                        m_type = str(item.get("media_type") or "IMAGE")
                        m_prod = str(item.get("media_product_type") or "FEED")
                        try:
                            res = adapter.fetch_media_insights(
                                ext_id,
                                media_product_type=m_prod,
                                media_type=m_type,
                                fallback_likes=item.get("like_count"),
                                fallback_comments=item.get("comments_count"),
                            )
                            thumb = item.get("thumbnail_url") or item.get("media_url")
                            with self.db.connection() as conn:
                                self._upsert_media_insight(
                                    conn,
                                    external_id=ext_id,
                                    publication_id=local_pub["id"] if local_pub else None,
                                    media_type=m_type,
                                    media_product_type=m_prod,
                                    caption=str(
                                        item.get("caption")
                                        or (local_pub["caption"] if local_pub else "")
                                    ),
                                    permalink=item.get("permalink")
                                    or (local_pub["external_url"] if local_pub else None),
                                    thumbnail_url=thumb,
                                    published_at=item.get("timestamp")
                                    or (local_pub["published_at"] if local_pub else None),
                                    is_active=not res.get("expired", False),
                                    metrics=res["metrics"],
                                    navigation_breakdown=res["navigation_breakdown"],
                                    extra_metrics=res["extra_metrics"],
                                    error_message=res["error_message"],
                                    now=now,
                                )
                            synced_media += 1
                            if res.get("error_message"):
                                errors.append(f"Media {ext_id}: {res['error_message']}")
                        except Exception as exc:
                            errors.append(f"Media {ext_id}: {exc}")
                except Exception as exc:
                    errors.append(f"Publicaciones de cuenta: {exc}")

            # Sync any local published feed/carousel publications missed by the media page
            for ext_id, local_pub in pubs_by_ext.items():
                if ext_id in seen_media_ids or local_pub["pub_type"] == "story":
                    continue
                m_type = "CAROUSEL_ALBUM" if local_pub["pub_type"] == "carousel" else "IMAGE"
                try:
                    res = adapter.fetch_media_insights(
                        ext_id,
                        media_product_type="FEED",
                        media_type=m_type,
                        include_metadata=True,
                    )
                    meta = res.get("metadata") or {}
                    with self.db.connection() as conn:
                        self._upsert_media_insight(
                            conn,
                            external_id=ext_id,
                            publication_id=local_pub["id"],
                            media_type=str(meta.get("media_type") or m_type),
                            media_product_type=str(meta.get("media_product_type") or "FEED"),
                            caption=str(meta.get("caption") or local_pub["caption"] or ""),
                            permalink=meta.get("permalink") or local_pub["external_url"],
                            thumbnail_url=meta.get("thumbnail_url") or meta.get("media_url"),
                            published_at=meta.get("timestamp") or local_pub["published_at"],
                            is_active=not res.get("expired", False),
                            metrics=res["metrics"],
                            navigation_breakdown=res["navigation_breakdown"],
                            extra_metrics=res["extra_metrics"],
                            error_message=res["error_message"],
                            now=now,
                        )
                    synced_media += 1
                except Exception as exc:
                    errors.append(f"Pub {local_pub['id']}: {exc}")

        self.db.set_setting("insights_last_sync_at", now)
        self.db.log_activity(
            "insights.sync",
            actor=actor,
            entity_type="insights",
            details={
                "days": clamped_days,
                "stories_only": stories_only,
                "synced_media": synced_media,
                "synced_stories": synced_stories,
                "errors": len(errors),
            },
        )
        return InsightsSyncResponse(
            synced_at=now,
            period_days=clamped_days,
            account=account_model,
            synced_media_count=synced_media,
            synced_stories_count=synced_stories,
            errors=errors,
        )

    def _resolve_media_external_id(self, identifier: str) -> tuple[str, Any | None]:
        """Resolve either a local publication_id or a Meta external_id."""
        with self.db.connection() as conn:
            pub = conn.execute("SELECT * FROM publications WHERE id = ?", (identifier,)).fetchone()
            if pub is not None:
                if not pub["external_id"]:
                    raise ValueError(f"La publicación '{identifier}' todavía no fue publicada en Instagram.")
                if pub["adapter"] != "graph_api":
                    raise ValueError(
                        f"La publicación '{identifier}' usó '{pub['adapter']}' (simulación local) y no tiene métricas en Meta."
                    )
                return str(pub["external_id"]), pub
            existing = conn.execute(
                "SELECT * FROM media_insights WHERE external_id = ?",
                (identifier,),
            ).fetchone()
            if existing is not None:
                linked_pub = None
                if existing["publication_id"]:
                    linked_pub = conn.execute(
                        "SELECT * FROM publications WHERE id = ?",
                        (existing["publication_id"],),
                    ).fetchone()
                return str(existing["external_id"]), linked_pub
            pub_by_ext = conn.execute(
                "SELECT * FROM publications WHERE external_id = ?",
                (identifier,),
            ).fetchone()
            if pub_by_ext is not None:
                return str(identifier), pub_by_ext
        raise KeyError(f"Métrica o publicación no encontrada: {identifier}")

    def sync_publication_insights(self, identifier: str, *, actor: str = "user") -> MediaInsight:
        """Refresh insights on demand for a single publication (by local ID or Meta external_id)."""
        adapter = self._get_graph_adapter_for_insights()
        external_id, local_pub = self._resolve_media_external_id(identifier)
        now = utcnow_iso()

        with self.db.connection() as conn:
            existing = conn.execute(
                "SELECT * FROM media_insights WHERE external_id = ?",
                (external_id,),
            ).fetchone()

        if existing is not None:
            product = str(existing["media_product_type"] or "FEED")
            m_type = str(existing["media_type"] or "IMAGE")
        elif local_pub is not None:
            product = "STORY" if local_pub["pub_type"] == "story" else "FEED"
            m_type = "CAROUSEL_ALBUM" if local_pub["pub_type"] == "carousel" else "IMAGE"
        else:
            product = "FEED"
            m_type = "IMAGE"

        res = adapter.fetch_media_insights(
            external_id,
            media_product_type=product,
            media_type=m_type,
            include_metadata=True,
        )
        meta = res.get("metadata") or {}
        with self.db.connection() as conn:
            self._upsert_media_insight(
                conn,
                external_id=external_id,
                publication_id=local_pub["id"] if local_pub else (existing["publication_id"] if existing else None),
                media_type=str(meta.get("media_type") or m_type),
                media_product_type=str(meta.get("media_product_type") or product),
                caption=str(meta.get("caption") or (local_pub["caption"] if local_pub else "")),
                permalink=meta.get("permalink") or (local_pub["external_url"] if local_pub else None),
                thumbnail_url=meta.get("thumbnail_url") or meta.get("media_url"),
                published_at=meta.get("timestamp") or (local_pub["published_at"] if local_pub else None),
                is_active=not res.get("expired", False),
                metrics=res["metrics"],
                navigation_breakdown=res["navigation_breakdown"],
                extra_metrics=res["extra_metrics"],
                error_message=res["error_message"],
                now=now,
            )

        self.db.log_activity(
            "insights.sync_item",
            actor=actor,
            entity_type="media_insight",
            entity_id=external_id,
            details={"publication_id": local_pub["id"] if local_pub else None},
        )
        return self.get_media_insight(external_id)

    def _local_thumb_for_pub(self, pub_row: Any | None) -> str | None:
        if pub_row is None:
            return None
        raw_prepared = json.loads(pub_row["prepared_files"] or "[]")
        if raw_prepared:
            resolved = self._resolve_prepared_file(raw_prepared[0])
            if resolved and Path(resolved).exists():
                return f"/media/prepared/{Path(resolved).name}"
        return None

    def get_media_insight(self, identifier: str) -> MediaInsight:
        external_id, local_pub = self._resolve_media_external_id(identifier)
        with self.db.connection() as conn:
            row = conn.execute(
                "SELECT * FROM media_insights WHERE external_id = ?",
                (external_id,),
            ).fetchone()
            if not row:
                raise KeyError(f"Sin métricas sincronizadas para: {identifier}")
            snap_count_row = conn.execute(
                "SELECT COUNT(*) AS c FROM media_insight_snapshots WHERE external_id = ?",
                (external_id,),
            ).fetchone()
        return self._row_to_media_insight(
            row,
            pub_type=local_pub["pub_type"] if local_pub else None,
            local_thumb_url=self._local_thumb_for_pub(local_pub),
            snapshots_count=int(snap_count_row["c"]) if snap_count_row else 0,
        )

    def get_media_insight_history(self, identifier: str, limit: int = 50) -> list[MediaInsightSnapshot]:
        external_id, _ = self._resolve_media_external_id(identifier)
        with self.db.connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM media_insight_snapshots
                WHERE external_id = ?
                ORDER BY captured_at DESC, id DESC
                LIMIT ?
                """,
                (external_id, max(1, min(limit, 500))),
            ).fetchall()
        return [
            MediaInsightSnapshot(
                id=r["id"],
                external_id=r["external_id"],
                views=r["views"],
                reach=r["reach"],
                likes=r["likes"],
                comments=r["comments"],
                saved=r["saved"],
                shares=r["shares"],
                replies=r["replies"],
                total_interactions=r["total_interactions"],
                follows=r["follows"],
                profile_visits=r["profile_visits"],
                navigation=r["navigation"],
                navigation_breakdown=json.loads(r["navigation_breakdown"] or "{}"),
                captured_at=r["captured_at"],
            )
            for r in rows
        ]

    def get_insights_overview(
        self,
        *,
        days: int = 7,
        media_product_type: str | None = None,
        sort_by: str = "published_at",
        limit: int = 100,
    ) -> InsightsOverview:
        clamped_days = max(1, min(int(days), 30))
        graph_adapter = self.get_adapter("graph_api")
        assert isinstance(graph_adapter, GraphAPIAdapter)

        with self.db.connection() as conn:
            acc_row = conn.execute(
                "SELECT * FROM account_insights WHERE period_days = ?",
                (clamped_days,),
            ).fetchone()
            if acc_row is None:
                acc_row = conn.execute(
                    "SELECT * FROM account_insights ORDER BY synced_at DESC LIMIT 1"
                ).fetchone()

            clauses: list[str] = []
            params: list[Any] = []
            if media_product_type:
                clauses.append("UPPER(media_product_type) = ?")
                params.append(media_product_type.strip().upper())

            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            rows = conn.execute(
                f"SELECT * FROM media_insights {where} ORDER BY COALESCE(published_at, synced_at) DESC",
                params,
            ).fetchall()

            pub_rows = conn.execute("SELECT id, pub_type, prepared_files, external_id FROM publications").fetchall()
            pubs_by_id = {r["id"]: r for r in pub_rows}
            pubs_by_ext = {r["external_id"]: r for r in pub_rows if r["external_id"]}

            snap_rows = conn.execute(
                "SELECT external_id, COUNT(*) AS c FROM media_insight_snapshots GROUP BY external_id"
            ).fetchall()
            snap_counts = {r["external_id"]: int(r["c"]) for r in snap_rows}

        items: list[MediaInsight] = []
        for r in rows:
            pub = pubs_by_id.get(r["publication_id"]) if r["publication_id"] else pubs_by_ext.get(r["external_id"])
            items.append(
                self._row_to_media_insight(
                    r,
                    pub_type=pub["pub_type"] if pub else None,
                    local_thumb_url=self._local_thumb_for_pub(pub),
                    snapshots_count=snap_counts.get(r["external_id"], 0),
                )
            )

        sort_map = {
            "published_at": lambda x: (x.published_at or "", x.synced_at),
            "views": lambda x: (x.views if x.views is not None else -1, x.published_at or ""),
            "reach": lambda x: (x.reach if x.reach is not None else -1, x.published_at or ""),
            "total_interactions": lambda x: (
                x.total_interactions if x.total_interactions is not None else -1,
                x.published_at or "",
            ),
            "likes": lambda x: (x.likes if x.likes is not None else -1, x.published_at or ""),
            "saved": lambda x: (x.saved if x.saved is not None else -1, x.published_at or ""),
            "shares": lambda x: (x.shares if x.shares is not None else -1, x.published_at or ""),
            "profile_visits": lambda x: (
                x.profile_visits if x.profile_visits is not None else -1,
                x.published_at or "",
            ),
        }
        key_fn = sort_map.get(sort_by, sort_map["published_at"])
        items.sort(key=key_fn, reverse=True)
        capped_items = items[: max(1, min(limit, 500))]

        ers = [i.engagement_rate for i in items if i.engagement_rate is not None]
        summary = InsightsSummary(
            total_items=len(items),
            feed_count=sum(1 for i in items if i.media_product_type == "FEED"),
            story_count=sum(1 for i in items if i.media_product_type == "STORY"),
            active_stories_count=sum(1 for i in items if i.media_product_type == "STORY" and i.is_active),
            reels_count=sum(1 for i in items if i.media_product_type == "REELS"),
            total_views=sum(i.views or 0 for i in items),
            total_reach=sum(i.reach or 0 for i in items),
            total_likes=sum(i.likes or 0 for i in items),
            total_comments=sum(i.comments or 0 for i in items),
            total_saved=sum(i.saved or 0 for i in items),
            total_shares=sum(i.shares or 0 for i in items),
            total_replies=sum(i.replies or 0 for i in items),
            total_interactions=sum(i.total_interactions or 0 for i in items),
            total_profile_visits=sum(i.profile_visits or 0 for i in items),
            total_follows=sum(i.follows or 0 for i in items),
            avg_engagement_rate=round(sum(ers) / len(ers), 2) if ers else None,
        )

        last_sync = self.db.get_setting("insights_last_sync_at")
        if not last_sync and acc_row is not None:
            last_sync = acc_row["synced_at"]

        return InsightsOverview(
            account=self._row_to_account_insights(acc_row) if acc_row is not None else None,
            summary=summary,
            items=capped_items,
            last_synced_at=str(last_sync) if last_sync else None,
            adapter_configured=graph_adapter.can_fetch_insights(),
            auto_sync_interval_minutes=self.get_insights_auto_sync_minutes(),
        )

    def run_periodic_insights_sync(self) -> InsightsSyncResponse | None:
        """Auto-sync insights periodically so active 24h stories are snapshotted before expiring."""
        if not self.is_scheduler_enabled():
            return None
        graph_adapter = self.get_adapter("graph_api")
        assert isinstance(graph_adapter, GraphAPIAdapter)
        if not graph_adapter.can_fetch_insights():
            return None

        interval_min = self.get_insights_auto_sync_minutes()
        last_sync = self.db.get_setting("insights_last_sync_at")
        if last_sync:
            try:
                if (datetime.now(timezone.utc) - _parse_iso(str(last_sync))) < timedelta(minutes=interval_min):
                    return None
            except Exception:
                pass

        return self.sync_insights(days=7, include_account_media=True, stories_only=False, actor="scheduler")

    # ------------------------------------------------------------------
    # Stats & activity logs
    # ------------------------------------------------------------------
    def get_stats(self) -> DashboardStats:
        with self.db.connection() as conn:
            pool_rows = conn.execute("SELECT status, COUNT(*) as c FROM pool_images GROUP BY status").fetchall()
            pub_rows = conn.execute("SELECT status, COUNT(*) as c FROM publications GROUP BY status").fetchall()
            type_rows = conn.execute(
                "SELECT pub_type, COUNT(*) as c FROM publications WHERE status = 'published' GROUP BY pub_type"
            ).fetchall()

        pool_counts = {r["status"]: r["c"] for r in pool_rows}
        pub_counts = {r["status"]: r["c"] for r in pub_rows}
        type_counts = {r["pub_type"]: r["c"] for r in type_rows}

        return DashboardStats(
            pool_total=sum(pool_counts.values()),
            pool_available=pool_counts.get("available", 0),
            pool_queued=pool_counts.get("queued", 0),
            pool_published=pool_counts.get("published", 0),
            pool_archived=pool_counts.get("archived", 0),
            publications_draft=pub_counts.get("draft", 0),
            publications_scheduled=pub_counts.get("scheduled", 0),
            publications_published=pub_counts.get("published", 0),
            publications_failed=pub_counts.get("failed", 0),
            posts_published=type_counts.get("post", 0) + type_counts.get("carousel", 0),
            stories_published=type_counts.get("story", 0),
            default_adapter=self.get_default_adapter_name(),
            scheduler_enabled=self.is_scheduler_enabled(),
        )

    def list_activity(self, limit: int = 50) -> list[ActivityLogEntry]:
        with self.db.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM activity_logs ORDER BY id DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [
            ActivityLogEntry(
                id=r["id"],
                actor=r["actor"],
                action=r["action"],
                entity_type=r["entity_type"],
                entity_id=r["entity_id"],
                details=json.loads(r["details"] or "{}"),
                created_at=r["created_at"],
            )
            for r in rows
        ]
