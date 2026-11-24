from __future__ import annotations

import json
from pathlib import Path

from PIL import Image
from typer.testing import CliRunner

from insta_poster.cli import app
from insta_poster.sdk import InstaPoster

runner = CliRunner()


def _create_sample(path: Path, size: tuple[int, int] = (1080, 1080)) -> Path:
    Image.new("RGB", size, (120, 40, 180)).save(path, format="JPEG")
    return path


def test_cli_json_workflow_for_agents(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    img_path = _create_sample(tmp_path / "sample.jpg", (1080, 1350))

    r_add = runner.invoke(
        app,
        ["pool", "add", str(img_path), "--tags", "drop,otoño", "--notes", "Producto estrella", "--json", "--data-dir", str(data_dir)],
    )
    assert r_add.exit_code == 0, r_add.output
    added = json.loads(r_add.output)
    assert added["created"] is True
    img_id = added["image"]["id"]

    r_pick = runner.invoke(app, ["pool", "pick", "--type", "post", "--unused", "--json", "--data-dir", str(data_dir)])
    assert r_pick.exit_code == 0, r_pick.output
    picked = json.loads(r_pick.output)
    assert picked[0]["id"] == img_id

    r_post = runner.invoke(
        app,
        [
            "post", "create",
            "--image", img_id,
            "--caption", "Lanzamiento desde CLI",
            "--hashtags", "#drop,#cli",
            "--now",
            "--json",
            "--data-dir", str(data_dir),
        ],
    )
    assert r_post.exit_code == 0, r_post.output
    pub = json.loads(r_post.output)
    assert pub["status"] == "published"
    assert "#drop" in pub["full_caption"]

    r_story = runner.invoke(
        app,
        ["story", "create", "--image", img_id, "--fit", "pad_blur", "--now", "--json", "--data-dir", str(data_dir)],
    )
    assert r_story.exit_code == 0, r_story.output
    story = json.loads(r_story.output)
    assert story["pub_type"] == "story"
    assert story["status"] == "published"

    # Draft + CLI queue edit
    r_draft = runner.invoke(
        app,
        ["post", "create", "--image", img_id, "--caption", "Original", "--json", "--data-dir", str(data_dir)],
    )
    assert r_draft.exit_code == 0, r_draft.output
    draft_id = json.loads(r_draft.output)["id"]

    r_edit = runner.invoke(
        app,
        ["queue", "edit", draft_id, "--caption", "Editado por CLI", "--hashtags", "#edit", "--json", "--data-dir", str(data_dir)],
    )
    assert r_edit.exit_code == 0, r_edit.output
    edited = json.loads(r_edit.output)
    assert edited["caption"] == "Editado por CLI"
    assert "#edit" in edited["hashtags"]


def test_python_sdk_local_code_mode(tmp_path: Path) -> None:
    data_dir = tmp_path / "sdk_data"
    folder = tmp_path / "pool_src"
    folder.mkdir()
    _create_sample(folder / "a.jpg", (1080, 1920))
    _create_sample(folder / "b.jpg", (1080, 1080))

    ip = InstaPoster.local(data_dir=data_dir)
    sync_res = ip.sync_folder(folder, tags=["sdk"])
    assert len(sync_res["added"]) == 2

    picked = ip.pick_images(pub_type="story", unused_only=True)
    story = ip.create_story(picked[0]["id"], publish_now=True)
    assert story["status"] == "published"

    stats = ip.stats()
    assert stats["stories_published"] == 1
    assert stats["pool_total"] == 2
