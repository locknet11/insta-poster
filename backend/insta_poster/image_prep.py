from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from PIL import Image, ImageFilter, ImageOps

# Instagram recommended dimensions
FEED_MAX_WIDTH = 1080
FEED_PORTRAIT_SIZE = (1080, 1350)  # 4:5
FEED_SQUARE_SIZE = (1080, 1080)    # 1:1
FEED_LANDSCAPE_SIZE = (1080, 608)  # ~1.78:1 (within 1.91:1 max)
STORY_SIZE = (1080, 1920)          # 9:16
THUMBNAIL_SIZE = (480, 480)

MIN_FEED_RATIO = 0.80   # 4:5
MAX_FEED_RATIO = 1.91   # 1.91:1


def compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_suitability(width: int, height: int) -> list[str]:
    ratio = width / max(height, 1)
    # Every image can be rendered for both via pad_blur/crop, but we tag natural suitability
    suitable: list[str] = []
    if 0.7 <= ratio <= 2.0:
        suitable.append("post")
    if ratio <= 0.85:
        suitable.append("story")
    if not suitable:
        suitable = ["post", "story"]
    return suitable


def inspect_image(path: Path) -> dict:
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        width, height = img.size
        fmt = (img.format or "").lower()
    mime, _ = mimetypes.guess_type(str(path))
    if not mime:
        mime = f"image/{fmt}" if fmt else "image/jpeg"
    ratio = round(width / max(height, 1), 4)
    return {
        "width": width,
        "height": height,
        "aspect_ratio": ratio,
        "file_size": path.stat().st_size,
        "mime_type": mime,
        "suitable_for": detect_suitability(width, height),
    }


def generate_thumbnail(src: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img.thumbnail(THUMBNAIL_SIZE, Image.Resampling.LANCZOS)
        img.save(dst, format="JPEG", quality=84, optimize=True)
    return dst


def _center_crop_to_size(img: Image.Image, target_size: tuple[int, int]) -> Image.Image:
    return ImageOps.fit(img, target_size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))


def _pad_blur_to_size(img: Image.Image, target_size: tuple[int, int]) -> Image.Image:
    tw, th = target_size
    bg = ImageOps.fit(img, target_size, method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    bg = bg.filter(ImageFilter.GaussianBlur(radius=36))
    # Slightly darken blurred backdrop for clean framing
    dark_overlay = Image.new("RGB", target_size, (12, 12, 16))
    bg = Image.blend(bg, dark_overlay, alpha=0.35)

    fg = img.copy()
    fg.thumbnail((tw, th), Image.Resampling.LANCZOS)
    x = (tw - fg.width) // 2
    y = (th - fg.height) // 2
    bg.paste(fg, (x, y))
    return bg


def _pad_solid_to_size(
    img: Image.Image,
    target_size: tuple[int, int],
    color: tuple[int, int, int] = (14, 14, 18),
) -> Image.Image:
    tw, th = target_size
    canvas = Image.new("RGB", target_size, color)
    fg = img.copy()
    fg.thumbnail((tw, th), Image.Resampling.LANCZOS)
    x = (tw - fg.width) // 2
    y = (th - fg.height) // 2
    canvas.paste(fg, (x, y))
    return canvas


def prepare_for_instagram(
    src: Path,
    dst: Path,
    pub_type: str,
    fit_mode: str = "auto",
) -> Path:
    """Render an Instagram-compliant RGB JPEG for feed post, carousel, or 9:16 story."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(src) as raw:
        img = ImageOps.exif_transpose(raw).convert("RGB")
        w, h = img.size
        ratio = w / max(h, 1)

        if pub_type == "story":
            target = STORY_SIZE
            effective_mode = "pad_blur" if fit_mode == "auto" else fit_mode
            if abs(ratio - (9 / 16)) < 0.03:
                rendered = img.resize(target, Image.Resampling.LANCZOS)
            elif effective_mode == "crop":
                rendered = _center_crop_to_size(img, target)
            elif effective_mode == "pad_solid":
                rendered = _pad_solid_to_size(img, target)
            else:
                rendered = _pad_blur_to_size(img, target)
        else:
            # Feed post or carousel
            if fit_mode == "auto" and MIN_FEED_RATIO <= ratio <= MAX_FEED_RATIO:
                # Already valid aspect ratio for IG feed; scale down if wider than 1080px
                if w > FEED_MAX_WIDTH:
                    new_h = int(round(h * (FEED_MAX_WIDTH / w)))
                    rendered = img.resize((FEED_MAX_WIDTH, new_h), Image.Resampling.LANCZOS)
                else:
                    rendered = img
            else:
                if ratio < 0.9:
                    target = FEED_PORTRAIT_SIZE
                elif ratio > 1.15:
                    target = FEED_LANDSCAPE_SIZE
                else:
                    target = FEED_SQUARE_SIZE

                effective_mode = "crop" if fit_mode == "auto" else fit_mode
                if effective_mode == "pad_blur":
                    rendered = _pad_blur_to_size(img, target)
                elif effective_mode == "pad_solid":
                    rendered = _pad_solid_to_size(img, target)
                else:
                    rendered = _center_crop_to_size(img, target)

        rendered.save(dst, format="JPEG", quality=92, optimize=True)
    return dst
