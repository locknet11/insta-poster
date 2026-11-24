from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field

ImageStatus = Literal["available", "queued", "published", "archived"]
PubType = Literal["post", "carousel", "story"]
PubStatus = Literal["draft", "scheduled", "publishing", "published", "failed"]
FitMode = Literal["auto", "crop", "pad_blur", "pad_solid"]
AdapterName = Literal["dry_run", "graph_api"]


class PoolImage(BaseModel):
    id: str
    filename: str
    original_filename: str
    filepath: str
    thumbnail_url: str
    image_url: str
    source_path: str | None = None
    sha256: str
    width: int
    height: int
    aspect_ratio: float
    file_size: int
    mime_type: str
    status: ImageStatus = "available"
    tags: list[str] = Field(default_factory=list)
    notes: str = ""
    suitable_for: list[str] = Field(default_factory=lambda: ["post", "story"])
    times_used_post: int = 0
    times_used_story: int = 0
    last_used_at: str | None = None
    created_at: str
    updated_at: str


class PoolImageUpdate(BaseModel):
    tags: list[str] | None = None
    notes: str | None = None
    status: ImageStatus | None = None


class FolderSyncRequest(BaseModel):
    folder_path: str
    tags: list[str] = Field(default_factory=list)
    recursive: bool = True


class FolderSyncResponse(BaseModel):
    folder_path: str
    added: list[PoolImage]
    skipped_duplicates: int
    errors: list[str]


class PublicationCreate(BaseModel):
    pub_type: PubType
    image_ids: list[str] = Field(min_length=1, max_length=10)
    caption: str = ""
    hashtags: list[str] = Field(default_factory=list)
    fit_mode: FitMode = "auto"
    adapter: AdapterName | None = None
    scheduled_at: str | None = None
    publish_now: bool = False
    created_by: str = "user"
    metadata: dict[str, Any] = Field(default_factory=dict)


class PublicationUpdate(BaseModel):
    pub_type: PubType | None = None
    image_ids: list[str] | None = Field(default=None, min_length=1, max_length=10)
    caption: str | None = None
    hashtags: list[str] | None = None
    fit_mode: FitMode | None = None
    adapter: AdapterName | None = None
    scheduled_at: str | None = None
    clear_schedule: bool = False
    status: Literal["draft", "scheduled"] | None = None
    publish_now: bool = False
    metadata: dict[str, Any] | None = None


class Publication(BaseModel):
    id: str
    pub_type: PubType
    status: PubStatus
    adapter: str
    image_ids: list[str]
    prepared_files: list[str] = Field(default_factory=list)
    prepared_urls: list[str] = Field(default_factory=list)
    caption: str = ""
    hashtags: list[str] = Field(default_factory=list)
    full_caption: str = ""
    fit_mode: str = "auto"
    scheduled_at: str | None = None
    published_at: str | None = None
    external_id: str | None = None
    external_url: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_by: str = "user"
    created_at: str
    updated_at: str


class AdapterStatus(BaseModel):
    name: str
    label: str
    configured: bool
    available: bool
    is_default: bool
    supports: list[str]
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class AdapterConfigUpdate(BaseModel):
    default_adapter: AdapterName | None = None
    graph_api: dict[str, Any] | None = None
    scheduler_enabled: bool | None = None


class ActivityLogEntry(BaseModel):
    id: int
    actor: str
    action: str
    entity_type: str | None = None
    entity_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class DashboardStats(BaseModel):
    pool_total: int
    pool_available: int
    pool_queued: int
    pool_published: int
    pool_archived: int
    publications_draft: int
    publications_scheduled: int
    publications_published: int
    publications_failed: int
    posts_published: int
    stories_published: int
    default_adapter: str
    scheduler_enabled: bool


class UserPublic(BaseModel):
    id: str
    username: str
    created_at: str
    updated_at: str
    last_login_at: str | None = None


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6, max_length=256)


class UserPasswordUpdateRequest(BaseModel):
    password: str = Field(min_length=6, max_length=256)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=6, max_length=256)


class AuthStatusResponse(BaseModel):
    authenticated: bool
    needs_setup: bool
    user: UserPublic | None = None
    auth_method: Literal["cookie", "bearer", "api_key", "disabled"] | None = None


class AuthLoginResponse(BaseModel):
    user: UserPublic
    token: str
    expires_at: str


class ApiKeyPublic(BaseModel):
    id: str
    name: str
    key_prefix: str
    created_by: str
    created_at: str
    last_used_at: str | None = None


class ApiKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class ApiKeyCreatedResponse(ApiKeyPublic):
    api_key: str
