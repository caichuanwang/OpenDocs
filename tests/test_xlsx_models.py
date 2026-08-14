from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any, cast

import pytest

from opendocs._models import PageBreakBlock, TableBlock, TextBlock, WarningRecord
from opendocs._native_protocol import MAX_FRAME_BYTES, encode_message
from opendocs.errors import LimitExceededError
from opendocs.parsers.xlsx.models import (
    XlsxChartSlot,
    XlsxDocument,
    XlsxImageSlot,
    XlsxNativeSlot,
    XlsxSheet,
    XlsxSheetKind,
    XlsxSheetState,
    document_from_wire,
    document_to_wire,
)


def _document() -> XlsxDocument:
    return XlsxDocument(
        sheets=(
            XlsxSheet(
                sheet_index=1,
                name="Visible",
                kind=XlsxSheetKind.WORKSHEET,
                state=XlsxSheetState.VISIBLE,
                slots=(
                    XlsxNativeSlot(
                        source_index=0,
                        anchor="A1:B2",
                        blocks=(
                            TextBlock("alpha"),
                            TableBlock((("head", None), ("value", "tail")), header_rows=0),
                        ),
                    ),
                    XlsxImageSlot(
                        source_index=1,
                        anchor="D4",
                        artifact_name="xlsx-image-1.png",
                        content_sha256="a" * 64,
                        alt_text="diagram",
                    ),
                    XlsxChartSlot(
                        source_index=2,
                        anchor="F5:J20",
                        artifact_name="xlsx-chart-1.png",
                        content_sha256="b" * 64,
                        blocks=(TextBlock("Series: 1, 2, 3"),),
                    ),
                ),
            ),
            XlsxSheet(
                sheet_index=2,
                name="Chart",
                kind=XlsxSheetKind.CHARTSHEET,
                state=XlsxSheetState.VERY_HIDDEN,
                slots=(),
            ),
        ),
        warnings=(WarningRecord(code="kept", message="warning"),),
    )


def test_xlsx_document_wire_round_trip_is_sheet_oriented_and_strict() -> None:
    document = _document()

    wire = document_to_wire(document)
    restored = document_from_wire(wire)

    assert restored == document
    assert "pages" not in repr(wire)
    assert "page_number" not in repr(wire)
    assert isinstance(restored.sheets, tuple)
    assert isinstance(restored.sheets[0].slots, tuple)


def test_xlsx_models_are_frozen_and_require_tuple_collections() -> None:
    document = _document()

    with pytest.raises(FrozenInstanceError):
        document.sheets[0].__setattr__("slots", ())
    with pytest.raises(TypeError, match="sheets"):
        XlsxDocument(sheets=cast(Any, []))
    with pytest.raises(TypeError, match="slots"):
        XlsxSheet(
            sheet_index=1,
            name="Sheet",
            kind=XlsxSheetKind.WORKSHEET,
            state=XlsxSheetState.VISIBLE,
            slots=cast(Any, []),
        )
    with pytest.raises(TypeError, match="supported block"):
        XlsxNativeSlot(source_index=0, anchor="A1", blocks=(cast(Any, PageBreakBlock(1)),))


@pytest.mark.parametrize("anchor", ["a1", "$A$1", "A0", "XFE1", "A1048577", "B2:A1"])
def test_xlsx_slots_reject_noncanonical_or_out_of_range_a1_anchors(anchor: str) -> None:
    with pytest.raises(ValueError, match="anchor"):
        XlsxNativeSlot(source_index=0, anchor=anchor, blocks=(TextBlock("value"),))


def test_xlsx_models_reject_duplicate_indexes_and_unsafe_artifacts() -> None:
    sheet = XlsxSheet(
        sheet_index=1,
        name="Sheet",
        kind=XlsxSheetKind.WORKSHEET,
        state=XlsxSheetState.VISIBLE,
        slots=(
            XlsxNativeSlot(source_index=0, anchor="A1", blocks=(TextBlock("one"),)),
            XlsxNativeSlot(source_index=1, anchor="A2", blocks=(TextBlock("two"),)),
        ),
    )
    with pytest.raises(ValueError, match="sheet indexes"):
        XlsxDocument(sheets=(sheet, sheet))
    with pytest.raises(ValueError, match="sheet name"):
        XlsxSheet(
            sheet_index=1,
            name="Bad/Name",
            kind=XlsxSheetKind.WORKSHEET,
            state=XlsxSheetState.VISIBLE,
            slots=(),
        )
    with pytest.raises(ValueError, match="source indexes"):
        XlsxSheet(
            sheet_index=1,
            name="Sheet",
            kind=XlsxSheetKind.WORKSHEET,
            state=XlsxSheetState.VISIBLE,
            slots=(sheet.slots[0], sheet.slots[0]),
        )
    with pytest.raises(ValueError, match="artifact_name"):
        XlsxImageSlot(
            source_index=0,
            anchor="A1",
            artifact_name="../escape.png",
            content_sha256="a" * 64,
        )
    with pytest.raises(ValueError, match="content_sha256"):
        XlsxImageSlot(
            source_index=0,
            anchor="A1",
            artifact_name="image.png",
            content_sha256="short",
        )


def test_xlsx_wire_rejects_unknown_fields_and_non_tuple_collections() -> None:
    wire = document_to_wire(_document())
    wire["page"] = 1
    with pytest.raises(ValueError, match="XLSX document wire"):
        document_from_wire(wire)

    wire = document_to_wire(_document())
    wire["sheets"] = list(cast(tuple[object, ...], wire["sheets"]))
    with pytest.raises(ValueError, match="sheets"):
        document_from_wire(wire)


def test_xlsx_wire_estimator_rejects_result_before_protocol_encoding() -> None:
    blocks = tuple(TextBlock("x" * 200) for _ in range(8_000))
    document = XlsxDocument(
        sheets=(
            XlsxSheet(
                sheet_index=1,
                name="Sheet",
                kind=XlsxSheetKind.WORKSHEET,
                state=XlsxSheetState.VISIBLE,
                slots=(XlsxNativeSlot(source_index=0, anchor="A1", blocks=blocks),),
            ),
        )
    )

    with pytest.raises(LimitExceededError, match="inline result budget"):
        document_to_wire(document)


def test_xlsx_wire_estimator_keeps_accepted_payload_below_frame_limit() -> None:
    document = XlsxDocument(
        sheets=(
            XlsxSheet(
                sheet_index=1,
                name="Sheet",
                kind=XlsxSheetKind.WORKSHEET,
                state=XlsxSheetState.VISIBLE,
                slots=(
                    XlsxNativeSlot(
                        source_index=0,
                        anchor="A1",
                        blocks=tuple(TextBlock("x" * 200) for _ in range(7_156)),
                    ),
                ),
            ),
        )
    )

    encoded = encode_message({"version": 1, "value": document_to_wire(document)})

    assert len(encoded) - 8 < MAX_FRAME_BYTES
