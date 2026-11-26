from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from typer.testing import CliRunner

from insta_poster.adapters.graph_api import GraphAPIAdapter
from insta_poster.api import create_app
from insta_poster.cli import app as cli_app
from insta_poster.sdk import InstaPoster
from insta_poster.service import InstaPosterService

runner = CliRunner()


def _png_bytes(size: tuple[int, int] = (1080, 1080), color: tuple[int, int, int] = (40, 120, 200)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_graph_api_adapter_insights_parsing_and_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = dict(request.url.params)
        if path.endswith("/17841407084835535") and "followers_count" in params.get("fields", ""):
            return httpx.Response(
                200,
                json={
                    "id": "17841407084835535",
                    "username": "entreamigoschalet",
                    "name": "Entre Amigos",
                    "followers_count": 167,
                    "follows_count": 29,
                    "media_count": 2,
                },
            )
        if path.endswith("/17841407084835535/insights"):
            if params.get("metric_type") == "total_value":
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {"name": "views", "total_value": {"value": 485}},
                            {"name": "accounts_engaged", "total_value": {"value": 12}},
                            {"name": "total_interactions", "total_value": {"value": 19}},
                            {"name": "profile_views", "total_value": {"value": 7}},
                            {"name": "likes", "total_value": {"value": 5}},
                            {"name": "shares", "total_value": {"value": 1}},
                        ]
                    },
                )
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "name": "reach",
                            "values": [
                                {"value": 62, "end_time": "2026-11-25T08:00:00+0000"},
                                {"value": 28, "end_time": "2026-11-26T08:00:00+0000"},
                            ],
                        },
                        {
                            "name": "follower_count",
                            "values": [
                                {"value": 2, "end_time": "2026-11-25T08:00:00+0000"},
                                {"value": 1, "end_time": "2026-11-26T08:00:00+0000"},
                            ],
                        },
                    ]
                },
            )
        if path.endswith("/story_live_1/insights"):
            if params.get("breakdown") == "story_navigation_action_type":
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "name": "navigation",
                                "values": [{"value": 39}],
                                "total_value": {
                                    "value": 39,
                                    "breakdowns": [
                                        {
                                            "dimension_keys": ["story_navigation_action_type"],
                                            "results": [
                                                {"dimension_values": ["tap_forward"], "value": 24},
                                                {"dimension_values": ["tap_exit"], "value": 11},
                                                {"dimension_values": ["swipe_forward"], "value": 4},
                                            ],
                                        }
                                    ],
                                },
                            }
                        ]
                    },
                )
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"name": "views", "values": [{"value": 46}]},
                        {"name": "reach", "values": [{"value": 37}]},
                        {"name": "replies", "values": [{"value": 2}]},
                        {"name": "shares", "values": [{"value": 1}]},
                        {"name": "total_interactions", "values": [{"value": 3}]},
                        {"name": "navigation", "values": [{"value": 39}]},
                        {"name": "profile_visits", "values": [{"value": 1}]},
                    ]
                },
            )
        if path.endswith("/story_expired_1/insights"):
            return httpx.Response(
                400,
                json={"error": {"message": "Object does not exist", "code": 100, "error_subcode": 33}},
            )
        if path.endswith("/reel_partial_1/insights"):
            metric = params.get("metric", "")
            if "," in metric:
                return httpx.Response(400, json={"error": {"message": "Unsupported metric in batch", "code": 100}})
            if metric == "views":
                return httpx.Response(200, json={"data": [{"name": "views", "values": [{"value": 320}]}]})
            if metric == "reach":
                return httpx.Response(200, json={"data": [{"name": "reach", "values": [{"value": 210}]}]})
            return httpx.Response(400, json={"error": {"message": "Unsupported metric", "code": 100}})
        return httpx.Response(404, json={"error": {"message": f"Unexpected URL {path}", "code": 404}})

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def patched_client(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", patched_client)

    adapter = GraphAPIAdapter(
        {
            "access_token": "EAB_TEST_TOKEN",
            "ig_user_id": "17841407084835535",
            "api_host": "graph.facebook.com",
            "api_version": "v21.0",
        }
    )
    assert adapter.can_fetch_insights() is True

    acc = adapter.fetch_account_insights(days=7)
    assert acc["username"] == "entreamigoschalet"
    assert acc["followers_count"] == 167
    assert acc["totals"]["views"] == 485
    assert acc["totals"]["reach"] == 90
    assert acc["totals"]["follower_delta"] == 3
    assert len(acc["daily_series"]) == 2
    assert acc["error_message"] is None

    story = adapter.fetch_media_insights("story_live_1", media_product_type="STORY")
    assert story["expired"] is False
    assert story["metrics"]["views"] == 46
    assert story["metrics"]["reach"] == 37
    assert story["metrics"]["navigation"] == 39
    assert story["navigation_breakdown"] == {"tap_forward": 24, "tap_exit": 11, "swipe_forward": 4}

    expired = adapter.fetch_media_insights("story_expired_1", media_product_type="STORY")
    assert expired["expired"] is True
    assert expired["error_message"] is not None

    reel = adapter.fetch_media_insights("reel_partial_1", media_product_type="REELS")
    assert reel["metrics"]["views"] == 320
    assert reel["metrics"]["reach"] == 210


def test_insights_sync_snapshots_story_expiration_api_cli_and_sdk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    app = create_app(data_dir=data_dir, enable_scheduler=False)
    client = TestClient(app)
    service: InstaPosterService = app.state.service

    # Setup auth & configure graph_api
    assert client.post("/api/auth/setup", json={"username": "admin", "password": "secret-password"}).status_code == 200
    assert (
        client.patch(
            "/api/settings",
            json={
                "graph_api": {
                    "api_host": "graph.facebook.com",
                    "ig_user_id": "17841407084835535",
                    "access_token": "EAB_FAKE_TOKEN_FOR_TESTS",
                }
            },
        ).status_code
        == 200
    )

    # Upload an image & create a local published graph_api carousel record
    up = client.post(
        "/api/pool/upload",
        files=[("files", ("photo.png", _png_bytes(), "image/png"))],
    )
    img_id = up.json()["added"][0]["id"]
    pub = client.post(
        "/api/publications",
        json={"pub_type": "post", "image_ids": [img_id], "caption": "Post de prueba", "publish_now": False},
    ).json()

    # Simulate that this publication was published via graph_api with external_id="18101164529122164"
    with service.db.connection() as conn:
        conn.execute(
            """
            UPDATE publications
            SET status = 'published', adapter = 'graph_api',
                external_id = '18101164529122164',
                external_url = 'https://www.instagram.com/p/DgL5IENnbbd/',
                published_at = '2026-11-26T01:10:12+00:00'
            WHERE id = ?
            """,
            (pub["id"],),
        )

    phase = {"step": 1}

    def fake_account_insights(self: GraphAPIAdapter, days: int = 7) -> dict[str, Any]:
        return {
            "period_days": days,
            "ig_user_id": "17841407084835535",
            "username": "entreamigoschalet",
            "name": "Entre Amigos",
            "followers_count": 167 + phase["step"],
            "follows_count": 29,
            "media_count": 2,
            "totals": {"views": 485, "reach": 212, "total_interactions": 19, "accounts_engaged": 12, "profile_views": 7},
            "daily_series": [
                {"date": "2026-11-25", "reach": 62, "follower_count": 1},
                {"date": "2026-11-26", "reach": 28, "follower_count": 0},
            ],
            "error_message": None,
        }

    def fake_active_stories(self: GraphAPIAdapter) -> list[dict[str, Any]]:
        if phase["step"] >= 3:
            return []  # Story expired after 24h!
        return [
            {
                "id": "17890204476501666",
                "media_type": "VIDEO",
                "media_product_type": "STORY",
                "permalink": "https://www.instagram.com/stories/entreamigoschalet/4038576990749484852",
                "timestamp": "2026-11-26T01:18:35+0000",
            }
        ]

    def fake_account_media(self: GraphAPIAdapter, limit: int = 50) -> list[dict[str, Any]]:
        return [
            {
                "id": "18101164529122164",
                "caption": "Post de prueba",
                "media_type": "CAROUSEL_ALBUM",
                "media_product_type": "FEED",
                "like_count": 4 + phase["step"],
                "comments_count": 1,
                "permalink": "https://www.instagram.com/p/DgL5IENnbbd/",
                "timestamp": "2026-11-26T01:10:12+0000",
            }
        ]

    def fake_media_insights(
        self: GraphAPIAdapter,
        external_id: str,
        *,
        media_product_type: str = "FEED",
        media_type: str = "IMAGE",
        fallback_likes: int | None = None,
        fallback_comments: int | None = None,
        include_metadata: bool = False,
    ) -> dict[str, Any]:
        if external_id == "17890204476501666":
            if phase["step"] >= 3:
                return {
                    "metrics": {},
                    "extra_metrics": {},
                    "navigation_breakdown": {},
                    "metadata": {},
                    "expired": True,
                    "error_message": "Historia expirada (>24h)",
                }
            views = 46 if phase["step"] == 1 else 68
            reach = 37 if phase["step"] == 1 else 52
            return {
                "metrics": {
                    "views": views,
                    "reach": reach,
                    "replies": 1,
                    "shares": 0,
                    "total_interactions": 1,
                    "navigation": 39,
                    "follows": 0,
                    "profile_visits": 2,
                    "profile_activity": 2,
                },
                "extra_metrics": {},
                "navigation_breakdown": {"tap_forward": 24, "tap_exit": 11, "swipe_forward": 4},
                "metadata": {},
                "expired": False,
                "error_message": None,
            }
        return {
            "metrics": {
                "views": 138 * phase["step"],
                "reach": 57 * phase["step"],
                "saved": 3,
                "likes": 4 + phase["step"],
                "comments": 1,
                "shares": 2,
                "total_interactions": 10 + phase["step"],
                "follows": 1,
                "profile_visits": 4,
                "profile_activity": 5,
            },
            "extra_metrics": {},
            "navigation_breakdown": {},
            "metadata": {},
            "expired": False,
            "error_message": None,
        }

    monkeypatch.setattr(GraphAPIAdapter, "fetch_account_insights", fake_account_insights)
    monkeypatch.setattr(GraphAPIAdapter, "fetch_active_stories", fake_active_stories)
    monkeypatch.setattr(GraphAPIAdapter, "fetch_account_media", fake_account_media)
    monkeypatch.setattr(GraphAPIAdapter, "fetch_media_insights", fake_media_insights)

    # Sync #1: both feed post and live story synced
    r1 = client.post("/api/insights/sync?days=7")
    assert r1.status_code == 200, r1.text
    s1 = r1.json()
    assert s1["synced_media_count"] == 1
    assert s1["synced_stories_count"] == 1
    assert s1["account"]["username"] == "entreamigoschalet"

    # GET /api/insights returns account + 2 items + summary
    ov = client.get("/api/insights?days=7&sort_by=views").json()
    assert ov["adapter_configured"] is True
    assert ov["summary"]["total_items"] == 2
    assert ov["summary"]["active_stories_count"] == 1
    assert ov["items"][0]["external_id"] == "18101164529122164"
    assert ov["items"][0]["publication_id"] == pub["id"]
    assert ov["items"][0]["thumbnail_url"].startswith("/media/prepared/")
    assert ov["items"][1]["external_id"] == "17890204476501666"
    assert ov["items"][1]["navigation_breakdown"]["tap_forward"] == 24

    # Published publication in /api/publications now includes inline insights summary
    pub_detail = client.get(f"/api/publications/{pub['id']}").json()
    assert pub_detail["insights"] is not None
    assert pub_detail["insights"]["views"] == 138
    assert pub_detail["insights"]["reach"] == 57
    assert pub_detail["insights"]["engagement_rate"] == round((11 / 57) * 100, 2)

    # Sync #2: metrics grew -> second snapshot recorded
    phase["step"] = 2
    assert client.post("/api/insights/sync?days=7").status_code == 200
    hist = client.get("/api/insights/media/17890204476501666/history").json()
    assert len(hist) == 2
    assert hist[0]["views"] == 68
    assert hist[1]["views"] == 46

    # Sync #3: story expired (>24h) -> marked is_active=False, previous metrics PRESERVED!
    phase["step"] = 3
    assert client.post("/api/insights/sync?days=7").status_code == 200
    expired_story = client.get("/api/insights/media/17890204476501666").json()
    assert expired_story["is_active"] is False
    assert expired_story["views"] == 68
    assert expired_story["reach"] == 52
    assert expired_story["navigation_breakdown"]["tap_exit"] == 11

    # Single-item refresh on expired story also preserves previous metrics
    r_single = client.post("/api/insights/media/17890204476501666/sync")
    assert r_single.status_code == 200
    assert r_single.json()["views"] == 68
    assert r_single.json()["error_message"] is not None

    # Periodic scheduler sync respects interval: skips immediately after sync, runs when overdue
    assert service.run_periodic_insights_sync() is None
    old_ts = (datetime.now(timezone.utc) - timedelta(minutes=45)).replace(microsecond=0).isoformat()
    service.db.set_setting("insights_last_sync_at", old_ts)
    auto_res = service.run_periodic_insights_sync()
    assert auto_res is not None
    assert auto_res.synced_media_count == 1

    # CLI overview & show --history --json
    cli_ov = runner.invoke(cli_app, ["insights", "overview", "--json", "--data-dir", str(data_dir)])
    assert cli_ov.exit_code == 0, cli_ov.output
    assert json.loads(cli_ov.output)["summary"]["total_items"] == 2

    cli_show = runner.invoke(
        cli_app,
        ["insights", "show", pub["id"], "--history", "--json", "--data-dir", str(data_dir)],
    )
    assert cli_show.exit_code == 0, cli_show.output
    show_data = json.loads(cli_show.output)
    assert show_data["external_id"] == "18101164529122164"
    assert len(show_data["history"]) >= 2

    # SDK local mode
    sdk = InstaPoster.local(data_dir=data_dir)
    sdk_ov = sdk.get_insights(days=7, media_product_type="STORY")
    assert len(sdk_ov["items"]) == 1
    assert sdk_ov["items"][0]["views"] == 68
    assert len(sdk.get_media_insight_history("17890204476501666")) == 2


def test_missing_pool_file_returns_404_and_reupload_self_heals(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    app = create_app(data_dir=data_dir, enable_scheduler=False)
    client = TestClient(app)
    assert client.post("/api/auth/setup", json={"username": "admin", "password": "secret-password"}).status_code == 200

    raw = _png_bytes((1000, 1000), (15, 190, 95))
    up = client.post("/api/pool/upload", files=[("files", ("heal.png", raw, "image/png"))])
    img = up.json()["added"][0]

    # Simulate missing file on disk
    Path(img["filepath"]).unlink()

    # Creating a publication should return 404 (not unhandled 500)
    resp = client.post(
        "/api/publications",
        json={"pub_type": "post", "image_ids": [img["id"]], "caption": "Test"},
    )
    assert resp.status_code == 404

    # Re-uploading the exact same image self-heals the missing file on disk
    reup = client.post("/api/pool/upload", files=[("files", ("heal.png", raw, "image/png"))])
    assert reup.status_code == 200
    assert Path(img["filepath"]).exists()

    # Now publication creation succeeds
    ok = client.post(
        "/api/publications",
        json={"pub_type": "post", "image_ids": [img["id"]], "caption": "Recovered"},
    )
    assert ok.status_code == 200
