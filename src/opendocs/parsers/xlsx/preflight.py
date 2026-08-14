from __future__ import annotations

import posixpath
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO, Any
from zipfile import BadZipFile, ZipFile, ZipInfo

from defusedxml import ElementTree as DefusedET
from defusedxml.common import DefusedXmlException

from opendocs._models import DocumentType
from opendocs.errors import CorruptDocumentError, LimitExceededError
from opendocs.parsers.office.package import validate_office_package
from opendocs.parsers.xlsx.models import XlsxSheetKind, XlsxSheetState

MAX_SHEETS = 128
MAX_DECLARED_CELLS = 2_000_000
MAX_SERIALIZED_CELLS = 200_000
MAX_NON_EMPTY_CELLS = 50_000
MAX_MATERIALIZED_GRID_CELLS = 200_000
MAX_MERGE_RANGES = 10_000
MAX_MERGE_FOOTPRINT = 50_000
MAX_SHARED_STRINGS = 100_000
MAX_SHARED_STRING_CHARS = 1_000_000
MAX_TABLES = 1_024
MAX_TABLE_FOOTPRINT = 200_000
MAX_TABLE_COLUMNS = 10_000
MAX_HYPERLINKS_AND_COMMENTS = 20_000
MAX_HYPERLINK_FOOTPRINT = 50_000
MAX_DRAWING_OBJECTS = 256
MAX_CHART_CACHE_POINTS = 200_000
MAX_NATIVE_TEXT_CHARS = 1_000_000
MAX_STYLE_RECORDS = 50_000
MAX_NUMBER_FORMATS = 10_000
MAX_FONTS = 10_000
MAX_FILLS = 10_000
MAX_BORDERS = 10_000
MAX_CELL_STYLE_XFS = 10_000
MAX_CELL_XFS = 10_000
MAX_NAMED_CELL_STYLES = 1_000
MAX_DXFS = 10_000
MAX_TABLE_STYLES = 1_000
MAX_CONDITIONAL_FORMATTING_RULES = 20_000
MAX_CONDITIONAL_FORMATTING_RANGES = 10_000
MAX_DATA_VALIDATIONS = 10_000
MAX_DEFINED_NAMES = 10_000
MAX_PIVOT_CACHES = 128
MAX_PIVOT_TABLES = 128
MAX_PIVOT_CACHE_RECORDS = 50_000
MAX_PIVOT_ITEMS = 200_000
MAX_CUSTOM_PROPERTIES = 1_000
MAX_ROW_DIMENSIONS = 50_000
MAX_COLUMN_DIMENSIONS = 10_000
MAX_PAGE_BREAKS = 10_000
MAX_SCENARIOS = 1_000
MAX_SHEET_VIEWS = 256
MAX_FILTER_ITEMS = 10_000
MAX_WORKBOOK_VIEWS = 256
MAX_EXTERNAL_REFERENCES = 128
MAX_COMMENT_AUTHORS = 10_000
MAX_XML_ELEMENTS_PER_PART = 200_000
MAX_TOTAL_XML_ELEMENTS = 1_000_000
MAX_PROJECTED_WIRE_BYTES = 8 * 1024 * 1024
MAX_PROJECTED_WORKBOOK_BYTES = 96 * 1024 * 1024

_SPREADSHEET_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_WORKSHEET_RELATIONSHIP = f"{_OFFICE_REL_NS}/worksheet"
_CHARTSHEET_RELATIONSHIP = f"{_OFFICE_REL_NS}/chartsheet"
_SHARED_STRINGS_RELATIONSHIP = f"{_OFFICE_REL_NS}/sharedStrings"
_STYLES_RELATIONSHIP = f"{_OFFICE_REL_NS}/styles"
_DRAWING_RELATIONSHIP = f"{_OFFICE_REL_NS}/drawing"
_CHART_RELATIONSHIP = f"{_OFFICE_REL_NS}/chart"
_IMAGE_RELATIONSHIP = f"{_OFFICE_REL_NS}/image"
_COMMENTS_RELATIONSHIP = f"{_OFFICE_REL_NS}/comments"
_TABLE_RELATIONSHIP = f"{_OFFICE_REL_NS}/table"
_HYPERLINK_RELATIONSHIP = f"{_OFFICE_REL_NS}/hyperlink"
_PIVOT_TABLE_RELATIONSHIP = f"{_OFFICE_REL_NS}/pivotTable"
_PIVOT_CACHE_DEFINITION_RELATIONSHIP = f"{_OFFICE_REL_NS}/pivotCacheDefinition"
_PIVOT_CACHE_RECORDS_RELATIONSHIP = f"{_OFFICE_REL_NS}/pivotCacheRecords"
_RELATIONSHIP_ID = f"{{{_OFFICE_REL_NS}}}id"
_RELATIONSHIP_TAG = f"{{{_PACKAGE_REL_NS}}}Relationship"
_A1_RANGE_RE = re.compile(r"^([A-Z]{1,3})([1-9][0-9]{0,6})(?::([A-Z]{1,3})([1-9][0-9]{0,6}))?$")
_MAX_COLUMN = 16_384
_MAX_ROW = 1_048_576
_DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
_DRAWING_MAIN_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_CHART_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
_CUSTOM_PROPERTIES_NS = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"


@dataclass(frozen=True, slots=True)
class XlsxUnsupportedObjectRef:
    sheet_index: int
    source_index: int
    kind: str
    relationship_id: str
    target: str


@dataclass(frozen=True, slots=True)
class XlsxPreflightSheet:
    sheet_index: int
    name: str
    kind: XlsxSheetKind
    state: XlsxSheetState
    part_name: str
    declared_cells: int
    serialized_cells: int
    non_empty_cells: int
    unsupported_objects: tuple[XlsxUnsupportedObjectRef, ...]


@dataclass(frozen=True, slots=True)
class XlsxPreflight:
    sheets: tuple[XlsxPreflightSheet, ...]
    date_1904: bool
    serialized_cells: int
    non_empty_cells: int
    native_text_chars: int
    projected_wire_bytes: int
    projected_workbook_bytes: int
    usage: XlsxResourceUsage


@dataclass(frozen=True, slots=True)
class XlsxResourceUsage:
    serialized_cells: int = 0
    non_empty_cells: int = 0
    materialized_grid_cells: int = 0
    merge_ranges: int = 0
    merge_footprint: int = 0
    shared_strings: int = 0
    shared_string_chars: int = 0
    tables: int = 0
    table_footprint: int = 0
    table_columns: int = 0
    hyperlinks_and_comments: int = 0
    hyperlink_footprint: int = 0
    drawing_objects: int = 0
    chart_cache_points: int = 0
    native_text_chars: int = 0
    style_records: int = 0
    number_formats: int = 0
    fonts: int = 0
    fills: int = 0
    borders: int = 0
    cell_style_xfs: int = 0
    cell_xfs: int = 0
    named_cell_styles: int = 0
    dxfs: int = 0
    table_styles: int = 0
    conditional_formatting_rules: int = 0
    conditional_formatting_ranges: int = 0
    data_validations: int = 0
    defined_names: int = 0
    pivot_caches: int = 0
    pivot_tables: int = 0
    pivot_cache_records: int = 0
    pivot_items: int = 0
    custom_properties: int = 0
    row_dimensions: int = 0
    column_dimensions: int = 0
    page_breaks: int = 0
    scenarios: int = 0
    sheet_views: int = 0
    filter_items: int = 0
    workbook_views: int = 0
    external_references: int = 0
    comment_authors: int = 0
    xml_elements: int = 0


@dataclass(slots=True)
class _ResourceUsage:
    serialized_cells: int = 0
    non_empty_cells: int = 0
    materialized_grid_cells: int = 0
    merge_ranges: int = 0
    merge_footprint: int = 0
    shared_strings: int = 0
    shared_string_chars: int = 0
    tables: int = 0
    table_footprint: int = 0
    table_columns: int = 0
    hyperlinks_and_comments: int = 0
    hyperlink_footprint: int = 0
    drawing_objects: int = 0
    chart_cache_points: int = 0
    native_text_chars: int = 0
    style_records: int = 0
    number_formats: int = 0
    fonts: int = 0
    fills: int = 0
    borders: int = 0
    cell_style_xfs: int = 0
    cell_xfs: int = 0
    named_cell_styles: int = 0
    dxfs: int = 0
    table_styles: int = 0
    conditional_formatting_rules: int = 0
    conditional_formatting_ranges: int = 0
    data_validations: int = 0
    defined_names: int = 0
    pivot_caches: int = 0
    pivot_tables: int = 0
    pivot_cache_records: int = 0
    pivot_items: int = 0
    custom_properties: int = 0
    row_dimensions: int = 0
    column_dimensions: int = 0
    page_breaks: int = 0
    scenarios: int = 0
    sheet_views: int = 0
    filter_items: int = 0
    workbook_views: int = 0
    external_references: int = 0
    comment_authors: int = 0
    xml_elements: int = 0

    def freeze(self) -> XlsxResourceUsage:
        return XlsxResourceUsage(
            **{name: getattr(self, name) for name in XlsxResourceUsage.__dataclass_fields__}
        )


@dataclass(frozen=True, slots=True)
class _Relationship:
    relationship_type: str
    target: str
    external: bool


@dataclass(frozen=True, slots=True)
class _WorksheetCounts:
    declared_cells: int
    serialized_cells: int
    non_empty_cells: int
    native_text_chars: int


def _increment(
    usage: _ResourceUsage,
    field_name: str,
    amount: int,
    *,
    limit: int,
    message: str,
) -> None:
    if amount < 0:
        raise ValueError("XLSX resource increments must be non-negative")
    value = getattr(usage, field_name) + amount
    if value > limit:
        raise LimitExceededError(message)
    setattr(usage, field_name, value)


def _add_native_text(usage: _ResourceUsage, characters: int) -> None:
    usage.native_text_chars += characters
    if usage.native_text_chars > MAX_NATIVE_TEXT_CHARS:
        raise LimitExceededError("XLSX exceeds the native text limit")


def _relationship_for(
    relationships: dict[str, _Relationship],
    relationship_id: str | None,
    *,
    expected_type: str,
) -> _Relationship:
    relationship = relationships.get(relationship_id or "")
    if relationship is None or relationship.relationship_type != expected_type:
        raise CorruptDocumentError("XLSX object relationship is invalid")
    return relationship


def _record_unsupported_object(
    unsupported_objects: list[XlsxUnsupportedObjectRef],
    *,
    sheet_index: int,
    relationship_id: str,
    relationship: _Relationship,
) -> None:
    unsupported_objects.append(
        XlsxUnsupportedObjectRef(
            sheet_index=sheet_index,
            source_index=len(unsupported_objects),
            kind=relationship.relationship_type.rsplit("/", 1)[-1] or "unknown",
            relationship_id=relationship_id,
            target=relationship.target,
        )
    )


def _column_number(label: str) -> int:
    number = 0
    for character in label:
        number = number * 26 + ord(character) - ord("A") + 1
    return number


def _parse_a1_range(value: str, *, message: str) -> tuple[int, int, int, int]:
    match = _A1_RANGE_RE.fullmatch(value)
    if match is None:
        raise CorruptDocumentError(message)
    start_column = _column_number(match.group(1))
    start_row = int(match.group(2))
    end_column = _column_number(match.group(3) or match.group(1))
    end_row = int(match.group(4) or match.group(2))
    if (
        start_column > _MAX_COLUMN
        or end_column > _MAX_COLUMN
        or start_row > _MAX_ROW
        or end_row > _MAX_ROW
        or end_column < start_column
        or end_row < start_row
    ):
        raise CorruptDocumentError(message)
    return start_column, start_row, end_column, end_row


def _area(bounds: tuple[int, int, int, int]) -> int:
    start_column, start_row, end_column, end_row = bounds
    return (end_column - start_column + 1) * (end_row - start_row + 1)


def _xml_events(stream: IO[bytes], *, message: str) -> Iterator[tuple[str, Any]]:
    try:
        yield from DefusedET.iterparse(
            stream,
            events=("start", "end"),
            forbid_dtd=True,
            forbid_entities=True,
            forbid_external=True,
        )
    except (DefusedXmlException, DefusedET.ParseError) as error:
        raise CorruptDocumentError(message) from error


def _preflight_all_xml_parts(
    archive: ZipFile,
    infos: dict[str, ZipInfo],
    usage: _ResourceUsage,
) -> None:
    for part_name in sorted(infos):
        if not (
            part_name.endswith(".xml")
            or part_name.endswith(".rels")
            or part_name == "[Content_Types].xml"
        ):
            continue
        part_elements = 0
        try:
            with archive.open(part_name) as stream:
                for event, element in _xml_events(
                    stream,
                    message=f"XLSX XML part is corrupt: {part_name}",
                ):
                    if event != "end":
                        continue
                    part_elements += 1
                    if part_elements > MAX_XML_ELEMENTS_PER_PART:
                        raise LimitExceededError("XLSX XML part exceeds the element limit")
                    usage.xml_elements += 1
                    if usage.xml_elements > MAX_TOTAL_XML_ELEMENTS:
                        raise LimitExceededError("XLSX exceeds the aggregate XML element limit")
                    element.clear()
        except (KeyError, OSError) as error:
            raise CorruptDocumentError(f"XLSX XML part is corrupt: {part_name}") from error


def _safe_relationship_target(source_part: str, target: str) -> str:
    if not target or target.startswith(("//", "\\")) or "\\" in target:
        raise CorruptDocumentError("XLSX relationship target is invalid")
    package_absolute = target.startswith("/")
    candidate = target[1:] if package_absolute else target
    if not candidate:
        raise CorruptDocumentError("XLSX relationship target is invalid")
    first_parts = PurePosixPath(candidate).parts[:1]
    if first_parts and ":" in first_parts[0]:
        raise CorruptDocumentError("XLSX relationship target is invalid")
    base = PurePosixPath(source_part).parent.as_posix()
    normalized = (
        posixpath.normpath(candidate)
        if package_absolute
        else posixpath.normpath(posixpath.join(base, candidate))
    )
    if normalized in {"", ".", ".."} or normalized.startswith(("../", "/")):
        raise CorruptDocumentError("XLSX relationship target is invalid")
    return normalized


def _relationships_part(source_part: str) -> str:
    path = PurePosixPath(source_part)
    return (path.parent / "_rels" / f"{path.name}.rels").as_posix()


def _read_relationships(
    archive: ZipFile,
    infos: dict[str, ZipInfo],
    source_part: str,
    *,
    required: bool,
) -> dict[str, _Relationship]:
    relationships_part = _relationships_part(source_part)
    if relationships_part not in infos:
        if required:
            raise CorruptDocumentError("XLSX relationships part is missing")
        return {}
    relationships: dict[str, _Relationship] = {}
    root_seen = False
    try:
        with archive.open(relationships_part) as stream:
            for event, element in _xml_events(stream, message="XLSX relationships part is corrupt"):
                if not root_seen and event == "start":
                    root_seen = True
                    if element.tag != f"{{{_PACKAGE_REL_NS}}}Relationships":
                        raise CorruptDocumentError("XLSX relationships namespace is invalid")
                if (
                    event == "end"
                    and isinstance(element.tag, str)
                    and element.tag.rsplit("}", 1)[-1] == "Relationship"
                    and element.tag != _RELATIONSHIP_TAG
                ):
                    raise CorruptDocumentError("XLSX relationships namespace is invalid")
                if event != "end" or element.tag != _RELATIONSHIP_TAG:
                    continue
                relationship_id = element.get("Id")
                relationship_type = element.get("Type")
                target = element.get("Target")
                target_mode = element.get("TargetMode")
                if (
                    not relationship_id
                    or not relationship_type
                    or not target
                    or target_mode not in {None, "External"}
                ):
                    raise CorruptDocumentError("XLSX relationship is malformed")
                if relationship_id in relationships:
                    raise CorruptDocumentError("XLSX relationship identifiers must be unique")
                external = target_mode == "External"
                normalized_target = (
                    target if external else _safe_relationship_target(source_part, target)
                )
                if not external and normalized_target not in infos:
                    raise CorruptDocumentError("XLSX relationship target is missing")
                relationships[relationship_id] = _Relationship(
                    relationship_type=relationship_type,
                    target=normalized_target,
                    external=external,
                )
                element.clear()
    except (KeyError, OSError) as error:
        raise CorruptDocumentError("XLSX relationships part is corrupt") from error
    if not root_seen:
        raise CorruptDocumentError("XLSX relationships part is corrupt")
    return relationships


def _parse_workbook(
    archive: ZipFile,
    infos: dict[str, ZipInfo],
    usage: _ResourceUsage,
) -> tuple[
    tuple[tuple[str, XlsxSheetKind, XlsxSheetState, str], ...],
    bool,
    tuple[str, ...],
    str | None,
    str | None,
]:
    relationships = _read_relationships(
        archive,
        infos,
        "xl/workbook.xml",
        required="xl/_rels/workbook.xml.rels" in infos,
    )
    sheet_entries: list[tuple[str, XlsxSheetKind, XlsxSheetState, str]] = []
    sheet_ids: set[int] = set()
    names: set[str] = set()
    pivot_cache_targets: list[str] = []
    date_1904 = False
    root_seen = False
    try:
        with archive.open("xl/workbook.xml") as stream:
            for event, element in _xml_events(stream, message="XLSX workbook part is corrupt"):
                if not root_seen and event == "start":
                    root_seen = True
                    if element.tag != f"{{{_SPREADSHEET_NS}}}workbook":
                        raise CorruptDocumentError("XLSX workbook namespace is invalid")
                if event != "end":
                    continue
                if element.tag == f"{{{_SPREADSHEET_NS}}}workbookPr":
                    raw_date = element.get("date1904")
                    if raw_date not in {None, "0", "1", "false", "true"}:
                        raise CorruptDocumentError("XLSX workbook date system is invalid")
                    date_1904 = raw_date in {"1", "true"}
                elif element.tag == f"{{{_SPREADSHEET_NS}}}definedName":
                    _increment(
                        usage,
                        "defined_names",
                        1,
                        limit=MAX_DEFINED_NAMES,
                        message="XLSX exceeds the defined name limit",
                    )
                    _add_native_text(usage, len(element.text or ""))
                    element.clear()
                elif element.tag == f"{{{_SPREADSHEET_NS}}}workbookView":
                    _increment(
                        usage,
                        "workbook_views",
                        1,
                        limit=MAX_WORKBOOK_VIEWS,
                        message="XLSX exceeds the workbook view limit",
                    )
                    element.clear()
                elif element.tag == f"{{{_SPREADSHEET_NS}}}externalReference":
                    _increment(
                        usage,
                        "external_references",
                        1,
                        limit=MAX_EXTERNAL_REFERENCES,
                        message="XLSX exceeds the external reference limit",
                    )
                    if element.get(_RELATIONSHIP_ID) not in relationships:
                        raise CorruptDocumentError("XLSX external reference is invalid")
                    element.clear()
                elif element.tag == f"{{{_SPREADSHEET_NS}}}pivotCache":
                    _increment(
                        usage,
                        "pivot_caches",
                        1,
                        limit=MAX_PIVOT_CACHES,
                        message="XLSX exceeds the pivot cache limit",
                    )
                    relationship = _relationship_for(
                        relationships,
                        element.get(_RELATIONSHIP_ID),
                        expected_type=_PIVOT_CACHE_DEFINITION_RELATIONSHIP,
                    )
                    if relationship.external:
                        raise CorruptDocumentError("XLSX pivot cache relationship is invalid")
                    pivot_cache_targets.append(relationship.target)
                    element.clear()
                elif element.tag == f"{{{_SPREADSHEET_NS}}}sheet":
                    if len(sheet_entries) >= MAX_SHEETS:
                        raise LimitExceededError("XLSX exceeds the sheet count limit")
                    name = element.get("name")
                    relationship_id = element.get(_RELATIONSHIP_ID)
                    raw_sheet_id = element.get("sheetId")
                    raw_state = element.get("state", XlsxSheetState.VISIBLE.value)
                    if not name or not relationship_id or not raw_sheet_id:
                        raise CorruptDocumentError("XLSX workbook sheet entry is malformed")
                    if (
                        len(name) > 31
                        or any(ord(character) < 32 for character in name)
                        or any(character in "[]:*?/\\" for character in name)
                    ):
                        raise CorruptDocumentError("XLSX workbook sheet name is invalid")
                    try:
                        sheet_id = int(raw_sheet_id)
                        state = XlsxSheetState(raw_state)
                    except ValueError as error:
                        raise CorruptDocumentError(
                            "XLSX workbook sheet entry is malformed"
                        ) from error
                    if sheet_id <= 0 or sheet_id in sheet_ids or name.casefold() in names:
                        raise CorruptDocumentError("XLSX workbook sheet entries must be unique")
                    relationship = relationships.get(relationship_id)
                    if relationship is None or relationship.external:
                        raise CorruptDocumentError("XLSX workbook sheet relationship is invalid")
                    if relationship.relationship_type == _WORKSHEET_RELATIONSHIP:
                        kind = XlsxSheetKind.WORKSHEET
                    elif relationship.relationship_type == _CHARTSHEET_RELATIONSHIP:
                        kind = XlsxSheetKind.CHARTSHEET
                    else:
                        raise CorruptDocumentError(
                            "XLSX workbook sheet relationship type is invalid"
                        )
                    sheet_ids.add(sheet_id)
                    names.add(name.casefold())
                    sheet_entries.append((name, kind, state, relationship.target))
                    element.clear()
    except (KeyError, OSError) as error:
        raise CorruptDocumentError("XLSX workbook part is corrupt") from error
    if not root_seen:
        raise CorruptDocumentError("XLSX workbook part is corrupt")
    if sheet_entries and not relationships:
        raise CorruptDocumentError("XLSX relationships part is missing")
    if any(
        relationship.external
        for relationship in relationships.values()
        if relationship.relationship_type
        in {
            _SHARED_STRINGS_RELATIONSHIP,
            _STYLES_RELATIONSHIP,
        }
    ):
        raise CorruptDocumentError("XLSX workbook singleton relationship is external")
    shared_string_targets = tuple(
        relationship.target
        for relationship in relationships.values()
        if relationship.relationship_type == _SHARED_STRINGS_RELATIONSHIP
        and not relationship.external
    )
    style_targets = tuple(
        relationship.target
        for relationship in relationships.values()
        if relationship.relationship_type == _STYLES_RELATIONSHIP and not relationship.external
    )
    if len(shared_string_targets) > 1 or len(style_targets) > 1:
        raise CorruptDocumentError("XLSX workbook singleton relationships are duplicated")
    shared_strings_part = (
        shared_string_targets[0]
        if shared_string_targets
        else "xl/sharedStrings.xml"
        if "xl/sharedStrings.xml" in infos
        else None
    )
    styles_part = (
        style_targets[0] if style_targets else "xl/styles.xml" if "xl/styles.xml" in infos else None
    )
    return (
        tuple(sheet_entries),
        date_1904,
        tuple(pivot_cache_targets),
        shared_strings_part,
        styles_part,
    )


def _preflight_shared_strings(
    archive: ZipFile,
    part_name: str | None,
    usage: _ResourceUsage,
) -> None:
    if part_name is None:
        return
    root_seen = False
    try:
        with archive.open(part_name) as stream:
            for event, element in _xml_events(
                stream, message="XLSX shared strings part is corrupt"
            ):
                if not root_seen and event == "start":
                    root_seen = True
                    if element.tag != f"{{{_SPREADSHEET_NS}}}sst":
                        raise CorruptDocumentError("XLSX shared strings namespace is invalid")
                if event == "end" and element.tag == f"{{{_SPREADSHEET_NS}}}si":
                    _increment(
                        usage,
                        "shared_strings",
                        1,
                        limit=MAX_SHARED_STRINGS,
                        message="XLSX exceeds the shared string item limit",
                    )
                    characters = sum(
                        len(node.text or "") for node in element.iter(f"{{{_SPREADSHEET_NS}}}t")
                    )
                    _increment(
                        usage,
                        "shared_string_chars",
                        characters,
                        limit=MAX_SHARED_STRING_CHARS,
                        message="XLSX exceeds the shared string text limit",
                    )
                    _add_native_text(usage, characters)
                    element.clear()
    except (KeyError, OSError) as error:
        raise CorruptDocumentError("XLSX shared strings part is corrupt") from error
    if not root_seen:
        raise CorruptDocumentError("XLSX shared strings part is corrupt")


def _preflight_styles(
    archive: ZipFile,
    part_name: str | None,
    usage: _ResourceUsage,
) -> None:
    if part_name is None:
        return
    containers = {
        "numFmts": ("number_formats", MAX_NUMBER_FORMATS, "number format"),
        "fonts": ("fonts", MAX_FONTS, "font"),
        "fills": ("fills", MAX_FILLS, "fill"),
        "borders": ("borders", MAX_BORDERS, "border"),
        "cellStyleXfs": ("cell_style_xfs", MAX_CELL_STYLE_XFS, "cellStyleXfs"),
        "cellXfs": ("cell_xfs", MAX_CELL_XFS, "cellXfs"),
        "cellStyles": ("named_cell_styles", MAX_NAMED_CELL_STYLES, "named style"),
        "dxfs": ("dxfs", MAX_DXFS, "dxf"),
        "tableStyles": ("table_styles", MAX_TABLE_STYLES, "table style"),
    }
    root_seen = False
    try:
        with archive.open(part_name) as stream:
            for event, element in _xml_events(stream, message="XLSX stylesheet part is corrupt"):
                if not root_seen and event == "start":
                    root_seen = True
                    if element.tag != f"{{{_SPREADSHEET_NS}}}styleSheet":
                        raise CorruptDocumentError("XLSX stylesheet namespace is invalid")
                if event != "end" or not isinstance(element.tag, str):
                    continue
                local_name = element.tag.rsplit("}", 1)[-1]
                details = containers.get(local_name)
                if details is None or not element.tag.startswith(f"{{{_SPREADSHEET_NS}}}"):
                    continue
                field_name, limit, label = details
                count = len(element)
                _increment(
                    usage,
                    field_name,
                    count,
                    limit=limit,
                    message=f"XLSX exceeds the {label} limit",
                )
                _increment(
                    usage,
                    "style_records",
                    count,
                    limit=MAX_STYLE_RECORDS,
                    message="XLSX exceeds the aggregate style record limit",
                )
                element.clear()
    except (KeyError, OSError) as error:
        raise CorruptDocumentError("XLSX stylesheet part is corrupt") from error
    if not root_seen:
        raise CorruptDocumentError("XLSX stylesheet part is corrupt")


def _preflight_custom_properties(
    archive: ZipFile,
    infos: dict[str, ZipInfo],
    usage: _ResourceUsage,
) -> None:
    part_name = "docProps/custom.xml"
    if part_name not in infos:
        return
    root_seen = False
    try:
        with archive.open(part_name) as stream:
            for event, element in _xml_events(stream, message="XLSX custom properties are corrupt"):
                if not root_seen and event == "start":
                    root_seen = True
                    if element.tag != f"{{{_CUSTOM_PROPERTIES_NS}}}Properties":
                        raise CorruptDocumentError("XLSX custom properties namespace is invalid")
                if event == "end" and element.tag == f"{{{_CUSTOM_PROPERTIES_NS}}}property":
                    _increment(
                        usage,
                        "custom_properties",
                        1,
                        limit=MAX_CUSTOM_PROPERTIES,
                        message="XLSX exceeds the custom property limit",
                    )
                    characters = sum(len(node.text or "") for node in element.iter())
                    _add_native_text(usage, characters)
                    element.clear()
    except (KeyError, OSError) as error:
        raise CorruptDocumentError("XLSX custom properties are corrupt") from error
    if not root_seen:
        raise CorruptDocumentError("XLSX custom properties are corrupt")


def _preflight_table(
    archive: ZipFile,
    part_name: str,
    usage: _ResourceUsage,
) -> int:
    root_seen = False
    table_reference: str | None = None
    try:
        with archive.open(part_name) as stream:
            for event, element in _xml_events(stream, message="XLSX table part is corrupt"):
                if not root_seen and event == "start":
                    root_seen = True
                    if element.tag != f"{{{_SPREADSHEET_NS}}}table":
                        raise CorruptDocumentError("XLSX table namespace is invalid")
                    table_reference = element.get("ref")
                if event == "end" and element.tag == f"{{{_SPREADSHEET_NS}}}tableColumn":
                    _increment(
                        usage,
                        "table_columns",
                        1,
                        limit=MAX_TABLE_COLUMNS,
                        message="XLSX exceeds the table column limit",
                    )
                    element.clear()
    except (KeyError, OSError) as error:
        raise CorruptDocumentError("XLSX table part is corrupt") from error
    if not root_seen or table_reference is None:
        raise CorruptDocumentError("XLSX table part is corrupt")
    footprint = _area(_parse_a1_range(table_reference, message="XLSX table range is invalid"))
    _increment(
        usage,
        "table_footprint",
        footprint,
        limit=MAX_TABLE_FOOTPRINT,
        message="XLSX exceeds the table footprint limit",
    )
    return footprint


def _preflight_comments(
    archive: ZipFile,
    part_name: str,
    usage: _ResourceUsage,
) -> None:
    root_seen = False
    try:
        with archive.open(part_name) as stream:
            for event, element in _xml_events(stream, message="XLSX comments part is corrupt"):
                if not root_seen and event == "start":
                    root_seen = True
                    if element.tag != f"{{{_SPREADSHEET_NS}}}comments":
                        raise CorruptDocumentError("XLSX comments namespace is invalid")
                if event == "end" and element.tag == f"{{{_SPREADSHEET_NS}}}comment":
                    reference = element.get("ref")
                    if reference is None:
                        raise CorruptDocumentError("XLSX comment anchor is invalid")
                    bounds = _parse_a1_range(reference, message="XLSX comment anchor is invalid")
                    if _area(bounds) != 1:
                        raise CorruptDocumentError("XLSX comment anchor is invalid")
                    _increment(
                        usage,
                        "hyperlinks_and_comments",
                        1,
                        limit=MAX_HYPERLINKS_AND_COMMENTS,
                        message="XLSX exceeds the hyperlink and comment limit",
                    )
                    characters = sum(
                        len(node.text or "") for node in element.iter(f"{{{_SPREADSHEET_NS}}}t")
                    )
                    _add_native_text(usage, characters)
                    element.clear()
                elif event == "end" and element.tag == f"{{{_SPREADSHEET_NS}}}author":
                    _increment(
                        usage,
                        "comment_authors",
                        1,
                        limit=MAX_COMMENT_AUTHORS,
                        message="XLSX exceeds the comment author limit",
                    )
                    _add_native_text(usage, len(element.text or ""))
                    element.clear()
    except (KeyError, OSError) as error:
        raise CorruptDocumentError("XLSX comments part is corrupt") from error
    if not root_seen:
        raise CorruptDocumentError("XLSX comments part is corrupt")


def _preflight_chart(
    archive: ZipFile,
    part_name: str,
    usage: _ResourceUsage,
) -> None:
    root_seen = False
    try:
        with archive.open(part_name) as stream:
            for event, element in _xml_events(stream, message="XLSX chart part is corrupt"):
                if not root_seen and event == "start":
                    root_seen = True
                    if element.tag != f"{{{_CHART_NS}}}chartSpace":
                        raise CorruptDocumentError("XLSX chart namespace is invalid")
                if event != "end":
                    continue
                if element.tag == f"{{{_CHART_NS}}}pt":
                    _increment(
                        usage,
                        "chart_cache_points",
                        1,
                        limit=MAX_CHART_CACHE_POINTS,
                        message="XLSX exceeds the chart cache point limit",
                    )
                elif element.tag == f"{{{_CHART_NS}}}ptCount":
                    raw_count = element.get("val")
                    try:
                        declared_count = int(raw_count or "")
                    except ValueError as error:
                        raise CorruptDocumentError("XLSX chart cache count is invalid") from error
                    if declared_count < 0 or declared_count > MAX_CHART_CACHE_POINTS:
                        raise LimitExceededError("XLSX exceeds the chart cache point limit")
                elif element.tag in {
                    f"{{{_CHART_NS}}}v",
                    f"{{{_CHART_NS}}}f",
                    f"{{{_DRAWING_MAIN_NS}}}t",
                }:
                    _add_native_text(usage, len(element.text or ""))
                element.clear()
    except (KeyError, OSError) as error:
        raise CorruptDocumentError("XLSX chart part is corrupt") from error
    if not root_seen:
        raise CorruptDocumentError("XLSX chart part is corrupt")


def _require_anchor_index(value: str | None, *, maximum: int) -> int:
    try:
        index = int(value or "")
    except ValueError as error:
        raise CorruptDocumentError("XLSX drawing anchor is invalid") from error
    if index < 0 or index >= maximum:
        raise CorruptDocumentError("XLSX drawing anchor is invalid")
    return index


def _validate_drawing_anchor(anchor: Any, *, allow_zero_absolute_extent: bool) -> None:
    local_name = anchor.tag.rsplit("}", 1)[-1]
    if local_name in {"oneCellAnchor", "twoCellAnchor"}:
        starts = anchor.findall(f"{{{_DRAWING_NS}}}from")
        if len(starts) != 1:
            raise CorruptDocumentError("XLSX drawing anchor is invalid")
        markers = starts
        if local_name == "twoCellAnchor":
            ends = anchor.findall(f"{{{_DRAWING_NS}}}to")
            if len(ends) != 1:
                raise CorruptDocumentError("XLSX drawing anchor is invalid")
            markers = [*markers, *ends]
        for marker in markers:
            _require_anchor_index(
                marker.findtext(f"{{{_DRAWING_NS}}}col"),
                maximum=_MAX_COLUMN,
            )
            _require_anchor_index(
                marker.findtext(f"{{{_DRAWING_NS}}}row"),
                maximum=_MAX_ROW,
            )
        if local_name == "oneCellAnchor":
            extents = anchor.findall(f"{{{_DRAWING_NS}}}ext")
            if len(extents) != 1:
                raise CorruptDocumentError("XLSX drawing anchor is invalid")
            try:
                cx = int(extents[0].get("cx", ""))
                cy = int(extents[0].get("cy", ""))
            except ValueError as error:
                raise CorruptDocumentError("XLSX drawing anchor is invalid") from error
            if cx <= 0 or cy <= 0:
                raise CorruptDocumentError("XLSX drawing anchor is invalid")
    elif local_name == "absoluteAnchor":
        positions = anchor.findall(f"{{{_DRAWING_NS}}}pos")
        extents = anchor.findall(f"{{{_DRAWING_NS}}}ext")
        if len(positions) != 1 or len(extents) != 1:
            raise CorruptDocumentError("XLSX drawing anchor is invalid")
        try:
            values = tuple(
                int(value)
                for value in (
                    positions[0].get("x", ""),
                    positions[0].get("y", ""),
                    extents[0].get("cx", ""),
                    extents[0].get("cy", ""),
                )
            )
        except ValueError as error:
            raise CorruptDocumentError("XLSX drawing anchor is invalid") from error
        invalid_extent = (
            values[2] < 0 or values[3] < 0
            if allow_zero_absolute_extent
            else values[2] <= 0 or values[3] <= 0
        )
        if values[0] < 0 or values[1] < 0 or invalid_extent:
            raise CorruptDocumentError("XLSX drawing anchor is invalid")
    else:
        raise CorruptDocumentError("XLSX drawing anchor is invalid")


def _preflight_drawing(
    archive: ZipFile,
    infos: dict[str, ZipInfo],
    part_name: str,
    usage: _ResourceUsage,
    visited_charts: set[str],
    sheet_index: int,
    unsupported_objects: list[XlsxUnsupportedObjectRef],
    *,
    allow_zero_absolute_extent: bool = False,
) -> None:
    relationships = _read_relationships(
        archive,
        infos,
        part_name,
        required=_relationships_part(part_name) in infos,
    )
    root_seen = False
    try:
        with archive.open(part_name) as stream:
            for event, element in _xml_events(stream, message="XLSX drawing part is corrupt"):
                if not root_seen and event == "start":
                    root_seen = True
                    if element.tag != f"{{{_DRAWING_NS}}}wsDr":
                        raise CorruptDocumentError("XLSX drawing namespace is invalid")
                if event != "end" or element.tag not in {
                    f"{{{_DRAWING_NS}}}oneCellAnchor",
                    f"{{{_DRAWING_NS}}}twoCellAnchor",
                    f"{{{_DRAWING_NS}}}absoluteAnchor",
                }:
                    continue
                _validate_drawing_anchor(
                    element,
                    allow_zero_absolute_extent=allow_zero_absolute_extent,
                )
                _increment(
                    usage,
                    "drawing_objects",
                    1,
                    limit=MAX_DRAWING_OBJECTS,
                    message="XLSX exceeds the drawing object limit",
                )
                _add_native_text(
                    usage,
                    sum(len(node.text or "") for node in element.iter(f"{{{_DRAWING_MAIN_NS}}}t")),
                )
                for node in element.iter():
                    for attribute_name, relationship_id in node.attrib.items():
                        if not attribute_name.startswith(f"{{{_OFFICE_REL_NS}}}"):
                            continue
                        relationship = relationships.get(relationship_id)
                        if relationship is None or relationship.external:
                            raise CorruptDocumentError("XLSX drawing relationship is invalid")
                        local_attribute = attribute_name.rsplit("}", 1)[-1]
                        local_tag = node.tag.rsplit("}", 1)[-1]
                        expected_type = None
                        if local_tag == "chart" and local_attribute == "id":
                            expected_type = _CHART_RELATIONSHIP
                        elif local_tag == "blip" and local_attribute in {"embed", "link"}:
                            expected_type = _IMAGE_RELATIONSHIP
                        if (
                            expected_type is not None
                            and relationship.relationship_type != expected_type
                        ):
                            raise CorruptDocumentError("XLSX drawing relationship type is invalid")
                        if (
                            expected_type == _CHART_RELATIONSHIP
                            and relationship.target not in visited_charts
                        ):
                            visited_charts.add(relationship.target)
                            _preflight_chart(archive, relationship.target, usage)
                element.clear()
    except (KeyError, OSError) as error:
        raise CorruptDocumentError("XLSX drawing part is corrupt") from error
    if not root_seen:
        raise CorruptDocumentError("XLSX drawing part is corrupt")
    known_relationship_types = {
        _CHART_RELATIONSHIP,
        _IMAGE_RELATIONSHIP,
        _HYPERLINK_RELATIONSHIP,
    }
    for relationship_id, relationship in relationships.items():
        if relationship.relationship_type not in known_relationship_types:
            _add_native_text(usage, len(relationship.target))
            _record_unsupported_object(
                unsupported_objects,
                sheet_index=sheet_index,
                relationship_id=relationship_id,
                relationship=relationship,
            )


def _preflight_pivot_cache(
    archive: ZipFile,
    infos: dict[str, ZipInfo],
    part_name: str,
    usage: _ResourceUsage,
) -> None:
    relationships = _read_relationships(
        archive,
        infos,
        part_name,
        required=False,
    )
    root_seen = False
    try:
        with archive.open(part_name) as stream:
            for event, element in _xml_events(stream, message="XLSX pivot cache part is corrupt"):
                if not root_seen and event == "start":
                    root_seen = True
                    if element.tag != f"{{{_SPREADSHEET_NS}}}pivotCacheDefinition":
                        raise CorruptDocumentError("XLSX pivot cache namespace is invalid")
                if event == "end" and element.tag in {
                    f"{{{_SPREADSHEET_NS}}}cacheField",
                    f"{{{_SPREADSHEET_NS}}}s",
                    f"{{{_SPREADSHEET_NS}}}n",
                    f"{{{_SPREADSHEET_NS}}}d",
                    f"{{{_SPREADSHEET_NS}}}b",
                    f"{{{_SPREADSHEET_NS}}}e",
                    f"{{{_SPREADSHEET_NS}}}m",
                }:
                    _increment(
                        usage,
                        "pivot_items",
                        1,
                        limit=MAX_PIVOT_ITEMS,
                        message="XLSX exceeds the pivot item limit",
                    )
                    element.clear()
    except (KeyError, OSError) as error:
        raise CorruptDocumentError("XLSX pivot cache part is corrupt") from error
    if not root_seen:
        raise CorruptDocumentError("XLSX pivot cache part is corrupt")
    for relationship in relationships.values():
        if relationship.relationship_type != _PIVOT_CACHE_RECORDS_RELATIONSHIP:
            continue
        _preflight_pivot_records(archive, relationship.target, usage)


def _preflight_pivot_records(
    archive: ZipFile,
    part_name: str,
    usage: _ResourceUsage,
) -> None:
    root_seen = False
    try:
        with archive.open(part_name) as stream:
            for event, element in _xml_events(stream, message="XLSX pivot records are corrupt"):
                if not root_seen and event == "start":
                    root_seen = True
                    if element.tag != f"{{{_SPREADSHEET_NS}}}pivotCacheRecords":
                        raise CorruptDocumentError("XLSX pivot records namespace is invalid")
                if event == "end" and element.tag == f"{{{_SPREADSHEET_NS}}}r":
                    _increment(
                        usage,
                        "pivot_cache_records",
                        1,
                        limit=MAX_PIVOT_CACHE_RECORDS,
                        message="XLSX exceeds the pivot cache record limit",
                    )
                    element.clear()
    except (KeyError, OSError) as error:
        raise CorruptDocumentError("XLSX pivot records are corrupt") from error
    if not root_seen:
        raise CorruptDocumentError("XLSX pivot records are corrupt")


def _worksheet_counts(
    archive: ZipFile,
    infos: dict[str, ZipInfo],
    part_name: str,
    usage: _ResourceUsage,
    visited_drawings: set[str],
    visited_charts: set[str],
    sheet_index: int,
    unsupported_objects: list[XlsxUnsupportedObjectRef],
) -> _WorksheetCounts:
    relationships = _read_relationships(
        archive,
        infos,
        part_name,
        required=False,
    )
    declared_bounds: tuple[int, int, int, int] | None = None
    serialized_cells = 0
    non_empty_cells = 0
    native_text_chars = 0
    actual_columns: list[int] = []
    actual_rows: list[int] = []
    seen_cells: set[tuple[int, int]] = set()
    drawing_targets: list[str] = []
    table_targets: list[str] = []
    pivot_targets: list[str] = []
    root_seen = False
    try:
        with archive.open(part_name) as stream:
            for event, element in _xml_events(stream, message="XLSX worksheet part is corrupt"):
                if not root_seen and event == "start":
                    root_seen = True
                    if element.tag != f"{{{_SPREADSHEET_NS}}}worksheet":
                        raise CorruptDocumentError("XLSX worksheet namespace is invalid")
                if event != "end":
                    continue
                if element.tag == f"{{{_SPREADSHEET_NS}}}dimension":
                    reference = element.get("ref")
                    if reference is None:
                        raise CorruptDocumentError("XLSX worksheet dimension is invalid")
                    declared_bounds = _parse_a1_range(
                        reference,
                        message="XLSX worksheet dimension is invalid",
                    )
                    if _area(declared_bounds) > MAX_DECLARED_CELLS:
                        raise LimitExceededError(
                            "XLSX worksheet exceeds the declared dimension limit"
                        )
                    element.clear()
                elif element.tag == f"{{{_SPREADSHEET_NS}}}c":
                    serialized_cells += 1
                    if serialized_cells > MAX_SERIALIZED_CELLS:
                        raise LimitExceededError("XLSX exceeds the serialized cell limit")
                    reference = element.get("r")
                    if reference is None:
                        raise CorruptDocumentError("XLSX cell coordinate is missing")
                    column, row, end_column, end_row = _parse_a1_range(
                        reference,
                        message="XLSX cell coordinate is invalid",
                    )
                    if column != end_column or row != end_row:
                        raise CorruptDocumentError("XLSX cell coordinate is invalid")
                    coordinate = (row, column)
                    if coordinate in seen_cells:
                        raise CorruptDocumentError("XLSX cell coordinates must be unique")
                    seen_cells.add(coordinate)
                    actual_columns.append(column)
                    actual_rows.append(row)
                    value_nodes = tuple(
                        child
                        for child in element.iter()
                        if child is not element
                        and child.tag
                        in {
                            f"{{{_SPREADSHEET_NS}}}v",
                            f"{{{_SPREADSHEET_NS}}}f",
                            f"{{{_SPREADSHEET_NS}}}t",
                        }
                    )
                    if any((node.text or "") != "" for node in value_nodes):
                        non_empty_cells += 1
                        if non_empty_cells > MAX_NON_EMPTY_CELLS:
                            raise LimitExceededError("XLSX exceeds the non-empty cell limit")
                    native_text_chars += sum(len(node.text or "") for node in value_nodes)
                    if native_text_chars > MAX_NATIVE_TEXT_CHARS:
                        raise LimitExceededError("XLSX exceeds the native text limit")
                    if element.get("t") == "s":
                        value_node = element.find(f"{{{_SPREADSHEET_NS}}}v")
                        try:
                            shared_string_index = int(
                                value_node.text if value_node is not None else ""
                            )
                        except (TypeError, ValueError) as error:
                            raise CorruptDocumentError(
                                "XLSX shared string index is invalid"
                            ) from error
                        if not 0 <= shared_string_index < usage.shared_strings:
                            raise CorruptDocumentError("XLSX shared string index is invalid")
                    raw_style = element.get("s")
                    if raw_style is not None:
                        try:
                            style_index = int(raw_style)
                        except ValueError as error:
                            raise CorruptDocumentError(
                                "XLSX cell style index is invalid"
                            ) from error
                        if style_index < 0 or style_index >= usage.cell_xfs:
                            raise CorruptDocumentError("XLSX cell style index is invalid")
                    _increment(
                        usage,
                        "materialized_grid_cells",
                        1,
                        limit=MAX_MATERIALIZED_GRID_CELLS,
                        message="XLSX exceeds the materialized grid limit",
                    )
                    element.clear()
                elif element.tag == f"{{{_SPREADSHEET_NS}}}mergeCell":
                    reference = element.get("ref")
                    if reference is None:
                        raise CorruptDocumentError("XLSX merge range is invalid")
                    footprint = _area(
                        _parse_a1_range(reference, message="XLSX merge range is invalid")
                    )
                    _increment(
                        usage,
                        "merge_ranges",
                        1,
                        limit=MAX_MERGE_RANGES,
                        message="XLSX exceeds the merge range limit",
                    )
                    _increment(
                        usage,
                        "merge_footprint",
                        footprint,
                        limit=MAX_MERGE_FOOTPRINT,
                        message="XLSX exceeds the merge footprint limit",
                    )
                    _increment(
                        usage,
                        "materialized_grid_cells",
                        footprint,
                        limit=MAX_MATERIALIZED_GRID_CELLS,
                        message="XLSX exceeds the materialized grid limit",
                    )
                    element.clear()
                elif element.tag == f"{{{_SPREADSHEET_NS}}}cfRule":
                    _increment(
                        usage,
                        "conditional_formatting_rules",
                        1,
                        limit=MAX_CONDITIONAL_FORMATTING_RULES,
                        message="XLSX exceeds the conditional formatting rule limit",
                    )
                    element.clear()
                elif element.tag == f"{{{_SPREADSHEET_NS}}}conditionalFormatting":
                    _increment(
                        usage,
                        "conditional_formatting_ranges",
                        1,
                        limit=MAX_CONDITIONAL_FORMATTING_RANGES,
                        message="XLSX exceeds the conditional formatting range limit",
                    )
                    element.clear()
                elif element.tag == f"{{{_SPREADSHEET_NS}}}dataValidation":
                    _increment(
                        usage,
                        "data_validations",
                        1,
                        limit=MAX_DATA_VALIDATIONS,
                        message="XLSX exceeds the data validation limit",
                    )
                    element.clear()
                elif element.tag == f"{{{_SPREADSHEET_NS}}}row":
                    raw_row = element.get("r")
                    if raw_row is not None:
                        try:
                            row_number = int(raw_row)
                        except ValueError as error:
                            raise CorruptDocumentError("XLSX row index is invalid") from error
                        if not 1 <= row_number <= _MAX_ROW:
                            raise CorruptDocumentError("XLSX row index is invalid")
                    if set(element.attrib) - {"r", "spans"}:
                        _increment(
                            usage,
                            "row_dimensions",
                            1,
                            limit=MAX_ROW_DIMENSIONS,
                            message="XLSX exceeds the row dimension limit",
                        )
                    element.clear()
                elif element.tag == f"{{{_SPREADSHEET_NS}}}col":
                    try:
                        minimum_column = int(element.get("min", ""))
                        maximum_column = int(element.get("max", ""))
                    except ValueError as error:
                        raise CorruptDocumentError("XLSX column range is invalid") from error
                    if (
                        not 1 <= minimum_column <= _MAX_COLUMN
                        or not minimum_column <= maximum_column <= _MAX_COLUMN
                    ):
                        raise CorruptDocumentError("XLSX column range is invalid")
                    _increment(
                        usage,
                        "column_dimensions",
                        1,
                        limit=MAX_COLUMN_DIMENSIONS,
                        message="XLSX exceeds the column dimension limit",
                    )
                    element.clear()
                elif element.tag == f"{{{_SPREADSHEET_NS}}}brk":
                    _increment(
                        usage,
                        "page_breaks",
                        1,
                        limit=MAX_PAGE_BREAKS,
                        message="XLSX exceeds the page break limit",
                    )
                    element.clear()
                elif element.tag == f"{{{_SPREADSHEET_NS}}}scenario":
                    _increment(
                        usage,
                        "scenarios",
                        1,
                        limit=MAX_SCENARIOS,
                        message="XLSX exceeds the scenario limit",
                    )
                    element.clear()
                elif element.tag == f"{{{_SPREADSHEET_NS}}}sheetView":
                    _increment(
                        usage,
                        "sheet_views",
                        1,
                        limit=MAX_SHEET_VIEWS,
                        message="XLSX exceeds the sheet view limit",
                    )
                    element.clear()
                elif element.tag in {
                    f"{{{_SPREADSHEET_NS}}}filterColumn",
                    f"{{{_SPREADSHEET_NS}}}customFilter",
                    f"{{{_SPREADSHEET_NS}}}filter",
                }:
                    _increment(
                        usage,
                        "filter_items",
                        1,
                        limit=MAX_FILTER_ITEMS,
                        message="XLSX exceeds the filter item limit",
                    )
                    element.clear()
                elif element.tag in {
                    f"{{{_SPREADSHEET_NS}}}oddHeader",
                    f"{{{_SPREADSHEET_NS}}}oddFooter",
                    f"{{{_SPREADSHEET_NS}}}evenHeader",
                    f"{{{_SPREADSHEET_NS}}}evenFooter",
                    f"{{{_SPREADSHEET_NS}}}firstHeader",
                    f"{{{_SPREADSHEET_NS}}}firstFooter",
                }:
                    _add_native_text(usage, len(element.text or ""))
                    element.clear()
                elif element.tag == f"{{{_SPREADSHEET_NS}}}hyperlink":
                    reference = element.get("ref")
                    if reference is None:
                        raise CorruptDocumentError("XLSX hyperlink anchor is invalid")
                    footprint = _area(
                        _parse_a1_range(reference, message="XLSX hyperlink anchor is invalid")
                    )
                    _increment(
                        usage,
                        "hyperlinks_and_comments",
                        1,
                        limit=MAX_HYPERLINKS_AND_COMMENTS,
                        message="XLSX exceeds the hyperlink and comment limit",
                    )
                    _increment(
                        usage,
                        "hyperlink_footprint",
                        footprint,
                        limit=MAX_HYPERLINK_FOOTPRINT,
                        message="XLSX exceeds the hyperlink footprint limit",
                    )
                    _increment(
                        usage,
                        "materialized_grid_cells",
                        footprint,
                        limit=MAX_MATERIALIZED_GRID_CELLS,
                        message="XLSX exceeds the materialized grid limit",
                    )
                    relationship_id = element.get(_RELATIONSHIP_ID)
                    if relationship_id is not None:
                        relationship = _relationship_for(
                            relationships,
                            relationship_id,
                            expected_type=_HYPERLINK_RELATIONSHIP,
                        )
                        _add_native_text(usage, len(relationship.target))
                    _add_native_text(
                        usage,
                        sum(
                            len(element.get(attribute, ""))
                            for attribute in ("display", "location", "tooltip")
                        ),
                    )
                    element.clear()
                elif element.tag == f"{{{_SPREADSHEET_NS}}}drawing":
                    relationship = _relationship_for(
                        relationships,
                        element.get(_RELATIONSHIP_ID),
                        expected_type=_DRAWING_RELATIONSHIP,
                    )
                    if relationship.external:
                        raise CorruptDocumentError("XLSX drawing relationship is invalid")
                    drawing_targets.append(relationship.target)
                    element.clear()
                elif element.tag == f"{{{_SPREADSHEET_NS}}}tablePart":
                    relationship = _relationship_for(
                        relationships,
                        element.get(_RELATIONSHIP_ID),
                        expected_type=_TABLE_RELATIONSHIP,
                    )
                    if relationship.external:
                        raise CorruptDocumentError("XLSX table relationship is invalid")
                    _increment(
                        usage,
                        "tables",
                        1,
                        limit=MAX_TABLES,
                        message="XLSX exceeds the table count limit",
                    )
                    table_targets.append(relationship.target)
                    element.clear()
    except (KeyError, OSError) as error:
        raise CorruptDocumentError("XLSX worksheet part is corrupt") from error
    if not root_seen:
        raise CorruptDocumentError("XLSX worksheet part is corrupt")
    for relationship in relationships.values():
        if relationship.relationship_type == _COMMENTS_RELATIONSHIP:
            if relationship.external:
                raise CorruptDocumentError("XLSX comments relationship is invalid")
            _preflight_comments(archive, relationship.target, usage)
        elif relationship.relationship_type == _PIVOT_TABLE_RELATIONSHIP:
            if relationship.external:
                raise CorruptDocumentError("XLSX pivot table relationship is invalid")
            pivot_targets.append(relationship.target)
    for target in table_targets:
        footprint = _preflight_table(archive, target, usage)
        _increment(
            usage,
            "materialized_grid_cells",
            footprint,
            limit=MAX_MATERIALIZED_GRID_CELLS,
            message="XLSX exceeds the materialized grid limit",
        )
    for target in drawing_targets:
        if target not in visited_drawings:
            visited_drawings.add(target)
            _preflight_drawing(
                archive,
                infos,
                target,
                usage,
                visited_charts,
                sheet_index,
                unsupported_objects,
            )
    for target in pivot_targets:
        _increment(
            usage,
            "pivot_tables",
            1,
            limit=MAX_PIVOT_TABLES,
            message="XLSX exceeds the pivot table limit",
        )
        _preflight_xml_root(
            archive,
            target,
            expected_tag=f"{{{_SPREADSHEET_NS}}}pivotTableDefinition",
            message="XLSX pivot table part is corrupt",
        )
    known_relationship_types = {
        _DRAWING_RELATIONSHIP,
        _COMMENTS_RELATIONSHIP,
        _TABLE_RELATIONSHIP,
        _HYPERLINK_RELATIONSHIP,
        _PIVOT_TABLE_RELATIONSHIP,
    }
    for relationship_id, relationship in relationships.items():
        if relationship.relationship_type in known_relationship_types:
            continue
        _increment(
            usage,
            "drawing_objects",
            1,
            limit=MAX_DRAWING_OBJECTS,
            message="XLSX exceeds the drawing object limit",
        )
        _add_native_text(usage, len(relationship.target))
        _record_unsupported_object(
            unsupported_objects,
            sheet_index=sheet_index,
            relationship_id=relationship_id,
            relationship=relationship,
        )
    if actual_columns:
        actual_bounds = (
            min(actual_columns),
            min(actual_rows),
            max(actual_columns),
            max(actual_rows),
        )
        if _area(actual_bounds) > MAX_DECLARED_CELLS:
            raise LimitExceededError("XLSX worksheet actual coordinates exceed the resource budget")
        if declared_bounds is not None:
            left, top, right, bottom = declared_bounds
            actual_left, actual_top, actual_right, actual_bottom = actual_bounds
            if (
                actual_left < left
                or actual_top < top
                or actual_right > right
                or actual_bottom > bottom
            ):
                raise CorruptDocumentError("XLSX cells fall outside the declared dimension")
    return _WorksheetCounts(
        declared_cells=_area(declared_bounds) if declared_bounds is not None else 0,
        serialized_cells=serialized_cells,
        non_empty_cells=non_empty_cells,
        native_text_chars=native_text_chars,
    )


def _preflight_xml_root(
    archive: ZipFile,
    part_name: str,
    *,
    expected_tag: str,
    message: str,
) -> None:
    root_seen = False
    try:
        with archive.open(part_name) as stream:
            for event, element in _xml_events(stream, message=message):
                if not root_seen and event == "start":
                    root_seen = True
                    if element.tag != expected_tag:
                        raise CorruptDocumentError(message)
                if event == "end":
                    element.clear()
    except (KeyError, OSError) as error:
        raise CorruptDocumentError(message) from error
    if not root_seen:
        raise CorruptDocumentError(message)


def _chartsheet_preflight(
    archive: ZipFile,
    infos: dict[str, ZipInfo],
    part_name: str,
    usage: _ResourceUsage,
    visited_drawings: set[str],
    visited_charts: set[str],
    sheet_index: int,
    unsupported_objects: list[XlsxUnsupportedObjectRef],
) -> None:
    relationships = _read_relationships(archive, infos, part_name, required=False)
    drawing_ids: list[str] = []
    root_seen = False
    try:
        with archive.open(part_name) as stream:
            for event, element in _xml_events(stream, message="XLSX chartsheet part is corrupt"):
                if not root_seen and event == "start":
                    root_seen = True
                    if element.tag != f"{{{_SPREADSHEET_NS}}}chartsheet":
                        raise CorruptDocumentError("XLSX chartsheet namespace is invalid")
                if event == "end":
                    if element.tag == f"{{{_SPREADSHEET_NS}}}drawing":
                        relationship_id = element.get(_RELATIONSHIP_ID)
                        if relationship_id is None:
                            raise CorruptDocumentError("XLSX chartsheet drawing is invalid")
                        drawing_ids.append(relationship_id)
                    element.clear()
    except (KeyError, OSError) as error:
        raise CorruptDocumentError("XLSX chartsheet part is corrupt") from error
    if not root_seen:
        raise CorruptDocumentError("XLSX chartsheet part is corrupt")
    for relationship_id in drawing_ids:
        relationship = _relationship_for(
            relationships,
            relationship_id,
            expected_type=_DRAWING_RELATIONSHIP,
        )
        if relationship.external:
            raise CorruptDocumentError("XLSX chartsheet drawing is invalid")
        if relationship.target not in visited_drawings:
            visited_drawings.add(relationship.target)
            _preflight_drawing(
                archive,
                infos,
                relationship.target,
                usage,
                visited_charts,
                sheet_index,
                unsupported_objects,
                allow_zero_absolute_extent=True,
            )
    for relationship_id, relationship in relationships.items():
        if relationship.relationship_type == _DRAWING_RELATIONSHIP:
            continue
        _increment(
            usage,
            "drawing_objects",
            1,
            limit=MAX_DRAWING_OBJECTS,
            message="XLSX exceeds the drawing object limit",
        )
        _add_native_text(usage, len(relationship.target))
        _record_unsupported_object(
            unsupported_objects,
            sheet_index=sheet_index,
            relationship_id=relationship_id,
            relationship=relationship,
        )


def _projected_budgets(
    *,
    sheet_count: int,
    usage: _ResourceUsage,
) -> tuple[int, int]:
    projected_wire = (
        sheet_count * 1_024
        + usage.serialized_cells * 256
        + usage.non_empty_cells * 1_024
        + usage.materialized_grid_cells * 128
        + usage.drawing_objects * 2_048
        + usage.chart_cache_points * 512
        + usage.hyperlinks_and_comments * 1_024
        + usage.native_text_chars * 8
    )
    projected_workbook = (
        sheet_count * 16_384
        + usage.serialized_cells * 512
        + usage.materialized_grid_cells * 256
        + usage.shared_strings * 128
        + usage.style_records * 1_024
        + usage.table_columns * 512
        + usage.conditional_formatting_rules * 1_024
        + usage.conditional_formatting_ranges * 512
        + usage.data_validations * 1_024
        + usage.defined_names * 512
        + usage.pivot_caches * 16_384
        + usage.pivot_tables * 16_384
        + usage.pivot_cache_records * 512
        + usage.pivot_items * 256
        + usage.custom_properties * 1_024
        + usage.row_dimensions * 512
        + usage.column_dimensions * 512
        + usage.page_breaks * 256
        + usage.scenarios * 2_048
        + usage.sheet_views * 2_048
        + usage.filter_items * 512
        + usage.workbook_views * 2_048
        + usage.external_references * 2_048
        + usage.comment_authors * 256
        + usage.drawing_objects * 32_768
        + usage.chart_cache_points * 512
        + usage.native_text_chars * 4
        + usage.xml_elements * 128
    )
    if projected_wire > MAX_PROJECTED_WIRE_BYTES:
        raise LimitExceededError("XLSX projected native wire exceeds the inline result budget")
    if projected_workbook > MAX_PROJECTED_WORKBOOK_BYTES:
        raise LimitExceededError("XLSX projected workbook exceeds the resource budget")
    return projected_wire, projected_workbook


def preflight_xlsx(path: Path) -> XlsxPreflight:
    validate_office_package(path, document_type=DocumentType.XLSX)
    try:
        with ZipFile(path) as archive:
            infos = {info.filename: info for info in archive.infolist()}
            usage = _ResourceUsage()
            _preflight_all_xml_parts(archive, infos, usage)
            (
                sheet_entries,
                date_1904,
                pivot_cache_targets,
                shared_strings_part,
                styles_part,
            ) = _parse_workbook(
                archive,
                infos,
                usage,
            )
            _preflight_shared_strings(archive, shared_strings_part, usage)
            _preflight_styles(archive, styles_part, usage)
            _preflight_custom_properties(archive, infos, usage)
            for target in pivot_cache_targets:
                _preflight_pivot_cache(archive, infos, target, usage)
            sheets: list[XlsxPreflightSheet] = []
            serialized_cells = 0
            non_empty_cells = 0
            visited_drawings: set[str] = set()
            visited_charts: set[str] = set()
            for sheet_index, (name, kind, state, part_name) in enumerate(
                sheet_entries,
                start=1,
            ):
                unsupported_objects: list[XlsxUnsupportedObjectRef] = []
                if kind is XlsxSheetKind.WORKSHEET:
                    counts = _worksheet_counts(
                        archive,
                        infos,
                        part_name,
                        usage,
                        visited_drawings,
                        visited_charts,
                        sheet_index,
                        unsupported_objects,
                    )
                else:
                    _chartsheet_preflight(
                        archive,
                        infos,
                        part_name,
                        usage,
                        visited_drawings,
                        visited_charts,
                        sheet_index,
                        unsupported_objects,
                    )
                    counts = _WorksheetCounts(0, 0, 0, 0)
                serialized_cells += counts.serialized_cells
                non_empty_cells += counts.non_empty_cells
                if serialized_cells > MAX_SERIALIZED_CELLS:
                    raise LimitExceededError("XLSX exceeds the serialized cell limit")
                if non_empty_cells > MAX_NON_EMPTY_CELLS:
                    raise LimitExceededError("XLSX exceeds the non-empty cell limit")
                usage.serialized_cells = serialized_cells
                usage.non_empty_cells = non_empty_cells
                _add_native_text(usage, counts.native_text_chars)
                sheets.append(
                    XlsxPreflightSheet(
                        sheet_index=sheet_index,
                        name=name,
                        kind=kind,
                        state=state,
                        part_name=part_name,
                        declared_cells=counts.declared_cells,
                        serialized_cells=counts.serialized_cells,
                        non_empty_cells=counts.non_empty_cells,
                        unsupported_objects=tuple(unsupported_objects),
                    )
                )
            projected_wire, projected_workbook = _projected_budgets(
                sheet_count=len(sheets),
                usage=usage,
            )
    except BadZipFile as error:
        raise CorruptDocumentError("XLSX package is corrupt") from error
    except OSError as error:
        raise CorruptDocumentError("XLSX package could not be read") from error
    return XlsxPreflight(
        sheets=tuple(sheets),
        date_1904=date_1904,
        serialized_cells=serialized_cells,
        non_empty_cells=non_empty_cells,
        native_text_chars=usage.native_text_chars,
        projected_wire_bytes=projected_wire,
        projected_workbook_bytes=projected_workbook,
        usage=usage.freeze(),
    )
