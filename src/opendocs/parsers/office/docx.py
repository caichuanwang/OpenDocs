from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from opendocs._models import (
    BBox,
    Block,
    DocumentType,
    HeadingBlock,
    Inline,
    InlineLink,
    InlineText,
    ListItemBlock,
    ListKind,
    ParagraphBlock,
    SpannedTableBlock,
    SpannedTableCell,
    TableBlock,
    WarningRecord,
)
from opendocs.errors import CorruptDocumentError, RuntimeDependencyError
from opendocs.parsers.office.models import (
    BreakSlot,
    ImageSlot,
    NativeSlot,
    OfficeDocument,
    OfficePage,
)
from opendocs.parsers.office.package import (
    ExtractedMediaArtifact,
    extract_package_media,
    open_validated_office_document,
)
from opendocs.source import ParseWorkspace

_HEADING_NAME_RE = re.compile(r"(?:heading|标题)\s*([1-9])", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class _LevelDefinition:
    kind: ListKind | None
    start: int


@dataclass(frozen=True, slots=True)
class _NumDefinition:
    abstract_num_id: int
    start_overrides: dict[int, int]


@dataclass(frozen=True, slots=True)
class _ListState:
    list_id: int
    level: int
    kind: ListKind
    ordinal: int


@dataclass(frozen=True, slots=True)
class _ParagraphSemantics:
    heading_level: int | None
    list_state: _ListState | None


@dataclass(frozen=True, slots=True)
class _RawNative:
    blocks: tuple[Block, ...]


@dataclass(frozen=True, slots=True)
class _RawImage:
    artifact_name: str
    content_sha256: str
    alt_text: str | None


@dataclass(frozen=True, slots=True)
class _RawBreak:
    pass


@dataclass(slots=True)
class _CellRecord:
    row: int
    column: int
    row_span: int
    column_span: int
    text: str


class _NumberingResolver:
    def __init__(self, document: Any) -> None:
        self._abstract_levels: dict[int, dict[int, _LevelDefinition]] = {}
        self._num_definitions: dict[int, _NumDefinition] = {}
        self._list_ids: dict[int, int] = {}
        self._ordinals: dict[tuple[int, int], int] = {}
        try:
            numbering = document.part.numbering_part.numbering_definitions._numbering
        except (AttributeError, KeyError):
            numbering = None
        if numbering is None:
            return

        for abstract in _children(numbering, qn("w:abstractNum")):
            abstract_id = _int_attr(abstract, qn("w:abstractNumId"))
            if abstract_id is None:
                continue
            levels: dict[int, _LevelDefinition] = {}
            for level in _children(abstract, qn("w:lvl")):
                ilvl = _int_attr(level, qn("w:ilvl"))
                if ilvl is None:
                    continue
                num_fmt_node = _first(_children(level, qn("w:numFmt")))
                start_node = _first(_children(level, qn("w:start")))
                num_fmt = None if num_fmt_node is None else num_fmt_node.get(qn("w:val"))
                start = 1
                if start_node is not None:
                    start_value = start_node.get(qn("w:val"))
                    if start_value and start_value.isdigit():
                        start = max(int(start_value), 1)
                kind = None
                if num_fmt == "bullet":
                    kind = ListKind.BULLET
                elif num_fmt and num_fmt != "none":
                    kind = ListKind.ORDERED
                levels[ilvl] = _LevelDefinition(kind=kind, start=start)
            self._abstract_levels[abstract_id] = levels

        for num in _children(numbering, qn("w:num")):
            num_id = _int_attr(num, qn("w:numId"))
            abstract_num = _first(_children(num, qn("w:abstractNumId")))
            abstract_id = None if abstract_num is None else _int_attr(abstract_num, qn("w:val"))
            if num_id is None or abstract_id is None:
                continue
            overrides: dict[int, int] = {}
            for override in _children(num, qn("w:lvlOverride")):
                ilvl = _int_attr(override, qn("w:ilvl"))
                if ilvl is None:
                    continue
                start_override = _first(_children(override, qn("w:startOverride")))
                if start_override is None:
                    continue
                start_value = start_override.get(qn("w:val"))
                if start_value and start_value.isdigit():
                    overrides[ilvl] = max(int(start_value), 1)
            self._num_definitions[num_id] = _NumDefinition(abstract_id, overrides)

    def state_for(self, paragraph: Paragraph) -> _ListState | None:
        num_info = _paragraph_numbering(paragraph)
        if num_info is None:
            return None
        num_id, level = num_info
        num_definition = self._num_definitions.get(num_id)
        if num_definition is None:
            return None
        level_definition = self._abstract_levels.get(num_definition.abstract_num_id, {}).get(level)
        if level_definition is None or level_definition.kind is None:
            return None

        list_id = self._list_ids.setdefault(num_id, len(self._list_ids))
        if level_definition.kind is ListKind.BULLET:
            ordinal = 1
        else:
            key = (list_id, level)
            start = num_definition.start_overrides.get(level, level_definition.start)
            ordinal = self._ordinals.get(key, start - 1) + 1
            self._ordinals[key] = ordinal
        return _ListState(list_id=list_id, level=level, kind=level_definition.kind, ordinal=ordinal)


def extract_docx(path: Path, workspace: ParseWorkspace) -> OfficeDocument:
    if not isinstance(path, Path):
        raise TypeError("path must be a Path")
    if not isinstance(workspace, ParseWorkspace):
        raise TypeError("workspace must be a ParseWorkspace")
    try:
        artifacts = extract_package_media(
            path,
            document_type=DocumentType.DOCX,
            workspace=workspace,
        )
        document = open_validated_office_document(
            path,
            document_type=DocumentType.DOCX,
            opener=lambda source: Document(str(source)),
        )
    except ImportError as error:
        raise RuntimeDependencyError("python-docx is required to parse DOCX documents") from error
    except (CorruptDocumentError, RuntimeDependencyError):
        raise
    except (KeyError, OSError, TypeError, ValueError) as error:
        raise CorruptDocumentError("DOCX package could not be opened") from error

    numbering = _NumberingResolver(document)
    media_by_name = {artifact.member_name: artifact for artifact in artifacts}
    raw_slots: list[_RawNative | _RawImage | _RawBreak] = []
    warnings: list[WarningRecord] = []
    for element in document.element.body:
        if element.tag == qn("w:p"):
            raw_slots.extend(
                _paragraph_slots(Paragraph(element, document), numbering, media_by_name, warnings)
            )
        elif element.tag == qn("w:tbl"):
            block = _table_block(Table(element, document), warnings)
            if block is not None:
                raw_slots.append(_RawNative((block,)))

    total_slots = max(len(raw_slots), 1)
    slots: list[NativeSlot | ImageSlot | BreakSlot] = []
    for source_index, raw_slot in enumerate(raw_slots):
        if isinstance(raw_slot, _RawBreak):
            slots.append(BreakSlot(source_index))
        elif isinstance(raw_slot, _RawNative):
            slots.append(NativeSlot(source_index, raw_slot.blocks))
        else:
            slots.append(
                ImageSlot(
                    source_index,
                    raw_slot.artifact_name,
                    raw_slot.content_sha256,
                    _synthetic_bbox(source_index, total_slots),
                    raw_slot.alt_text,
                )
            )

    return OfficeDocument(
        DocumentType.DOCX,
        (OfficePage(1, tuple(slots)),),
        tuple(warnings),
    )


def _paragraph_slots(
    paragraph: Paragraph,
    numbering: _NumberingResolver,
    media_by_name: dict[str, ExtractedMediaArtifact],
    warnings: list[WarningRecord],
) -> list[_RawNative | _RawImage | _RawBreak]:
    semantics = _ParagraphSemantics(
        heading_level=_heading_level(paragraph),
        list_state=numbering.state_for(paragraph),
    )
    items: list[_RawNative | _RawImage | _RawBreak] = []
    inlines: list[Inline] = []

    for child in paragraph._p:
        if child.tag == qn("w:r"):
            _consume_run(
                child,
                semantics,
                part=paragraph.part,
                link_target=None,
                media_by_name=media_by_name,
                inlines=inlines,
                items=items,
            )
        elif child.tag == qn("w:hyperlink"):
            target = _hyperlink_target(paragraph, child)
            for run in _children(child, qn("w:r")):
                _consume_run(
                    run,
                    semantics,
                    part=paragraph.part,
                    link_target=target,
                    media_by_name=media_by_name,
                    inlines=inlines,
                    items=items,
                )

    if _has_visible_content(inlines):
        items.append(_RawNative((_paragraph_block(tuple(inlines), semantics),)))
        inlines.clear()
    if _paragraph_has_page_start_section_break(paragraph):
        items.append(_RawBreak())
    return items


def _consume_run(
    run: Any,
    semantics: _ParagraphSemantics,
    *,
    part: Any,
    link_target: str | None,
    media_by_name: dict[str, ExtractedMediaArtifact],
    inlines: list[Inline],
    items: list[_RawNative | _RawImage | _RawBreak],
) -> None:
    for node in run:
        if node.tag == qn("w:t"):
            _append_inline(inlines, node.text or "", target=link_target)
        elif node.tag == qn("w:tab"):
            _append_inline(inlines, "\t", target=link_target)
        elif node.tag == qn("w:cr"):
            _append_inline(inlines, "\n", target=link_target)
        elif node.tag == qn("w:noBreakHyphen"):
            _append_inline(inlines, "-", target=link_target)
        elif node.tag == qn("w:br"):
            if node.get(qn("w:type")) == "page":
                if _has_visible_content(inlines):
                    items.append(_RawNative((_paragraph_block(tuple(inlines), semantics),)))
                    inlines.clear()
                items.append(_RawBreak())
            else:
                _append_inline(inlines, "\n", target=link_target)
        elif node.tag == qn("w:drawing"):
            if _has_visible_content(inlines):
                items.append(_RawNative((_paragraph_block(tuple(inlines), semantics),)))
                inlines.clear()
            items.extend(_drawing_slots(node, part, media_by_name))


def _paragraph_block(inlines: tuple[Inline, ...], semantics: _ParagraphSemantics) -> Block:
    if semantics.list_state is not None:
        return ListItemBlock(
            semantics.list_state.list_id,
            semantics.list_state.level,
            semantics.list_state.kind,
            semantics.list_state.ordinal,
            inlines,
        )
    if semantics.heading_level is not None:
        return HeadingBlock(semantics.heading_level, inlines)
    return ParagraphBlock(inlines)


def _append_inline(inlines: list[Inline], text: str, *, target: str | None) -> None:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized:
        return
    if target:
        if inlines and isinstance(inlines[-1], InlineLink) and inlines[-1].target == target:
            previous = inlines[-1]
            inlines[-1] = InlineLink(previous.label + normalized, target)
            return
        try:
            inlines.append(InlineLink(normalized, target))
        except (TypeError, ValueError):
            _append_inline(inlines, normalized, target=None)
        return
    if inlines and isinstance(inlines[-1], InlineText):
        previous = inlines[-1]
        inlines[-1] = InlineText(previous.text + normalized)
    else:
        inlines.append(InlineText(normalized))


def _has_visible_content(inlines: list[Inline]) -> bool:
    return any(
        inline.label.strip() if isinstance(inline, InlineLink) else inline.text.strip()
        for inline in inlines
    )


def _heading_level(paragraph: Paragraph) -> int | None:
    direct = _outline_level(paragraph._element)
    if direct is not None:
        return direct
    for style in _style_chain(paragraph):
        inherited = _outline_level(style._element)
        if inherited is not None:
            return inherited
        for value in (style.name, style.style_id):
            if not value:
                continue
            match = _HEADING_NAME_RE.fullmatch(value)
            if match:
                return min(max(int(match.group(1)), 1), 6)
    return None


def _outline_level(element: Any) -> int | None:
    p_pr = _first(_children(element, qn("w:pPr")))
    if p_pr is None:
        return None
    node = _first(_children(p_pr, qn("w:outlineLvl")))
    if node is None:
        return None
    value = node.get(qn("w:val"))
    if value is None or not value.isdigit():
        return None
    return min(max(int(value) + 1, 1), 6)


def _style_chain(paragraph: Paragraph) -> list[Any]:
    chain: list[Any] = []
    seen: set[str] = set()
    style = paragraph.style
    while style is not None:
        identity = style.style_id or style.name
        if identity in seen:
            break
        seen.add(identity)
        chain.append(style)
        style = style.base_style
    return chain


def _paragraph_numbering(paragraph: Paragraph) -> tuple[int, int] | None:
    direct = _num_pr(paragraph._element)
    if direct is not None:
        return direct
    for style in _style_chain(paragraph):
        inherited = _num_pr(style._element)
        if inherited is not None:
            return inherited
    return None


def _num_pr(element: Any) -> tuple[int, int] | None:
    p_pr = _first(_children(element, qn("w:pPr")))
    if p_pr is None:
        return None
    num_pr = _first(_children(p_pr, qn("w:numPr")))
    if num_pr is None:
        return None
    num_id_node = _first(_children(num_pr, qn("w:numId")))
    if num_id_node is None:
        return None
    num_id = _int_attr(num_id_node, qn("w:val"))
    if num_id is None:
        return None
    level_node = _first(_children(num_pr, qn("w:ilvl")))
    level = 0 if level_node is None else (_int_attr(level_node, qn("w:val")) or 0)
    return num_id, max(level, 0)


def _hyperlink_target(paragraph: Paragraph, hyperlink: Any) -> str | None:
    relationship_id = hyperlink.get(qn("r:id"))
    address = ""
    if relationship_id:
        relation = paragraph.part.rels.get(relationship_id)
        if relation is not None:
            address = relation.target_ref
    anchor = hyperlink.get(qn("w:anchor")) or ""
    if address and anchor:
        return f"{address}#{anchor}"
    if address:
        return address
    if anchor:
        return f"#{anchor}"
    return None


def _drawing_slots(
    drawing: Any,
    part: Any,
    media_by_name: dict[str, ExtractedMediaArtifact],
) -> list[_RawImage]:
    alt_text = _drawing_alt_text(drawing)
    items: list[_RawImage] = []
    for blip in drawing.iter(qn("a:blip")):
        embed = blip.get(qn("r:embed"))
        if not embed:
            continue
        relation = part.rels.get(embed)
        if relation is None or relation.reltype != RELATIONSHIP_TYPE.IMAGE or relation.is_external:
            continue
        member_name = str(relation.target_part.partname).lstrip("/")
        artifact = media_by_name.get(member_name)
        if artifact is None:
            continue
        items.append(
            _RawImage(
                artifact_name=artifact.artifact_name,
                content_sha256=artifact.content_sha256,
                alt_text=alt_text,
            )
        )
    return items


def _drawing_alt_text(drawing: Any) -> str | None:
    doc_pr = next((node for node in drawing.iter(qn("wp:docPr"))), None)
    if doc_pr is None:
        return None
    for attribute in ("descr", "title", "name"):
        value = doc_pr.get(attribute)
        if value:
            return value
    return None


def _table_block(
    table: Table, warnings: list[WarningRecord]
) -> TableBlock | SpannedTableBlock | None:
    rows = _children(table._tbl, qn("w:tr"))
    if not rows:
        return None
    row_count = len(rows)
    column_count = max(
        len(table.columns),
        max(
            (sum(_grid_span(cell) for cell in _children(row, qn("w:tc"))) for row in rows),
            default=0,
        ),
    )
    if column_count <= 0:
        return None
    occupied: list[list[_CellRecord | None]] = [[None] * column_count for _ in range(row_count)]
    records: list[_CellRecord] = []
    nested_flattened = False

    for row_index, row in enumerate(rows):
        column_index = 0
        for cell in _children(row, qn("w:tc")):
            while column_index < column_count and occupied[row_index][column_index] is not None:
                column_index += 1
            if column_index >= column_count:
                break
            grid_span = max(_grid_span(cell), 1)
            vmerge = _vmerge(cell)
            if vmerge == "continue" and row_index > 0:
                origin = occupied[row_index - 1][column_index]
                if origin is not None:
                    origin.row_span += 1
                    span = origin.column_span
                    for offset in range(span):
                        if column_index + offset < column_count:
                            occupied[row_index][column_index + offset] = origin
                    column_index += span
                    continue

            text, nested = _table_cell_text(cell)
            nested_flattened |= nested
            span = min(grid_span, column_count - column_index)
            record = _CellRecord(
                row=row_index,
                column=column_index,
                row_span=1,
                column_span=span,
                text=text,
            )
            records.append(record)
            for offset in range(span):
                occupied[row_index][column_index + offset] = record
            column_index += span

        while column_index < column_count:
            if occupied[row_index][column_index] is None:
                blank = _CellRecord(row_index, column_index, 1, 1, "")
                records.append(blank)
                occupied[row_index][column_index] = blank
            column_index += 1

    header_rows = 0
    for row in rows:
        tr_pr = _first(_children(row, qn("w:trPr")))
        if tr_pr is None or _first(_children(tr_pr, qn("w:tblHeader"))) is None:
            break
        header_rows += 1

    if nested_flattened:
        warnings.append(
            WarningRecord(
                code="docx_nested_table_flattened",
                message="DOCX nested table content was flattened into cell text",
            )
        )

    has_spans = any(record.row_span > 1 or record.column_span > 1 for record in records)
    if not has_spans:
        grid = tuple(
            tuple(
                (occupied[row][column] or _CellRecord(row, column, 1, 1, "")).text
                for column in range(column_count)
            )
            for row in range(row_count)
        )
        return TableBlock(grid, header_rows)

    return SpannedTableBlock(
        row_count,
        column_count,
        tuple(
            SpannedTableCell(
                record.row,
                record.column,
                record.row_span,
                record.column_span,
                record.text,
            )
            for record in records
        ),
        header_rows,
    )


def _grid_span(cell: Any) -> int:
    tc_pr = _first(_children(cell, qn("w:tcPr")))
    if tc_pr is None:
        return 1
    grid_span = _first(_children(tc_pr, qn("w:gridSpan")))
    if grid_span is None:
        return 1
    value = grid_span.get(qn("w:val"))
    return int(value) if value and value.isdigit() else 1


def _vmerge(cell: Any) -> str | None:
    tc_pr = _first(_children(cell, qn("w:tcPr")))
    if tc_pr is None:
        return None
    vmerge = _first(_children(tc_pr, qn("w:vMerge")))
    if vmerge is None:
        return None
    return vmerge.get(qn("w:val")) or "continue"


def _table_cell_text(cell: Any) -> tuple[str, bool]:
    parts: list[str] = []
    nested = False
    for child in cell:
        if child.tag == qn("w:p"):
            text = _paragraph_plain_text(child)
            if text:
                parts.append(text)
        elif child.tag == qn("w:tbl"):
            nested = True
            text = _nested_table_text(child)
            if text:
                parts.append(text)
    return "\n".join(part for part in parts if part).strip(), nested


def _paragraph_plain_text(paragraph: Any) -> str:
    pieces: list[str] = []
    for child in paragraph:
        if child.tag == qn("w:r"):
            _collect_run_text(child, pieces)
        elif child.tag == qn("w:hyperlink"):
            for run in _children(child, qn("w:r")):
                _collect_run_text(run, pieces)
    return "".join(pieces).strip()


def _collect_run_text(run: Any, pieces: list[str]) -> None:
    for node in run:
        if node.tag == qn("w:t"):
            pieces.append(node.text or "")
        elif node.tag == qn("w:tab"):
            pieces.append("\t")
        elif node.tag == qn("w:cr") or node.tag == qn("w:br"):
            pieces.append("\n")
        elif node.tag == qn("w:noBreakHyphen"):
            pieces.append("-")


def _nested_table_text(table: Any) -> str:
    rows: list[str] = []
    for row in _children(table, qn("w:tr")):
        cells = [text for cell in _children(row, qn("w:tc")) if (text := _table_cell_text(cell)[0])]
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows).strip()


def _paragraph_has_page_start_section_break(paragraph: Paragraph) -> bool:
    p_pr = _first(_children(paragraph._element, qn("w:pPr")))
    if p_pr is None:
        return False
    section = _first(_children(p_pr, qn("w:sectPr")))
    if section is None:
        return False
    section_type = _first(_children(section, qn("w:type")))
    if section_type is None:
        return True
    value = section_type.get(qn("w:val"))
    return value in {None, "nextPage", "oddPage", "evenPage"}


def _synthetic_bbox(index: int, total: int) -> BBox:
    return BBox(0.0, index / total, 1.0, (index + 1) / total)


def _int_attr(element: Any, attribute: str) -> int | None:
    value = element.get(attribute)
    if value is None or not value.isdigit():
        return None
    return int(value)


def _first(values: list[Any]) -> Any | None:
    return values[0] if values else None


def _children(element: Any, tag: str) -> list[Any]:
    return [child for child in element if child.tag == tag]
