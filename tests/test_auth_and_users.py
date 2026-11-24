from __future__ import annotations

import json
import time
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image
from typer.testing import CliRunner

from insta_poster.api import create_app
from insta_poster.auth import sign_media_filename
from insta_poster.cli import app as cli_app


def _png_bytes(size: tuple[int, int] = (800, 800)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", size, (100, 140, 220)).save(buf, format="PNG")
    return buf.getvalue()


def test_auth_lockout_setup_login_logout_and_dokploy_https_cookie(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path / "data", enable_scheduler=False)
    client = TestClient(app)

    # Public health & auth status
    assert client.get("/api/health").status_code == 200
    status = client.get("/api/auth/status").json()
    assert status == {
        "authenticated": False,
        "needs_setup": True,
        "user": None,
        "auth_method": None,
    }

    # All protected endpoints & docs are locked
    for path in ("/api/stats", "/api/pool", "/api/publications", "/api/settings", "/api/users", "/docs", "/openapi.json"):
        assert client.get(path).status_code == 401, f"{path} should require auth"

    # Initial setup creates first user and authenticates via HttpOnly cookie
    setup = client.post("/api/auth/setup", json={"username": "Admin", "password": "password123"})
    assert setup.status_code == 200
    body = setup.json()
    assert body["user"]["username"] == "admin"
    assert body["token"]
    assert "insta_poster_session" in client.cookies

    # Protected routes and Swagger docs now work via cookie
    assert client.get("/api/stats").status_code == 200
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200

    # Setup is permanently closed once a user exists
    second_setup = TestClient(app).post("/api/auth/setup", json={"username": "hacker", "password": "password123"})
    assert second_setup.status_code == 403

    # Logout invalidates session token
    old_token = body["token"]
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/stats").status_code == 401
    assert TestClient(app).get("/api/stats", headers={"Authorization": f"Bearer {old_token}"}).status_code == 401

    # Login behind reverse proxy (Traefik/Dokploy) sets Secure + HttpOnly cookie
    https_client = TestClient(app)
    login = https_client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "password123"},
        headers={"X-Forwarded-Proto": "https"},
    )
    assert login.status_code == 200
    set_cookie = login.headers.get("set-cookie", "").lower()
    assert "httponly" in set_cookie
    assert "secure" in set_cookie
    assert "samesite=lax" in set_cookie

    # Bearer token works for API clients/agents
    bearer_token = login.json()["token"]
    api_client = TestClient(app)
    assert api_client.get("/api/stats", headers={"Authorization": f"Bearer {bearer_token}"}).status_code == 200


def test_multi_user_management_password_change_and_guards(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path / "data", enable_scheduler=False)
    client_a = TestClient(app)

    setup = client_a.post("/api/auth/setup", json={"username": "alice", "password": "alice-password"})
    assert setup.status_code == 200
    alice_id = setup.json()["user"]["id"]

    # Cannot delete yourself / last remaining user
    assert client_a.delete(f"/api/users/{alice_id}").status_code == 400

    # Alice creates Bob
    bob_resp = client_a.post("/api/users", json={"username": "bob", "password": "bob-password"})
    assert bob_resp.status_code == 200
    bob_id = bob_resp.json()["id"]

    # Duplicate username rejected
    assert client_a.post("/api/users", json={"username": "BOB", "password": "another-password"}).status_code == 400

    # Bob logs in independently and has the exact same capabilities (single user type)
    client_b = TestClient(app)
    bob_login = client_b.post("/api/auth/login", json={"username": "bob", "password": "bob-password"})
    assert bob_login.status_code == 200

    # Bob creates Carol
    carol_resp = client_b.post("/api/users", json={"username": "carol", "password": "carol-password"})
    assert carol_resp.status_code == 200
    carol_id = carol_resp.json()["id"]

    users = client_b.get("/api/users").json()
    assert [u["username"] for u in users] == ["alice", "bob", "carol"]

    # Carol logs in, then Bob resets Carol's password -> Carol's existing session is revoked
    client_c = TestClient(app)
    assert client_c.post("/api/auth/login", json={"username": "carol", "password": "carol-password"}).status_code == 200
    assert client_c.get("/api/stats").status_code == 200

    assert client_b.patch(f"/api/users/{carol_id}/password", json={"password": "carol-new-pw"}).status_code == 200
    assert client_c.get("/api/stats").status_code == 401
    assert client_c.post("/api/auth/login", json={"username": "carol", "password": "carol-new-pw"}).status_code == 200

    # Bob changes own password (keeps current session, revokes other sessions)
    client_b2 = TestClient(app)
    assert client_b2.post("/api/auth/login", json={"username": "bob", "password": "bob-password"}).status_code == 200
    assert (
        client_b.post(
            "/api/auth/change-password",
            json={"current_password": "wrong-password", "new_password": "bob-new-password"},
        ).status_code
        == 400
    )
    assert (
        client_b.post(
            "/api/auth/change-password",
            json={"current_password": "bob-password", "new_password": "bob-new-password"},
        ).status_code
        == 200
    )
    assert client_b.get("/api/stats").status_code == 200
    assert client_b2.get("/api/stats").status_code == 401

    # Bob cannot delete himself, but can delete Carol
    assert client_b.delete(f"/api/users/{bob_id}").status_code == 400
    assert client_b.delete(f"/api/users/{carol_id}").status_code == 200
    assert client_c.get("/api/stats").status_code == 401


def test_media_protection_and_signed_prepared_urls(tmp_path: Path) -> None:
    app = create_app(data_dir=tmp_path / "data", enable_scheduler=False)
    authed = TestClient(app)
    anon = TestClient(app)

    assert authed.post("/api/auth/setup", json={"username": "owner", "password": "owner-password"}).status_code == 200

    upload = authed.post(
        "/api/pool/upload",
        files=[("files", ("photo.png", _png_bytes(), "image/png"))],
    )
    assert upload.status_code == 200
    img = upload.json()["added"][0]

    pub = authed.post(
        "/api/publications",
        json={"pub_type": "post", "image_ids": [img["id"]], "caption": "Hola", "publish_now": False},
    ).json()
    prepared_url = pub["prepared_urls"][0]
    prepared_filename = prepared_url.rsplit("/", 1)[-1]

    # Anonymous cannot fetch any media
    assert anon.get(img["image_url"]).status_code == 401
    assert anon.get(img["thumbnail_url"]).status_code == 401
    assert anon.get(prepared_url).status_code == 401

    # Authenticated user can fetch all media via cookie
    assert authed.get(img["image_url"]).status_code == 200
    assert authed.get(img["thumbnail_url"]).status_code == 200
    assert authed.get(prepared_url).status_code == 200

    # Meta Graph API can HEAD and GET /media/prepared/{file} using a valid signed URL
    secret = app.state.service.get_media_signing_secret()
    exp, sig = sign_media_filename(prepared_filename, secret, ttl_seconds=600)
    signed = f"{prepared_url}?exp={exp}&sig={sig}"
    assert anon.head(signed).status_code == 200
    assert anon.get(signed).status_code == 200
    assert anon.get(f"{prepared_url}?exp={exp}&sig=badsignature").status_code == 401
    assert anon.get(f"{prepared_url}?exp={int(time.time()) - 10}&sig={sig}").status_code == 401

    # Path traversal blocked
    assert authed.get("/media/pool/..%2Finsta_poster.db").status_code == 404


def test_env_bootstrap_and_api_key_and_rate_limit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("INSTA_POSTER_INIT_USER", "dokployadmin")
    monkeypatch.setenv("INSTA_POSTER_INIT_PASSWORD", "initial-pass-123")
    monkeypatch.setenv("INSTA_POSTER_API_KEY", "super-agent-key-999")

    data_dir = tmp_path / "data"
    app = create_app(data_dir=data_dir, enable_scheduler=False)
    client = TestClient(app)

    status = client.get("/api/auth/status").json()
    assert status["needs_setup"] is False
    assert status["authenticated"] is False

    # API key works for agents
    assert client.get("/api/stats", headers={"X-API-Key": "wrong-key"}).status_code == 401
    assert client.get("/api/stats", headers={"X-API-Key": "super-agent-key-999"}).status_code == 200

    # Bootstrapped user can log in and change password
    assert client.post("/api/auth/login", json={"username": "dokployadmin", "password": "initial-pass-123"}).status_code == 200
    assert (
        client.post(
            "/api/auth/change-password",
            json={"current_password": "initial-pass-123", "new_password": "rotated-pass-456"},
        ).status_code
        == 200
    )

    # Restarting app with the same env vars does NOT overwrite the rotated password
    app2 = create_app(data_dir=data_dir, enable_scheduler=False)
    client2 = TestClient(app2)
    assert client2.post("/api/auth/login", json={"username": "dokployadmin", "password": "initial-pass-123"}).status_code == 401
    assert client2.post("/api/auth/login", json={"username": "dokployadmin", "password": "rotated-pass-456"}).status_code == 200

    # Rate limit triggers after repeated failures
    rl_client = TestClient(app2)
    for _ in range(10):
        resp = rl_client.post("/api/auth/login", json={"username": "dokployadmin", "password": "wrong"})
        assert resp.status_code == 401
    blocked = rl_client.post("/api/auth/login", json={"username": "dokployadmin", "password": "wrong"})
    assert blocked.status_code == 429
    assert "retry-after" in blocked.headers


def test_cli_user_management(tmp_path: Path) -> None:
    runner = CliRunner()
    data_dir = tmp_path / "cli_auth_data"

    r1 = runner.invoke(cli_app, ["user", "create", "juan", "--password", "secret123", "--json", "--data-dir", str(data_dir)])
    assert r1.exit_code == 0, r1.output
    assert json.loads(r1.output)["username"] == "juan"

    r2 = runner.invoke(cli_app, ["user", "create", "maria", "--password", "secret456", "--json", "--data-dir", str(data_dir)])
    assert r2.exit_code == 0, r2.output

    r_list = runner.invoke(cli_app, ["user", "list", "--json", "--data-dir", str(data_dir)])
    assert r_list.exit_code == 0
    assert [u["username"] for u in json.loads(r_list.output)] == ["juan", "maria"]

    r_pw = runner.invoke(cli_app, ["user", "passwd", "maria", "--password", "newsecret789", "--json", "--data-dir", str(data_dir)])
    assert r_pw.exit_code == 0, r_pw.output

    r_del = runner.invoke(cli_app, ["user", "delete", "maria", "--json", "--data-dir", str(data_dir)])
    assert r_del.exit_code == 0, r_del.output

    # Deleting the last remaining user fails without --force
    r_del_last = runner.invoke(cli_app, ["user", "delete", "juan", "--json", "--data-dir", str(data_dir)])
    assert r_del_last.exit_code == 1

    r_del_force = runner.invoke(cli_app, ["user", "delete", "juan", "--force", "--json", "--data-dir", str(data_dir)])
    assert r_del_force.exit_code == 0, r_del_force.output
