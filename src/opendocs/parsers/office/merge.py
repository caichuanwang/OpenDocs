from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from opendocs._models import (
    Block,
    DocumentType,
    HardPageBreakBlock,
    HeadingBlock,
    InlineLink,
    InlineText,
    ListItemBlock,
    MarkdownBlock,
    PageBreakBlock,
    ParagraphBlock,
    ParsedDocument,
    SpannedTableBlock,
    TableBlock,
    TextBlock,
    WarningRecord,
)
from opendocs.parsers.office.models import BreakSlot, ImageSlot, NativeSlot, OfficeDocument
from opendocs.vision.base import VisionResult, VisionTableElement, VisionTextElement

_MAX_WARNINGS_PER_CODE = 20


@dataclass(frozen=True, slots=True)
class OfficeVisualOutcome:
    result: VisionResult | None
    warning_code: str | None = None
    occurrences: frozenset[tuple[int, int]] | None = None

    def __post_init__(self) -> None:
        if self.result is not None and not isinstance(self.result, VisionResult):
            raise TypeError("result must be a VisionResult or None")
        if self.warning_code is not None and not isinstance(self.warning_code, str):
            raise TypeError("warning_code must be a str or None")
        if self.warning_code == "":
            raise ValueError("warning_code must not be empty")
        if self.occurrences is not None and (
            not isinstance(self.occurrences, frozenset)
            or any(
                not isinstance(item, tuple)
                or len(item) != 2
                or any(isinstance(value, bool) or not isinstance(value, int) for value in item)
                for item in self.occurrences
            )
        ):
            raise TypeError("occurrences must be a frozenset of page/source pairs or None")


def _vision_blocks(result: VisionResult) -> tuple[Block, ...]:
    blocks: list[Block] = []
    for element in sorted(result.elements, key=lambda item: item.source_index):
        if isinstance(element, VisionTextElement):
            if element.text.strip():
                blocks.append(TextBlock(element.text.strip()))
        elif isinstance(element, VisionTableElement):
            blocks.append(TableBlock(element.grid, element.header_rows))
    return tuple(blocks)


def _slot_warning(code: str, document_type: DocumentType, page: int, source: int) -> WarningRecord:
    return WarningRecord(
        code=code,
        message=(
            f"{document_type.value.upper()} page {page} source {source}: {code.replace('_', ' ')}"
        ),
    )


def _bounded_warnings(warnings: list[WarningRecord]) -> tuple[WarningRecord, ...]:
    kept: list[WarningRecord] = []
    counts: dict[str, int] = {}
    suppressed: dict[str, int] = {}
    for warning in warnings:
        count = counts.get(warning.code, 0)
        counts[warning.code] = count + 1
        if count < _MAX_WARNINGS_PER_CODE:
            kept.append(warning)
        else:
            suppressed[warning.code] = suppressed.get(warning.code, 0) + 1
    kept.extend(
        WarningRecord(
            code="office_warnings_truncated",
            message=f"suppressed {count} additional {code} warnings",
        )
        for code, count in suppressed.items()
    )
    return tuple(kept)


def merge_office_document(
    document: OfficeDocument,
    visual_outcomes: Mapping[str, OfficeVisualOutcome],
) -> ParsedDocument:
    if not isinstance(document, OfficeDocument):
        raise TypeError("document must be an OfficeDocument")
    for digest, outcome in visual_outcomes.items():
        if not isinstance(digest, str):
            raise TypeError("visual outcome keys must be strings")
        if not isinstance(outcome, OfficeVisualOutcome):
            raise TypeError("visual outcomes must contain OfficeVisualOutcome values")

    blocks: list[Block] = []
    warnings = list(document.warnings)
    for page in document.pages:
        if document.document_type is DocumentType.PPTX:
            blocks.append(PageBreakBlock(page.page_number))
        for slot in sorted(page.slots, key=lambda item: item.source_index):
            if isinstance(slot, NativeSlot):
                blocks.extend(slot.blocks)
            elif isinstance(slot, BreakSlot):
                blocks.append(HardPageBreakBlock())
            elif isinstance(slot, ImageSlot):
                outcome = visual_outcomes.get(slot.content_sha256)
                if outcome is None:
                    continue
                occurrence = (page.page_number, slot.source_index)
                if outcome.occurrences is not None and occurrence not in outcome.occurrences:
                    continue
                if outcome.result is not None:
                    blocks.extend(_vision_blocks(outcome.result))
                if outcome.warning_code is not None:
                    warnings.append(
                        _slot_warning(
                            outcome.warning_code,
                            document.document_type,
                            page.page_number,
                            slot.source_index,
                        )
                    )
    return ParsedDocument(document.document_type, tuple(blocks), _bounded_warnings(warnings))


def has_semantic_office_content(document: ParsedDocument) -> bool:
    for block in document.blocks:
        if isinstance(block, TextBlock) and block.text.strip():
            return True
        if isinstance(block, MarkdownBlock) and block.markdown.strip():
            return True
        if isinstance(block, TableBlock) and any(
            cell.strip() for row in block.grid for cell in row
        ):
            return True
        if isinstance(block, SpannedTableBlock) and any(cell.text.strip() for cell in block.cells):
            return True
        if isinstance(block, ParagraphBlock | HeadingBlock | ListItemBlock) and any(
            inline.text.strip() if isinstance(inline, InlineText) else inline.label.strip()
            for inline in block.inlines
            if isinstance(inline, InlineText | InlineLink)
        ):
            return True
        if not isinstance(block, PageBreakBlock | HardPageBreakBlock):
            continue
    return False
