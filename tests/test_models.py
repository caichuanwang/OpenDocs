from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any, cast

import pytest

from opendocs._models import (
    BBox,
    CoordinateTransform,
    DocumentType,
    MarkdownBlock,
    PageBreakBlock,
    ParsedDocument,
    RenderResult,
    TableBlock,
    TextBlock,
    WarningRecord,
)


def test_document_type_exposes_stable_values() -> None:
    assert DocumentType.TEXT == "text"
    assert DocumentType.MARKDOWN == "markdown"
    assert DocumentType.PDF == "pdf"
    assert DocumentType.IMAGE == "image"
    assert DocumentType.DOCX == "docx"
    assert DocumentType.PPTX == "pptx"


def test_parsed_document_preserves_block_order_and_tuple_storage() -> None:
    first = TextBlock("alpha")
    second = MarkdownBlock("**beta**")
    warning = WarningRecord(code="kept", message="original warning")
    document = ParsedDocument(
        document_type=DocumentType.MARKDOWN,
        blocks=(first, second),
        warnings=(warning,),
    )

    assert document.blocks == (first, second)
    assert isinstance(document.blocks, tuple)
    assert document.warnings == (warning,)
    assert isinstance(document.warnings, tuple)


def test_models_are_frozen() -> None:
    document = ParsedDocument(
        document_type=DocumentType.TEXT,
        blocks=(TextBlock("alpha"),),
    )
    attribute_name = "blocks"

    with pytest.raises(FrozenInstanceError):
        setattr(document, attribute_name, ())


@pytest.mark.parametrize(
    ("factory", "field_name"),
    [
        (lambda value: TextBlock(value), "text"),
        (lambda value: MarkdownBlock(value), "markdown"),
        (lambda value: WarningRecord(code=value, message="message"), "code"),
        (lambda value: WarningRecord(code="code", message=value), "message"),
        (lambda value: RenderResult(markdown=value), "markdown"),
    ],
)
def test_models_reject_non_string_fields(
    factory: Any,
    field_name: str,
) -> None:
    with pytest.raises(TypeError, match=field_name):
        factory(cast(Any, 123))


def test_parsed_document_rejects_non_document_type() -> None:
    with pytest.raises(TypeError, match="document_type"):
        ParsedDocument(
            document_type=cast(Any, "text"),
            blocks=(TextBlock("alpha"),),
        )


@pytest.mark.parametrize(
    ("field_name", "kwargs"),
    [
        (
            "blocks",
            {
                "document_type": DocumentType.TEXT,
                "blocks": cast(Any, [TextBlock("alpha")]),
            },
        ),
        (
            "warnings",
            {
                "document_type": DocumentType.TEXT,
                "blocks": (TextBlock("alpha"),),
                "warnings": cast(Any, [WarningRecord(code="code", message="message")]),
            },
        ),
    ],
)
def test_parsed_document_requires_tuple_fields(
    field_name: str,
    kwargs: dict[str, Any],
) -> None:
    with pytest.raises(TypeError, match=field_name):
        ParsedDocument(**kwargs)


def test_table_block_normalizes_none_and_preserves_rectangular_grid() -> None:
    block = TableBlock(
        grid=(("header", None), ("value", "")),
        header_rows=1,
    )

    assert block.grid == (("header", ""), ("value", ""))
    assert block.header_rows == 1


@pytest.mark.parametrize(
    ("grid", "header_rows", "message"),
    [
        ((), 0, "at least one row"),
        (((),), 0, "at least one column"),
        ((("a",), ("b", "c")), 1, "rectangular"),
        ((("a",),), -1, "header_rows"),
        ((("a",),), 2, "header_rows"),
        (((cast(Any, 1),),), 0, "grid\\[0\\]\\[0\\]"),
    ],
)
def test_table_block_rejects_invalid_grid(
    grid: Any,
    header_rows: int,
    message: str,
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        TableBlock(grid=grid, header_rows=header_rows)


def test_page_break_and_table_are_valid_document_blocks() -> None:
    document = ParsedDocument(
        document_type=DocumentType.PDF,
        blocks=(PageBreakBlock(1), TableBlock((("a",),), header_rows=0)),
    )

    assert document.blocks[0] == PageBreakBlock(1)


@pytest.mark.parametrize("page_number", [0, -1, cast(Any, True), cast(Any, "1")])
def test_page_break_rejects_invalid_page_number(page_number: Any) -> None:
    with pytest.raises((TypeError, ValueError), match="page_number"):
        PageBreakBlock(page_number)


def test_coordinate_transform_maps_page_crop_and_pixels() -> None:
    transform = CoordinateTransform(
        crop_box=BBox(10, 20, 210, 120),
        raster_width=1000,
        raster_height=500,
    )
    page_bbox = BBox(0.25, 0.20, 0.75, 0.80)

    assert transform.page_to_pixels(page_bbox) == (250, 100, 750, 400)
    assert transform.pixels_to_page((250, 100, 750, 400)) == page_bbox
    assert transform.points_to_page(BBox(60, 40, 160, 100)) == page_bbox
    assert transform.page_to_points(page_bbox) == BBox(60, 40, 160, 100)
    assert transform.crop_to_page(BBox(0.0, 0.0, 1.0, 1.0), page_bbox) == page_bbox


@pytest.mark.parametrize(
    "factory",
    [
        lambda: BBox(float("nan"), 0.0, 1.0, 1.0),
        lambda: BBox(0.5, 0.0, 0.4, 1.0),
        lambda: CoordinateTransform(BBox(0, 0, 1, 1), 0, 1),
    ],
)
def test_coordinate_models_reject_invalid_values(factory: Any) -> None:
    with pytest.raises((TypeError, ValueError)):
        factory()


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_coordinate_transform_round_trips_all_page_rotations(rotation: int) -> None:
    transform = CoordinateTransform(
        crop_box=BBox(25, 40, 325, 240),
        media_box=BBox(-40, -20, 410, 320),
        rotation=rotation,
        raster_width=1200,
        raster_height=800,
        crop_pixel_box=(7, 11, 1007, 711),
    )
    points = BBox(75, 80, 225, 200)

    page = transform.points_to_page(points)

    point_round_trip = transform.page_to_points(page)
    assert point_round_trip.left == pytest.approx(points.left)
    assert point_round_trip.top == pytest.approx(points.top)
    assert point_round_trip.right == pytest.approx(points.right)
    assert point_round_trip.bottom == pytest.approx(points.bottom)
    pixels = transform.page_to_pixels(page)
    assert pixels[0] >= 7 and pixels[1] >= 11
    round_tripped = transform.pixels_to_page(pixels)
    assert round_tripped.left <= page.left
    assert round_tripped.top <= page.top
    assert round_tripped.right >= page.right
    assert round_tripped.bottom >= page.bottom


def test_page_to_pixels_uses_floor_ceil_and_preserves_subpixel_bbox() -> None:
    transform = CoordinateTransform(
        crop_box=BBox(0, 0, 100, 100),
        raster_width=20,
        raster_height=20,
        crop_pixel_box=(3, 4, 13, 14),
    )

    assert transform.page_to_pixels(BBox(0.101, 0.201, 0.102, 0.202)) == (4, 6, 5, 7)


def test_bbox_allows_negative_points_but_normalized_bbox_rejects_them() -> None:
    points = BBox(-100, -50, 10, 20)
    assert points.left == -100
    with pytest.raises(ValueError, match=r"within \[0, 1\]"):
        points.require_normalized()


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_negative_boxes_round_trip_on_non_square_raster(rotation: int) -> None:
    transform = CoordinateTransform(
        crop_box=BBox(-100, -50, 200, 150),
        media_box=BBox(-120, -80, 240, 180),
        rotation=rotation,
        raster_width=1400,
        raster_height=900,
        crop_pixel_box=(100, 75, 1100, 775),
    )
    points = BBox(-75, -25, 125, 100)
    page = transform.points_to_page(points)
    round_trip = transform.page_to_points(page)
    assert round_trip.left == pytest.approx(points.left)
    assert round_trip.top == pytest.approx(points.top)
    assert round_trip.right == pytest.approx(points.right)
    assert round_trip.bottom == pytest.approx(points.bottom)
    pixels = transform.page_to_pixels(page)
    assert 100 <= pixels[0] < pixels[2] <= 1100
    assert 75 <= pixels[1] < pixels[3] <= 775
    covered = transform.pixels_to_page(pixels)
    assert covered.left <= page.left
    assert covered.top <= page.top
    assert covered.right >= page.right
    assert covered.bottom >= page.bottom


def test_coordinate_transform_rejects_invalid_rotation_or_crop_outside_media() -> None:
    with pytest.raises(ValueError, match="rotation"):
        CoordinateTransform(BBox(0, 0, 10, 10), 10, 10, rotation=45)
    with pytest.raises(ValueError, match="within media_box"):
        CoordinateTransform(
            BBox(0, 0, 20, 20),
            10,
            10,
            media_box=BBox(5, 5, 15, 15),
        )
    with pytest.raises(ValueError, match="within the raster"):
        CoordinateTransform(
            BBox(0, 0, 20, 20),
            10,
            10,
            crop_pixel_box=(1, 1, 11, 9),
        )


def test_render_result_requires_tuple_warnings() -> None:
    with pytest.raises(TypeError, match="warnings"):
        RenderResult(
            markdown="alpha",
            warnings=cast(Any, [WarningRecord(code="code", message="message")]),
        )


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        (
            {
                "document_type": DocumentType.TEXT,
                "blocks": (TextBlock("alpha"), cast(Any, "beta")),
            },
            "blocks\\[1\\]",
        ),
        (
            {
                "document_type": DocumentType.TEXT,
                "blocks": (TextBlock("alpha"),),
                "warnings": (WarningRecord(code="code", message="message"), cast(Any, "beta")),
            },
            "warnings\\[1\\]",
        ),
    ],
)
def test_parsed_document_rejects_invalid_tuple_elements(
    kwargs: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(TypeError, match=message):
        ParsedDocument(**kwargs)
