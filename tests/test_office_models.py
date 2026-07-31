from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any, cast

import pytest

from opendocs._models import (
    BBox,
    DocumentType,
    InlineText,
    ListItemBlock,
    ListKind,
    PageBreakBlock,
    TableBlock,
    TextBlock,
    WarningRecord,
)
from opendocs.parsers.office.models import (
    BreakSlot,
    ImageSlot,
    NativeSlot,
    OfficeDocument,
    OfficePage,
    document_from_wire,
    document_to_wire,
)


def test_office_document_wire_round_trip_preserves_all_slot_kinds() -> None:
    document = OfficeDocument(
        document_type=DocumentType.DOCX,
        pages=(
            OfficePage(
                page_number=1,
                slots=(
                    NativeSlot(
                        source_index=0,
                        blocks=(
                            TextBlock("alpha"),
                            TableBlock((("head", None), ("value", "tail")), header_rows=1),
                        ),
                    ),
                    ImageSlot(
                        source_index=1,
                        artifact_name="office-media-1.png",
                        content_sha256="a" * 64,
                        bbox=BBox(0.1, 0.2, 0.7, 0.9),
                        alt_text="diagram",
                    ),
                    BreakSlot(source_index=2),
                    NativeSlot(source_index=3, blocks=(PageBreakBlock(2),)),
                ),
            ),
        ),
        warnings=(WarningRecord(code="kept", message="warning"),),
    )

    restored = document_from_wire(document_to_wire(document))

    assert restored == document
    assert isinstance(restored.pages, tuple)
    assert isinstance(restored.pages[0].slots, tuple)
    first_slot = restored.pages[0].slots[0]
    assert isinstance(first_slot, NativeSlot)
    assert isinstance(first_slot.blocks, tuple)


def test_office_document_wire_round_trip_restores_enum_fields() -> None:
    document = OfficeDocument(
        document_type=DocumentType.DOCX,
        pages=(
            OfficePage(
                page_number=1,
                slots=(
                    NativeSlot(
                        source_index=0,
                        blocks=(
                            ListItemBlock(
                                list_id=7,
                                level=1,
                                kind=ListKind.ORDERED,
                                ordinal=3,
                                inlines=(InlineText("step"),),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    wire = document_to_wire(document)
    pages_wire = cast(tuple[dict[str, object], ...], wire["pages"])
    page_wire = pages_wire[0]
    page_slots = cast(tuple[dict[str, object], ...], page_wire["slots"])
    native_slot_wire = page_slots[0]
    block_wire = cast(tuple[dict[str, object], ...], native_slot_wire["blocks"])[0]
    block_fields = cast(dict[str, object], block_wire["fields"])

    block_fields["kind"] = ListKind.ORDERED.value
    assert block_fields["kind"] == ListKind.ORDERED.value

    restored = document_from_wire(wire)
    restored_block = cast(NativeSlot, restored.pages[0].slots[0]).blocks[0]

    assert restored == document
    assert isinstance(restored_block, ListItemBlock)
    assert restored_block.kind is ListKind.ORDERED


def test_office_models_are_frozen() -> None:
    page = OfficePage(page_number=1, slots=(BreakSlot(source_index=0),))

    with pytest.raises(FrozenInstanceError):
        page.__setattr__("slots", ())


@pytest.mark.parametrize(
    ("wire", "message"),
    [
        ({}, "Office document wire is invalid"),
        (
            {
                "type": "office_document",
                "document_type": DocumentType.DOCX.value,
                "pages": (),
                "warnings": (),
                "extra": True,
            },
            "Office document wire is invalid",
        ),
        (
            {
                "type": "office_document",
                "document_type": DocumentType.DOCX.value,
                "pages": [],
                "warnings": (),
            },
            "Office document pages are invalid",
        ),
        (
            {
                "type": "office_document",
                "document_type": "pdf",
                "pages": (),
                "warnings": (),
            },
            "Office document type is invalid",
        ),
        (
            {
                "type": "office_document",
                "document_type": DocumentType.DOCX.value,
                "pages": (
                    {
                        "type": "office_page",
                        "page_number": 1,
                        "slots": (
                            {
                                "type": "native_slot",
                                "source_index": 0,
                                "blocks": [],
                            },
                        ),
                    },
                ),
                "warnings": (),
            },
            "Office native slot blocks are invalid",
        ),
        (
            {
                "type": "office_document",
                "document_type": DocumentType.DOCX.value,
                "pages": (
                    {
                        "type": "office_page",
                        "page_number": 1,
                        "slots": (
                            {"type": "break_slot", "source_index": 0},
                            {"type": "break_slot", "source_index": 0},
                        ),
                    },
                ),
                "warnings": (),
            },
            "Office page source indexes must be unique",
        ),
        (
            {
                "type": "office_document",
                "document_type": DocumentType.DOCX.value,
                "pages": (
                    {
                        "type": "office_page",
                        "page_number": 1,
                        "slots": (
                            {
                                "type": "image_slot",
                                "source_index": 0,
                                "artifact_name": "nested/file.png",
                                "content_sha256": "a" * 64,
                                "bbox": (0.0, 0.0, 1.0, 1.0),
                                "alt_text": None,
                            },
                        ),
                    },
                ),
                "warnings": (),
            },
            "artifact_name",
        ),
        (
            {
                "type": "office_document",
                "document_type": DocumentType.DOCX.value,
                "pages": (
                    {
                        "type": "office_page",
                        "page_number": 1,
                        "slots": (
                            {
                                "type": "image_slot",
                                "source_index": 0,
                                "artifact_name": "file.png",
                                "content_sha256": "a" * 64,
                                "bbox": (0.0, 0.0, 1.2, 1.0),
                                "alt_text": None,
                            },
                        ),
                    },
                ),
                "warnings": (),
            },
            "bbox",
        ),
        (
            {
                "type": "office_document",
                "document_type": DocumentType.DOCX.value,
                "pages": (
                    {
                        "type": "office_page",
                        "page_number": 1,
                        "slots": ({"type": "break_slot", "source_index": -1},),
                    },
                ),
                "warnings": (),
            },
            "source_index",
        ),
    ],
)
def test_document_from_wire_rejects_invalid_shapes(wire: dict[str, object], message: str) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        document_from_wire(wire)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (
            cast(Any, "bad"),
            "page_number",
        ),
        (
            cast(Any, True),
            "page_number",
        ),
        (
            -1,
            "page_number",
        ),
    ],
)
def test_page_rejects_invalid_page_number(value: Any, message: str) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        OfficePage(page_number=value, slots=(BreakSlot(source_index=0),))


def test_native_slot_requires_non_empty_block_tuple() -> None:
    with pytest.raises((TypeError, ValueError), match="blocks"):
        NativeSlot(source_index=0, blocks=cast(Any, []))

    with pytest.raises((TypeError, ValueError), match="at least one block"):
        NativeSlot(source_index=0, blocks=())


def test_image_slot_requires_portable_basename_and_sha256() -> None:
    with pytest.raises(ValueError, match="artifact_name"):
        ImageSlot(
            source_index=0,
            artifact_name="../escape.png",
            content_sha256="a" * 64,
            bbox=BBox(0.0, 0.0, 1.0, 1.0),
        )

    with pytest.raises(ValueError, match="content_sha256"):
        ImageSlot(
            source_index=0,
            artifact_name="image.png",
            content_sha256="short",
            bbox=BBox(0.0, 0.0, 1.0, 1.0),
        )
