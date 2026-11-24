from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from insta_poster.api import create_app


def _png_bytes(size: tuple[int, int], color: tuple[int, int, int]) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_api_end_to_end_post_carousel_story_and_scheduler(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path / "data", enable_scheduler=False)
    client = TestClient(app)

    # Health is public; protected routes require auth until setup/login
    assert client.get("/api/health").json()["ok"] is True
    assert client.get("/api/agent/schema").status_code == 401

    setup = client.post("/api/auth/setup", json={"username": "tester", "password": "secret-password"})
    assert setup.status_code == 200
    assert setup.json()["user"]["username"] == "tester"

    schema = client.get("/api/agent/schema").json()
    assert "cli_examples" in schema
    assert "python_code_mode" in schema

    # Upload two images
    upload = client.post(
        "/api/pool/upload",
        files=[
            ("files", ("img1.png", _png_bytes((1200, 1200), (220, 50, 90)), "image/png")),
            ("files", ("img2.png", _png_bytes((1080, 1920), (50, 150, 220)), "image/png")),
        ],
        data={"tags": "campana,lanzamiento", "notes": "Fotos de prueba"},
    )
    assert upload.status_code == 200
    added = upload.json()["added"]
    assert len(added) == 2
    id1, id2 = added[0]["id"], added[1]["id"]

    # Live preview JPEG for story & post
    prev = client.get(f"/api/pool/{id1}/preview?pub_type=story&fit_mode=pad_blur")
    assert prev.status_code == 200
    assert prev.headers["content-type"] == "image/jpeg"

    # Publish Story immediately via dry_run
    story_resp = client.post(
        "/api/publications",
        json={
            "pub_type": "story",
            "image_ids": [id2],
            "fit_mode": "pad_blur",
            "publish_now": True,
            "created_by": "test-agent",
        },
    )
    assert story_resp.status_code == 200
    story = story_resp.json()
    assert story["status"] == "published"
    assert story["external_id"].startswith("dryrun_story_")
    receipt = tmp_path / "data" / "published_dry_run" / story["id"] / "receipt.json"
    assert receipt.exists()
    assert json.loads(receipt.read_text(encoding="utf-8"))["pub_type"] == "story"

    # Schedule a carousel in the past and run due scheduler
    past_iso = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    car_resp = client.post(
        "/api/publications",
        json={
            "pub_type": "carousel",
            "image_ids": [id1, id2],
            "caption": "Carrusel automático",
            "hashtags": ["#test", "#ig"],
            "scheduled_at": past_iso,
            "publish_now": False,
        },
    )
    assert car_resp.status_code == 200
    car = car_resp.json()
    assert car["status"] == "scheduled"
    assert len(car["prepared_files"]) == 2

    due_resp = client.post("/api/scheduler/run-due")
    assert due_resp.status_code == 200
    due = due_resp.json()
    assert len(due) == 1
    assert due[0]["id"] == car["id"]
    assert due[0]["status"] == "published"

    # Verify stats reflect published story + carousel
    stats = client.get("/api/stats").json()
    assert stats["stories_published"] == 1
    assert stats["posts_published"] == 1

    # Verify image usage counters incremented
    img2 = client.get(f"/api/pool/{id2}").json()
    assert img2["times_used_story"] == 1
    assert img2["times_used_post"] == 1

    # Adapter settings update & masking
    upd = client.patch(
        "/api/settings",
        json={
            "default_adapter": "dry_run",
            "graph_api": {
                "api_host": "graph.instagram.com",
                "ig_user_id": "",
                "public_base_url": "https://example.trycloudflare.com",
                "access_token": "IGAA_SUPER_SECRET_TOKEN_123456",
            },
        },
    )
    assert upd.status_code == 200
    settings = upd.json()
    assert settings["graph_api"]["has_access_token"] is True
    assert "access_token" not in settings["graph_api"]

    # Create draft, edit it (swap image, change caption/hashtags/fit_mode, turn into carousel), then save
    draft_resp = client.post(
        "/api/publications",
        json={
            "pub_type": "post",
            "image_ids": [id1],
            "caption": "Borrador inicial",
            "hashtags": ["#v1"],
            "publish_now": False,
        },
    )
    assert draft_resp.status_code == 200
    draft = draft_resp.json()
    assert draft["status"] == "draft"

    edit_resp = client.patch(
        f"/api/publications/{draft['id']}",
        json={
            "pub_type": "carousel",
            "image_ids": [id2, id1],
            "caption": "Borrador editado",
            "hashtags": ["#v2", "#editado"],
            "fit_mode": "pad_blur",
        },
    )
    assert edit_resp.status_code == 200
    edited = edit_resp.json()
    assert edited["pub_type"] == "carousel"
    assert edited["image_ids"] == [id2, id1]
    assert edited["caption"] == "Borrador editado"
    assert "#v2" in edited["hashtags"]
    assert len(edited["prepared_files"]) == 2
    assert edited["status"] == "draft"
