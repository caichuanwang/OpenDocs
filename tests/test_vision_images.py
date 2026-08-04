from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import pytest  # pyright: ignore[reportMissingImports]
from PIL import Image, ImageDraw  # pyright: ignore[reportMissingImports]

from opendocs._models import BBox
from opendocs.errors import CorruptDocumentError
from opendocs.vision.base import VisionResult, VisionTextElement
from opendocs.vision.images import (
    map_result_to_bbox,
    merge_tiled_results,
    prepare_image,
    prepared_paths,
)


def _arrow(path: Path) -> None:
    image = Image.new("RGBA", (120, 120), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    color = (47, 79, 79, 255)
    draw.ellipse((7, 7, 31, 31), fill=color)
    draw.line((24, 19, 59, 19, 59, 96, 102, 96), fill=color, width=7, joint="curve")
    draw.line((96, 84, 108, 96, 96, 108), fill=color, width=7, joint="curve")
    image.save(path, "PNG")


def test_transparent_sparse_arrow_is_skipped_only_when_embedded(tmp_path: Path) -> None:
    path = tmp_path / "arrow.png"
    _arrow(path)

    embedded = prepare_image(path, tmp_path, "embedded", None, "embedded", 0.01)
    standalone = prepare_image(path, tmp_path, "standalone", None, "standalone")

    assert embedded["skipped"] is True
    assert embedded["reason"] == "decorative_icon"
    facts = embedded["facts"]
    assert facts["alpha_coverage"] < 0.2
    assert standalone["skipped"] is False
    assert prepared_paths(standalone, tmp_path)


def test_opaque_sparse_icon_is_skipped_only_when_embedded(tmp_path: Path) -> None:
    path = tmp_path / "opaque-icon.jpg"
    image = Image.new("RGB", (120, 120), "white")
    draw = ImageDraw.Draw(image)
    draw.line((15, 60, 95, 60), fill="black", width=6)
    draw.polygon(((95, 45), (112, 60), (95, 75)), fill="black")
    image.save(path, "JPEG")

    embedded = prepare_image(path, tmp_path, "opaque-embedded", None, "embedded", 0.01)
    standalone = prepare_image(path, tmp_path, "opaque-standalone", None, "standalone")

    assert embedded["skipped"] is True
    assert embedded["facts"]["alpha_coverage"] == 1.0
    assert embedded["facts"]["foreground_coverage"] < 0.2
    assert standalone["skipped"] is False


def test_small_opaque_text_image_is_not_filtered_as_icon(tmp_path: Path) -> None:
    path = tmp_path / "small-text.jpg"
    image = Image.new("RGB", (120, 120), "white")
    ImageDraw.Draw(image).text((20, 50), "AB12", fill="black")
    image.save(path, "JPEG")

    prepared = prepare_image(path, tmp_path, "small-text", None, "embedded", 0.01)

    assert prepared["skipped"] is False
    assert prepared["facts"]["components"] > 2


def test_transparency_is_flattened_onto_white(tmp_path: Path) -> None:
    path = tmp_path / "transparent.png"
    image = Image.new("RGBA", (20, 20), (0, 0, 0, 0))
    image.putpixel((10, 10), (255, 0, 0, 255))
    image.save(path, "PNG")

    prepared = prepare_image(path, tmp_path, "white", None, "standalone")

    with Image.open(prepared_paths(prepared, tmp_path)[0]) as output:
        assert output.mode == "RGB"
        assert output.getpixel((0, 0)) == (255, 255, 255)
        assert output.getpixel((10, 10)) == (255, 0, 0)


def test_white_transparent_content_uses_contrasting_background(tmp_path: Path) -> None:
    path = tmp_path / "white-on-transparent.png"
    image = Image.new("RGBA", (120, 120), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((20, 50, 100, 70), fill="white")
    image.save(path, "PNG")

    embedded = prepare_image(path, tmp_path, "white-content", None, "embedded", 0.2)

    assert embedded["skipped"] is False
    assert embedded["facts"]["alpha_coverage"] > 0.1
    output_path = prepared_paths(embedded, tmp_path)[0]
    with Image.open(output_path) as output:
        assert output.getpixel((0, 0)) == (0, 0, 0)
        assert output.getpixel((60, 60)) == (255, 255, 255)


def test_prepare_image_removes_partially_written_tile_on_save_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "source.png"
    Image.new("RGB", (300, 300), "white").save(path, "PNG")

    def fail_after_write(
        _image: Image.Image,
        destination: str | Path,
        *_args: object,
        **_kwargs: object,
    ) -> None:
        output = Path(destination)
        output.write_bytes(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(Image.Image, "save", fail_after_write)

    with pytest.raises(CorruptDocumentError):
        prepare_image(path, tmp_path, "failed", None, "standalone")

    assert not tuple(tmp_path.glob("failed-*.png"))


def test_prepared_paths_rejects_workspace_escape(tmp_path: Path) -> None:
    prepared = {
        "skipped": False,
        "reason": None,
        "width": 10,
        "height": 10,
        "facts": {},
        "parts": [
            {
                "name": "../outside.png",
                "top": 0.0,
                "bottom": 1.0,
                "core_top": 0.0,
                "core_bottom": 1.0,
                "width": 10,
                "height": 10,
            }
        ],
    }

    with pytest.raises(ValueError, match="basename"):
        prepared_paths(prepared, tmp_path)


def test_small_complex_image_is_not_filtered_as_icon(tmp_path: Path) -> None:
    path = tmp_path / "qr-like.png"
    image = Image.new("RGBA", (120, 120), "white")
    draw = ImageDraw.Draw(image)
    for y in range(0, 120, 12):
        for x in range(0, 120, 12):
            if (x // 12 + y // 12) % 2 == 0:
                draw.rectangle((x, y, x + 7, y + 7), fill="black")
    image.save(path, "PNG")

    prepared = prepare_image(path, tmp_path, "qr", None, "embedded", 0.01)

    assert prepared["skipped"] is False


def test_long_image_tiles_cover_source_and_preserve_readable_width(tmp_path: Path) -> None:
    path = tmp_path / "long.png"
    Image.new("RGB", (1080, 20_000), "white").save(path, "PNG")
    image = Image.open(path)
    draw = ImageDraw.Draw(image)
    for y in range(100, 20_000, 500):
        draw.rectangle((50, y, 1030, y + 20), fill="black")
    image.save(path, "PNG")
    image.close()

    prepared = prepare_image(path, tmp_path, "tile", None, "standalone")
    parts = prepared["parts"]

    assert isinstance(parts, list)
    assert len(parts) > 1
    assert parts[0]["top"] == 0.0
    assert parts[-1]["bottom"] == 1.0
    assert all(part["width"] >= 768 for part in parts)
    assert all(left["bottom"] > right["top"] for left, right in pairwise(parts))


def test_tiled_results_map_bboxes_order_and_deduplicate_boundaries(tmp_path: Path) -> None:
    path = tmp_path / "long.png"
    image = Image.new("RGB", (800, 4000), "white")
    ImageDraw.Draw(image).line((0, 100, 799, 100), fill="black", width=3)
    image.save(path, "PNG")
    prepared = prepare_image(path, tmp_path, "merge", None, "standalone")
    parts = prepared["parts"]
    assert isinstance(parts, list) and len(parts) > 1
    results: list[VisionResult | None] = [
        VisionResult((VisionTextElement("first", 0, BBox(0, 0.1, 1, 0.2)),))
    ]
    results.extend(None for _ in parts[1:-1])
    results.append(
        VisionResult(
            (
                VisionTextElement("first", 1, BBox(0, 0.4, 1, 0.45)),
                VisionTextElement("second", 2, BBox(0, 0.6, 1, 0.7)),
            )
        )
    )

    merged = merge_tiled_results(prepared, results)
    mapped = map_result_to_bbox(merged, BBox(0.2, 0.3, 0.8, 0.9))

    assert [
        element.text for element in merged.elements if isinstance(element, VisionTextElement)
    ] == ["first", "first", "second"]
    assert merged.elements[0].bbox is not None
    assert merged.elements[1].bbox is not None
    assert merged.elements[2].bbox is not None
    assert merged.elements[0].bbox.top < merged.elements[1].bbox.top
    assert all(
        element.bbox is not None
        and 0.2 <= element.bbox.left < element.bbox.right <= 0.8
        and 0.3 <= element.bbox.top < element.bbox.bottom <= 0.9
        for element in mapped.elements
    )


def test_tiled_results_only_deduplicate_adjacent_boundary_text(tmp_path: Path) -> None:
    path = tmp_path / "long-boundary.png"
    image = Image.new("RGB", (800, 4000), "white")
    ImageDraw.Draw(image).line((0, 100, 799, 100), fill="black", width=3)
    image.save(path, "PNG")
    prepared = prepare_image(path, tmp_path, "boundary", None, "standalone")
    parts = prepared["parts"]
    assert len(parts) > 1

    results: list[VisionResult | None] = [
        VisionResult(
            (
                VisionTextElement("Yes", 0),
                VisionTextElement("Status", 1),
            )
        ),
        VisionResult(
            (
                VisionTextElement("Status\nNext", 2),
                VisionTextElement("Yes", 3),
                VisionTextElement("Yes", 4),
            )
        ),
    ]
    results.extend(None for _ in parts[2:])

    merged = merge_tiled_results(prepared, results)

    assert [
        element.text for element in merged.elements if isinstance(element, VisionTextElement)
    ] == ["Yes", "Status", "Next", "Yes", "Yes"]
