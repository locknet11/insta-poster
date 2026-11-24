from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class PublishResult:
    external_id: str
    external_url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class AdapterCheckResult:
    configured: bool
    available: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)


class InstagramAdapter(ABC):
    name: str
    label: str
    supports: list[str] = ["post", "carousel", "story"]

    @abstractmethod
    def check_health(self, verify_remote: bool = False) -> AdapterCheckResult:
        """Check whether the adapter is available and properly configured."""

    @abstractmethod
    def publish_post(
        self,
        *,
        publication_id: str,
        image_path: Path,
        caption: str,
        metadata: dict[str, Any] | None = None,
    ) -> PublishResult:
        """Publish a single feed image post."""

    @abstractmethod
    def publish_carousel(
        self,
        *,
        publication_id: str,
        image_paths: list[Path],
        caption: str,
        metadata: dict[str, Any] | None = None,
    ) -> PublishResult:
        """Publish a multi-image carousel post."""

    @abstractmethod
    def publish_story(
        self,
        *,
        publication_id: str,
        image_path: Path,
        metadata: dict[str, Any] | None = None,
    ) -> PublishResult:
        """Publish a 9:16 Instagram Story."""
