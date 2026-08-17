from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from itertools import groupby

from opendocs._models import (
    Block,
    DocumentType,
    HeadingBlock,
    InlineText,
    MarkdownBlock,
    ParagraphBlock,
    ParsedDocument,
    TableBlock,
    TextBlock,
    WarningRecord,
)
from opendocs.parsers.xlsx.models import (
    XlsxChartSlot,
    XlsxDocument,
    XlsxImageSlot,
    XlsxNativeSlot,
    XlsxSheet,
    XlsxSheetState,
    XlsxSlot,
)
from opendocs.vision.base import VisionResult, VisionTableElement, VisionTextElement

_A1_START = re.compile(r"^([A-Z]{1,3})([1-9][0-9]{0,6})")
_STATE_LABELS = {
    XlsxSheetState.VISIBLE: "Visible",
    XlsxSheetState.HIDDEN: "Hidden",
    XlsxSheetState.VERY_HIDDEN: "Very Hidden",
}


@dataclass(frozen=True, slots=True)
class XlsxVisualOutcome:
    result: VisionResult | None
    warning_code: str | None = None

    def __post_init__(self) -> None:
        if self.result is not None and not isinstance(self.result, VisionResult):
            raise TypeError("result must be a VisionResult or None")
        if self.warning_code is not None and not isinstance(self.warning_code, str):
            raise TypeError("warning_code must be a str or None")
        if self.warning_code == "":
            raise ValueError("warning_code must not be empty")


def _column_number(label: str) -> int:
    number = 0
    for character in label:
        number = number * 26 + ord(character) - ord("A") + 1
    return number


def _anchor_position(anchor: str) -> tuple[int, int]:
    match = _A1_START.match(anchor)
    if match is None:
        raise ValueError("XLSX slot anchor is invalid")
    return int(match.group(2)), _column_number(match.group(1))


def _slot_kind_rank(slot: XlsxSlot) -> int:
    if isinstance(slot, XlsxNativeSlot):
        return 0
    if isinstance(slot, XlsxChartSlot):
        return 1
    return 2


def _slot_sort_key(slot: XlsxSlot) -> tuple[int, int, int, int]:
    row, column = _anchor_position(slot.anchor)
    return row, column, _slot_kind_rank(slot), slot.source_index


def _is_extractor_prelude(slot: XlsxSlot, sheet_index: int) -> bool:
    if not isinstance(slot, XlsxNativeSlot) or slot.source_index != 0:
        return False
    expected = f"<!-- xlsx-sheet: {sheet_index} -->"
    return any(
        isinstance(block, MarkdownBlock) and block.markdown == expected for block in slot.blocks
    )


def _sheet_prelude(sheet: XlsxSheet) -> tuple[Block, ...]:
    return (
        MarkdownBlock(f"<!-- xlsx-sheet: {sheet.sheet_index} -->"),
        HeadingBlock(1, (InlineText(f"{sheet.name} ({_STATE_LABELS[sheet.state]})"),)),
    )


def _object_anchor(sheet: XlsxSheet, slot: XlsxImageSlot | XlsxChartSlot) -> MarkdownBlock:
    return MarkdownBlock(
        f"<!-- xlsx-object: sheet={sheet.sheet_index} "
        f"range={slot.anchor} object={slot.source_index} -->"
    )


def _metadata_blocks(
    kind: str,
    slot: XlsxImageSlot | XlsxChartSlot,
) -> tuple[Block, ...]:
    labels = (
        (f"{kind} name", slot.object_name),
        (f"{kind} description", slot.alt_text),
        (f"{kind} title", slot.title),
    )
    return (
        *((HeadingBlock(3, (InlineText("Embedded image"),)),) if kind == "Image" else ()),
        *(ParagraphBlock((InlineText(f"{label}: {value}"),)) for label, value in labels if value),
    )


def _native_blocks(sheet: XlsxSheet, slot: XlsxSlot) -> tuple[Block, ...]:
    if isinstance(slot, XlsxNativeSlot):
        return slot.blocks
    if isinstance(slot, XlsxChartSlot):
        return (
            _object_anchor(sheet, slot),
            *_metadata_blocks("Chart", slot),
            *slot.blocks,
        )
    return (_object_anchor(sheet, slot), *_metadata_blocks("Image", slot))


def _vision_blocks(result: VisionResult) -> tuple[Block, ...]:
    blocks: list[Block] = [HeadingBlock(3, (InlineText("Visual interpretation"),))]
    for element in sorted(result.elements, key=lambda item: item.source_index):
        if isinstance(element, VisionTextElement):
            if element.text.strip():
                blocks.append(TextBlock(element.text.strip()))
        elif isinstance(element, VisionTableElement):
            blocks.append(TableBlock(element.grid, element.header_rows))
    return tuple(blocks)


def _visual_blocks(
    slot: XlsxSlot,
    visual_outcomes: Mapping[str, XlsxVisualOutcome],
) -> tuple[Block, ...]:
    if not isinstance(slot, XlsxImageSlot | XlsxChartSlot):
        return ()
    outcome = visual_outcomes.get(slot.content_sha256)
    if outcome is None or outcome.result is None:
        return ()
    return _vision_blocks(outcome.result)


def _visual_warning(
    sheet: XlsxSheet,
    slot: XlsxImageSlot | XlsxChartSlot,
    code: str,
) -> WarningRecord:
    kind = "chart" if isinstance(slot, XlsxChartSlot) else "image"
    return WarningRecord(
        code=code,
        message=f"{sheet.name}!{slot.anchor}: {kind} visual interpretation was not completed",
    )


def merge_xlsx_document(
    document: XlsxDocument,
    visual_outcomes: Mapping[str, XlsxVisualOutcome],
) -> ParsedDocument:
    if not isinstance(document, XlsxDocument):
        raise TypeError("document must be an XlsxDocument")
    for digest, outcome in visual_outcomes.items():
        if not isinstance(digest, str):
            raise TypeError("visual outcome keys must be strings")
        if not isinstance(outcome, XlsxVisualOutcome):
            raise TypeError("visual outcomes must contain XlsxVisualOutcome values")

    blocks: list[Block] = []
    warnings = list(document.warnings)
    for sheet in document.sheets:
        blocks.extend(_sheet_prelude(sheet))
        slots = tuple(
            sorted(
                (
                    slot
                    for slot in sheet.slots
                    if not _is_extractor_prelude(slot, sheet.sheet_index)
                ),
                key=_slot_sort_key,
            )
        )
        for _position, positioned_slots in groupby(
            slots,
            key=lambda item: _anchor_position(item.anchor),
        ):
            group = tuple(positioned_slots)
            for slot in group:
                blocks.extend(_native_blocks(sheet, slot))
            for slot in group:
                blocks.extend(_visual_blocks(slot, visual_outcomes))
                if isinstance(slot, XlsxImageSlot | XlsxChartSlot):
                    outcome = visual_outcomes.get(slot.content_sha256)
                    if outcome is not None and outcome.warning_code is not None:
                        warnings.append(_visual_warning(sheet, slot, outcome.warning_code))
    return ParsedDocument(DocumentType.XLSX, tuple(blocks), tuple(warnings))
