from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _default_project_root() -> Path:
    env_root = os.environ.get("INSTA_POSTER_HOME")
    if env_root:
        return Path(env_root).expanduser().resolve()
    return Path.cwd().resolve()


@dataclass(frozen=True)
class AppPaths:
    root: Path
    data_dir: Path
    db_path: Path
    pool_dir: Path
    thumbnails_dir: Path
    prepared_dir: Path
    dry_run_dir: Path

    @classmethod
    def from_data_dir(cls, data_dir: Path | None = None) -> "AppPaths":
        root = _default_project_root()
        if data_dir is None:
            env_data = os.environ.get("INSTA_POSTER_DATA_DIR")
            data_dir = Path(env_data).expanduser().resolve() if env_data else (root / "data")
        else:
            data_dir = Path(data_dir).expanduser().resolve()

        paths = cls(
            root=root,
            data_dir=data_dir,
            db_path=data_dir / "insta_poster.db",
            pool_dir=data_dir / "pool",
            thumbnails_dir=data_dir / "thumbnails",
            prepared_dir=data_dir / "prepared",
            dry_run_dir=data_dir / "published_dry_run",
        )
        paths.ensure_dirs()
        return paths

    def ensure_dirs(self) -> None:
        for d in (
            self.data_dir,
            self.pool_dir,
            self.thumbnails_dir,
            self.prepared_dir,
            self.dry_run_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)


def get_api_key() -> str | None:
    key = os.environ.get("INSTA_POSTER_API_KEY", "").strip()
    return key or None


def get_session_ttl_days() -> int:
    raw = os.environ.get("INSTA_POSTER_SESSION_TTL_DAYS", "30").strip()
    try:
        days = int(raw)
        return max(1, min(days, 365))
    except ValueError:
        return 30


def get_cookie_secure_override() -> bool | None:
    raw = os.environ.get("INSTA_POSTER_COOKIE_SECURE", "").strip().lower()
    if raw in ("1", "true", "yes"):
        return True
    if raw in ("0", "false", "no"):
        return False
    return None


def get_cors_origins() -> list[str]:
    raw = os.environ.get("INSTA_POSTER_CORS_ORIGINS", "").strip()
    if raw:
        return [o.strip() for o in raw.split(",") if o.strip()]
    return [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]


def is_auth_disabled() -> bool:
    return os.environ.get("INSTA_POSTER_AUTH_DISABLED", "").strip().lower() in ("1", "true", "yes")


def get_init_user_credentials() -> tuple[str, str] | None:
    username = os.environ.get("INSTA_POSTER_INIT_USER", "").strip()
    password = os.environ.get("INSTA_POSTER_INIT_PASSWORD", "")
    if username and password:
        return username, password
    return None


def get_secret_key_env() -> str | None:
    key = os.environ.get("INSTA_POSTER_SECRET_KEY", "").strip()
    return key or None


SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff"}
