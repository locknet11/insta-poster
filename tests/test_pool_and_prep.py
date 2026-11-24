from __future__ import annotations

from pathlib import Path
from PIL import Image

from insta_poster.config import AppPaths
from insta_poster.image_prep import prepare_for_instagram
from insta_poster.models import PoolImageUpdate
from insta_poster.service import InstaPosterService


def _make_image(path: Path, size: tuple[int, int], color: tuple[int, int, int]) -> Path:
    img = Image.new("RGB", size, color)
    img.save(path, format="PNG")
    return path


def test_pool_sync_dedup_and_pick(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    src_dir = tmp_path / "photos"
    src_dir.mkdir()

    _make_image(src_dir / "square.png", (1200, 1200), (200, 80, 90))
    _make_image(src_dir / "vertical.png", (1080, 1920), (40, 120, 210))
    _make_image(src_dir / "wide.png", (2000, 900), (30, 180, 100))

    svc = InstaPosterService(AppPaths.from_data_dir(data_dir))
    res1 = svc.sync_folder(str(src_dir), tags=["Verano", "producto"])
    assert len(res1.added) == 3
    assert res1.skipped_duplicates == 0
    assert res1.errors == []

    # Re-sync must deduplicate by SHA-256
    res2 = svc.sync_folder(str(src_dir), tags=["verano"])
    assert len(res2.added) == 0
    assert res2.skipped_duplicates == 3

    # Pick prefers vertical image for stories
    picked_story = svc.pick_images(pub_type="story", count=1, tag="verano", randomize=False)
    assert len(picked_story) == 1
    assert picked_story[0].original_filename == "vertical.png"

    # Update metadata
    updated = svc.update_image(
        picked_story[0].id,
        PoolImageUpdate(notes="Foto vertical ideal para historia", tags=["story-ready", "verano"]),
    )
    assert "story-ready" in updated.tags
    assert "vertical" in updated.notes


def test_image_prep_renders_valid_dimensions(tmp_path: Path) -> None:
    wide = _make_image(tmp_path / "wide.png", (2400, 1000), (10, 100, 200))

    story_blur = prepare_for_instagram(wide, tmp_path / "story_blur.jpg", pub_type="story", fit_mode="pad_blur")
    with Image.open(story_blur) as im:
        assert im.size == (1080, 1920)
        assert im.mode == "RGB"

    story_crop = prepare_for_instagram(wide, tmp_path / "story_crop.jpg", pub_type="story", fit_mode="crop")
    with Image.open(story_crop) as im:
        assert im.size == (1080, 1920)

    feed_post = prepare_for_instagram(wide, tmp_path / "feed.jpg", pub_type="post", fit_mode="auto")
    with Image.open(feed_post) as im:
        w, h = im.size
        assert w <= 1080
        assert 0.80 <= (w / h) <= 1.91
