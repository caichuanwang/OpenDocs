from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.formatting.rule import Rule
from openpyxl.styles import Font
from openpyxl.styles.differential import DifferentialStyle
from openpyxl.styles.numbers import NumberFormat
from openpyxl.worksheet.table import Table

import opendocs.parsers.xlsx.extract as extract_module
from opendocs._models import (
    DocumentType,
    HeadingBlock,
    MarkdownBlock,
    ParagraphBlock,
    ParsedDocument,
    SpannedTableBlock,
    TableBlock,
)
from opendocs.errors import LimitExceededError
from opendocs.markdown import render_markdown
from opendocs.parsers.xlsx.extract import extract_xlsx
from opendocs.parsers.xlsx.models import XlsxNativeSlot, XlsxSheet, XlsxSheetKind, XlsxSheetState
from opendocs.parsers.xlsx.preflight import preflight_xlsx
from tests.xlsx_fixtures import rewrite_xlsx


def _save_ordered_workbook(path: Path) -> None:
    workbook = Workbook()
    visible = workbook.active
    visible.title = "Visible"
    visible["A1"] = "kept"
    hidden = workbook.create_sheet("Hidden")
    hidden.sheet_state = "hidden"
    very_hidden = workbook.create_sheet("Very Hidden")
    very_hidden.sheet_state = "veryHidden"
    chart = BarChart()
    chart.add_data(Reference(visible, min_col=1, min_row=1, max_row=1))
    chart_sheet = workbook.create_chartsheet("Chart")
    chart_sheet.add_chart(chart)
    workbook.save(path)


def _native_slots(document_sheet: XlsxSheet) -> tuple[XlsxNativeSlot, ...]:
    return tuple(slot for slot in document_sheet.slots if isinstance(slot, XlsxNativeSlot))


def test_extract_preserves_all_sheet_like_entries_states_and_empty_sheets(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ordered.xlsx"
    _save_ordered_workbook(path)

    document = extract_xlsx(path, preflight_xlsx(path))

    assert [
        (sheet.sheet_index, sheet.name, sheet.kind, sheet.state) for sheet in document.sheets
    ] == [
        (1, "Visible", XlsxSheetKind.WORKSHEET, XlsxSheetState.VISIBLE),
        (2, "Hidden", XlsxSheetKind.WORKSHEET, XlsxSheetState.HIDDEN),
        (3, "Very Hidden", XlsxSheetKind.WORKSHEET, XlsxSheetState.VERY_HIDDEN),
        (4, "Chart", XlsxSheetKind.CHARTSHEET, XlsxSheetState.VISIBLE),
    ]
    for expected_index, sheet in enumerate(document.sheets, start=1):
        prelude = _native_slots(sheet)[0]
        assert prelude.anchor == "A1"
        assert prelude.source_index == 0
        assert isinstance(prelude.blocks[0], MarkdownBlock)
        assert prelude.blocks[0].markdown == f"<!-- xlsx-sheet: {expected_index} -->"
        assert sheet.name not in prelude.blocks[0].markdown
        assert isinstance(prelude.blocks[1], HeadingBlock)
        assert isinstance(prelude.blocks[2], ParagraphBlock)
    assert len(_native_slots(document.sheets[1])) == 1
    assert len(_native_slots(document.sheets[2])) == 1
    assert len(_native_slots(document.sheets[3])) == 1


def test_extract_builds_tables_regions_merges_and_ignores_style_only_cells(
    tmp_path: Path,
) -> None:
    path = tmp_path / "regions.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Regions"
    sheet.append(("Name", "Amount"))
    sheet.append(("A", 1))
    sheet.append(("B", 2))
    table = Table(displayName="Ledger", ref="A1:B3")
    table.headerRowCount = 1
    sheet.add_table(table)
    sheet["D1"] = "Merged"
    sheet.merge_cells("D1:E1")
    sheet["D2"] = "left"
    sheet["E2"] = "right"
    sheet["H1"] = "first"
    sheet["H3"] = "second"
    sheet["J1"].font = Font(bold=True, color="FF0000")
    workbook.save(path)

    document = extract_xlsx(path, preflight_xlsx(path))

    slots = _native_slots(document.sheets[0])
    assert [slot.anchor for slot in slots] == ["A1", "A1:B3", "D1:E2", "H1", "H3"]
    assert [slot.source_index for slot in slots] == list(range(5))
    assert isinstance(slots[1].blocks[1], TableBlock)
    assert slots[1].blocks[1].header_rows == 1
    assert slots[1].blocks[1].grid == (("Name", "Amount"), ("A", "1"), ("B", "2"))
    assert isinstance(slots[2].blocks[1], SpannedTableBlock)
    assert slots[2].blocks[1].header_rows == 0
    assert [(cell.row_span, cell.column_span, cell.text) for cell in slots[2].blocks[1].cells] == [
        (1, 2, "Merged"),
        (1, 1, "left"),
        (1, 1, "right"),
    ]
    assert isinstance(slots[3].blocks[1], TableBlock)
    assert slots[3].blocks[1].header_rows == 0
    assert all(
        "Regions" not in block.markdown
        for slot in slots
        for block in slot.blocks
        if isinstance(block, MarkdownBlock)
    )
    assert all(slot.anchor != "J1" for slot in slots)


def test_excel_table_header_row_count_zero_is_not_promoted_to_header(tmp_path: Path) -> None:
    path = tmp_path / "headerless.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet["A1"] = "one"
    sheet["A2"] = "two"
    table = Table(displayName="Headerless", ref="A1:A2")
    table.headerRowCount = 0
    sheet.add_table(table)
    workbook.save(path)

    document = extract_xlsx(path, preflight_xlsx(path))

    table_block = _native_slots(document.sheets[0])[1].blocks[1]
    assert isinstance(table_block, TableBlock)
    assert table_block.header_rows == 0


def test_extracted_native_blocks_render_to_stable_markdown(tmp_path: Path) -> None:
    path = tmp_path / "golden.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Ledger"
    sheet.append(("Item", "Amount"))
    sheet.append(("Book", 12.5))
    sheet["B2"].number_format = "$0.00"
    workbook.save(path)

    document = extract_xlsx(path, preflight_xlsx(path))
    blocks = tuple(
        block
        for extracted_sheet in document.sheets
        for slot in extracted_sheet.slots
        if isinstance(slot, XlsxNativeSlot)
        for block in slot.blocks
    )
    rendered = render_markdown(
        ParsedDocument(DocumentType.XLSX, blocks, document.warnings),
        max_output_chars=10_000,
    )

    assert rendered.markdown == (
        "<!-- xlsx-sheet: 1 -->\n\n"
        "# Ledger\n\n"
        "Sheet state: visible\n\n"
        "<!-- xlsx-region: sheet=1 range=A1:B2 object=1 -->\n\n"
        "<table>\n"
        "<tbody>\n"
        "<tr><td>Item</td><td>Amount</td></tr>\n"
        "<tr><td>Book</td><td>$12.50</td></tr>\n"
        "</tbody>\n"
        "</table>\n"
    )


def test_extract_rechecks_component_bounding_box_before_materializing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "bounded.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet["A1"] = "a"
    sheet["B1"] = "b"
    sheet["B2"] = "c"
    workbook.save(path)
    index = preflight_xlsx(path)
    monkeypatch.setattr(extract_module, "MAX_MATERIALIZED_GRID_CELLS", 3)

    with pytest.raises(LimitExceededError, match="materialized grid"):
        extract_xlsx(path, index)


def test_extract_rechecks_materialized_grid_across_all_sheets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "bounded-workbook.xlsx"
    workbook = Workbook()
    first = workbook.active
    first.append(("a", "b"))
    second = workbook.create_sheet("Second")
    second.append(("c", "d"))
    workbook.save(path)
    index = preflight_xlsx(path)
    monkeypatch.setattr(extract_module, "MAX_MATERIALIZED_GRID_CELLS", 3)

    with pytest.raises(LimitExceededError, match="materialized grid"):
        extract_xlsx(path, index)


def test_extract_aggregates_number_format_warnings_after_twenty_coordinates(
    tmp_path: Path,
) -> None:
    path = tmp_path / "warnings.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Warnings"
    for row in range(1, 23):
        cell = sheet.cell(row, 1, row)
        cell.number_format = "0.00E+00"
    workbook.save(path)

    document = extract_xlsx(path, preflight_xlsx(path))

    warnings = [
        warning for warning in document.warnings if warning.code == "xlsx_unsupported_number_format"
    ]
    assert len(warnings) == 21
    assert warnings[0].message.startswith("Warnings!A1:")
    assert warnings[19].message.startswith("Warnings!A20:")
    assert warnings[20].message == "2 additional xlsx_unsupported_number_format warnings suppressed"


def test_extract_falls_back_when_conditional_rule_can_change_number_format(
    tmp_path: Path,
) -> None:
    path = tmp_path / "conditional-number-format.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet["A1"] = 1234.5
    sheet.conditional_formatting.add(
        "A1",
        Rule(
            type="expression",
            formula=("1",),
            dxf=DifferentialStyle(numFmt=NumberFormat(numFmtId=164, formatCode="$0.00")),
        ),
    )
    workbook.save(path)

    document = extract_xlsx(path, preflight_xlsx(path))

    region = _native_slots(document.sheets[0])[1]
    assert isinstance(region.blocks[1], TableBlock)
    assert region.blocks[1].grid == (("1234.5",),)
    assert [warning.code for warning in document.warnings] == ["xlsx_unsupported_number_format"]


def test_extract_loads_full_mode_openpyxl_once_with_links_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "once.xlsx"
    workbook = Workbook()
    workbook.active["A1"] = "value"
    workbook.save(path)
    index = preflight_xlsx(path)
    calls: list[tuple[Path, dict[str, object]]] = []
    original = extract_module.openpyxl.load_workbook

    def recording_loader(filename: Path, **kwargs: object) -> object:
        calls.append((filename, kwargs))
        return original(filename, **kwargs)

    monkeypatch.setattr(extract_module.openpyxl, "load_workbook", recording_loader)

    extract_xlsx(path, index)

    assert calls == [
        (
            path,
            {
                "read_only": False,
                "data_only": False,
                "rich_text": False,
                "keep_links": False,
            },
        )
    ]


def test_formula_sidecar_prefers_cache_and_distinguishes_missing_empty_and_special_formulas(
    tmp_path: Path,
) -> None:
    path = tmp_path / "formulas.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Formulas"
    for row in range(1, 9):
        sheet.cell(row, 1, row)
        sheet.cell(row, 2, f"=A{row}*2")
    sheet["B1"].number_format = "$#,##0.00"
    workbook.save(path)
    with ZipFile(path) as archive:
        xml = archive.read("xl/worksheets/sheet1.xml")
    replacements = {
        b'<c r="B1" s="1"><f>A1*2</f><v></v></c>': b'<c r="B1" s="1"><f>A1*2</f><v>2</v></c>',
        b'<c r="B2"><f>A2*2</f><v></v></c>': b'<c r="B2"><f>A2*2</f></c>',
        b'<c r="B3"><f>A3*2</f><v></v></c>': b'<c r="B3"><f>A3*2</f><v></v></c>',
        b'<c r="B4"><f>A4*2</f><v></v></c>': b'<c r="B4"><f>[1]Sheet1!A1</f></c>',
        b'<c r="B5"><f>A5*2</f><v></v></c>': (
            b'<c r="B5"><f t="array" ref="B5:C5">SUM(A5:A6)</f><v>10</v></c>'
        ),
        b'<c r="B6"><f>A6*2</f><v></v></c>': (
            b'<c r="B6"><f t="dataTable" ref="B6:C7" dt2D="1" r1="A1" r2="A2"></f><v></v></c>'
        ),
        b'<c r="B7"><f>A7*2</f><v></v></c>': (
            b'<c r="B7"><f t="shared" ref="B7:B8" si="0">A7*2</f></c>'
        ),
        b'<c r="B8"><f>A8*2</f><v></v></c>': b'<c r="B8"><f t="shared" si="0"></f></c>',
    }
    for old, new in replacements.items():
        assert old in xml
        xml = xml.replace(old, new)
    rewrite_xlsx(path, {"xl/worksheets/sheet1.xml": xml})

    document = extract_xlsx(path, preflight_xlsx(path))

    text_by_anchor = {
        slot.anchor: "\n".join(cell for row in slot.blocks[1].grid for cell in row)
        for slot in _native_slots(document.sheets[0])[1:]
        if isinstance(slot.blocks[1], TableBlock)
    }
    all_text = "\n".join(text_by_anchor.values())
    assert "$2.00" in all_text
    assert "=A2*2" in all_text
    assert "=A3*2" not in all_text
    assert "=[1]Sheet1!A1" in all_text
    assert "Array/spill formula B5:C5: =SUM(A5:A6); saved value: 10" in all_text
    assert "Data-table formula B6:C7 (dt2D=1, r1=A1, r2=A2)" in all_text
    assert "=A7*2" in all_text
    assert "=A8*2" in all_text
    codes = [warning.code for warning in document.warnings]
    assert codes.count("xlsx_formula_cache_missing") == 4
    assert codes.count("xlsx_data_table_formula") == 1
    assert codes.count("xlsx_external_reference") == 1


def test_repeated_extraction_is_deterministic(tmp_path: Path) -> None:
    path = tmp_path / "repeat.xlsx"
    _save_ordered_workbook(path)
    index = preflight_xlsx(path)

    assert extract_xlsx(path, index) == extract_xlsx(path, index)
