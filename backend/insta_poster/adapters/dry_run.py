from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from insta_poster.adapters.base import AdapterCheckResult, InstagramAdapter, PublishResult


class DryRunAdapter(InstagramAdapter):
    """Local simulation adapter that validates, renders, and archives receipts locally."""

    name = "dry_run"
    label = "Simulación Local (Dry-Run)"
    supports = ["post", "carousel", "story"]

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def check_health(self, verify_remote: bool = False) -> AdapterCheckResult:
        return AdapterCheckResult(
            configured=True,
            available=True,
            message="Listo para simular publicaciones localmente sin tocar tu cuenta real.",
            details={"output_dir": str(self.output_dir)},
        )

    def _write_receipt(
        self,
        *,
        publication_id: str,
        pub_type: str,
        image_paths: list[Path],
        caption: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> PublishResult:
        pub_dir = self.output_dir / publication_id
        pub_dir.mkdir(parents=True, exist_ok=True)
        copied: list[str] = []
        for idx, p in enumerate(image_paths):
            dst = pub_dir / f"{idx + 1:02d}_{p.name}"
            shutil.copy2(p, dst)
            copied.append(str(dst))

        sim_id = f"dryrun_{pub_type}_{publication_id[:10]}"
        receipt = {
            "simulated": True,
            "external_id": sim_id,
            "publication_id": publication_id,
            "pub_type": pub_type,
            "caption": caption,
            "files": copied,
            "metadata": metadata or {},
            "published_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
        receipt_path = pub_dir / "receipt.json"
        receipt_path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")

        return PublishResult(
            external_id=sim_id,
            external_url=f"local://dry-run/{publication_id}",
            raw={"receipt_path": str(receipt_path), "files": copied},
        )

    def publish_post(
        self,
        *,
        publication_id: str,
        image_path: Path,
        caption: str,
        metadata: dict[str, Any] | None = None,
    ) -> PublishResult:
        return self._write_receipt(
            publication_id=publication_id,
            pub_type="post",
            image_paths=[image_path],
            caption=caption,
            metadata=metadata,
        )

    def publish_carousel(
        self,
        *,
        publication_id: str,
        image_paths: list[Path],
        caption: str,
        metadata: dict[str, Any] | None = None,
    ) -> PublishResult:
        return self._write_receipt(
            publication_id=publication_id,
            pub_type="carousel",
            image_paths=image_paths,
            caption=caption,
            metadata=metadata,
        )

    def publish_story(
        self,
        *,
        publication_id: str,
        image_path: Path,
        metadata: dict[str, Any] | None = None,
    ) -> PublishResult:
        return self._write_receipt(
            publication_id=publication_id,
            pub_type="story",
            image_paths=[image_path],
            caption="",
            metadata=metadata,
        )
