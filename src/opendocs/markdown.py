from __future__ import annotations

import html
import re
from urllib.parse import urlsplit

from opendocs._models import (
    Block,
    HardPageBreakBlock,
    HeadingBlock,
    Inline,
    InlineLink,
    ListItemBlock,
    ListKind,
    MarkdownBlock,
    PageBreakBlock,
    ParagraphBlock,
    ParsedDocument,
    RenderResult,
    SpannedTableBlock,
    TableBlock,
    WarningRecord,
)
from opendocs.errors import LimitExceededError, NoUsableContentError

_INLINE_MARKDOWN = re.compile(r"([\\`*_\[\]<>])")
_BLOCK_MARKDOWN = re.compile(r"(?m)^([ \t]{0,3})([#>]|[-+](?=\s)|(\d+)([.)])(?=\s))")
_LINK_TARGET_MARKDOWN = re.compile(r"([\\()])")
_SAFE_LINK_SCHEMES = frozenset({"http", "https", "mailto"})


def _escape_plain_text(value: str) -> str:
    escaped = _INLINE_MARKDOWN.sub(r"\\\1", value)
    return _BLOCK_MARKDOWN.sub(_escape_block_marker, escaped)


def _escape_block_marker(match: re.Match[str]) -> str:
    indent = match.group(1)
    marker = match.group(2)
    ordered_prefix = match.group(3)
    ordered_delimiter = match.group(4)

    if ordered_prefix is not None and ordered_delimiter is not None:
        return f"{indent}{ordered_prefix}\\{ordered_delimiter}"
    return f"{indent}\\{marker}"


def _normalize_cell_newlines(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _escape_pipe_cell(value: str) -> str:
    value = _normalize_cell_newlines(value)
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def _escape_html_cell(value: str) -> str:
    return html.escape(_normalize_cell_newlines(value), quote=True).replace("\n", "<br>")


def _escape_link_target(value: str) -> str:
    return _LINK_TARGET_MARKDOWN.sub(r"\\\1", value)


def _render_inlines(inlines: tuple[Inline, ...]) -> tuple[str, tuple[WarningRecord, ...]]:
    rendered: list[str] = []
    warnings: list[WarningRecord] = []

    for inline in inlines:
        if isinstance(inline, InlineLink):
            rendered_value, inline_warning = _render_link(inline)
            rendered.append(rendered_value)
            if inline_warning is not None:
                warnings.append(inline_warning)
            continue
        rendered.append(_escape_plain_text(inline.text))

    return "".join(rendered).rstrip("\n"), tuple(warnings)


def _render_link(inline: InlineLink) -> tuple[str, WarningRecord | None]:
    if _is_safe_link_target(inline.target):
        label = _escape_plain_text(inline.label)
        target = _escape_link_target(inline.target)
        return f"[{label}]({target})", None

    return _escape_plain_text(inline.label), WarningRecord(
        code="unsafe_link_target",
        message=f"rendered plain text for unsupported link target: {inline.target}",
    )


def _is_safe_link_target(target: str) -> bool:
    if target.startswith("#"):
        return True
    parsed = urlsplit(target)
    return parsed.scheme.lower() in _SAFE_LINK_SCHEMES


def _render_pipe_table(block: TableBlock) -> str:
    header = "| " + " | ".join(_escape_pipe_cell(cell) for cell in block.grid[0]) + " |"
    separator = "| " + " | ".join("---" for _ in block.grid[0]) + " |"
    body = [
        "| " + " | ".join(_escape_pipe_cell(cell) for cell in row) + " |" for row in block.grid[1:]
    ]
    return "\n".join((header, separator, *body))


def _render_html_rows(rows: tuple[tuple[str, ...], ...], *, header: bool) -> list[str]:
    tag = "th" if header else "td"
    return [
        "<tr>" + "".join(f"<{tag}>{_escape_html_cell(cell)}</{tag}>" for cell in row) + "</tr>"
        for row in rows
    ]


def _render_html_table(block: TableBlock) -> str:
    lines = ["<table>"]
    if block.header_rows:
        lines.append("<thead>")
        lines.extend(_render_html_rows(block.grid[: block.header_rows], header=True))
        lines.append("</thead>")
    lines.append("<tbody>")
    lines.extend(_render_html_rows(block.grid[block.header_rows :], header=False))
    lines.extend(("</tbody>", "</table>"))
    return "\n".join(lines)


def _render_spanned_rows(
    block: SpannedTableBlock,
    *,
    start_row: int,
    end_row: int,
    header: bool,
) -> list[str]:
    origins = {(cell.row, cell.column): cell for cell in block.cells}
    tag = "th" if header else "td"
    rows: list[str] = []
    for row_index in range(start_row, end_row):
        cells: list[str] = []
        for column_index in range(block.column_count):
            cell = origins.get((row_index, column_index))
            if cell is None:
                continue
            attrs = []
            if cell.row_span != 1:
                attrs.append(f' rowspan="{cell.row_span}"')
            if cell.column_span != 1:
                attrs.append(f' colspan="{cell.column_span}"')
            cells.append(f"<{tag}{''.join(attrs)}>{_escape_html_cell(cell.text)}</{tag}>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return rows


def _render_spanned_table(block: SpannedTableBlock) -> str:
    lines = ["<table>"]
    if block.header_rows:
        lines.append("<thead>")
        lines.extend(
            _render_spanned_rows(block, start_row=0, end_row=block.header_rows, header=True)
        )
        lines.append("</thead>")
    lines.append("<tbody>")
    lines.extend(
        _render_spanned_rows(
            block,
            start_row=block.header_rows,
            end_row=block.row_count,
            header=False,
        )
    )
    lines.extend(("</tbody>", "</table>"))
    return "\n".join(lines)


def _render_list_item(block: ListItemBlock) -> tuple[str, tuple[WarningRecord, ...]]:
    content, warnings = _render_inlines(block.inlines)
    marker = "- " if block.kind is ListKind.BULLET else f"{block.ordinal}. "
    indent = "  " * block.level
    if not content:
        return f"{indent}{marker}".rstrip(), warnings

    lines = content.split("\n")
    continuation = indent + (" " * len(marker))
    rendered = [f"{indent}{marker}{lines[0]}"]
    rendered.extend(f"{continuation}{line}" for line in lines[1:])
    return "\n".join(rendered), warnings


def _render_block(block: Block) -> tuple[str, tuple[WarningRecord, ...]]:
    if isinstance(block, MarkdownBlock):
        return block.markdown.rstrip("\n"), ()
    if isinstance(block, PageBreakBlock):
        return f"<!-- page: {block.page_number} -->", ()
    if isinstance(block, HardPageBreakBlock):
        return "<!-- page-break -->", ()
    if isinstance(block, TableBlock):
        if block.header_rows == 1:
            return _render_pipe_table(block), ()
        return _render_html_table(block), ()
    if isinstance(block, SpannedTableBlock):
        return _render_spanned_table(block), ()
    if isinstance(block, HeadingBlock):
        content, warnings = _render_inlines(block.inlines)
        return f"{'#' * block.level} {content}".rstrip(), warnings
    if isinstance(block, ParagraphBlock):
        return _render_inlines(block.inlines)
    if isinstance(block, ListItemBlock):
        return _render_list_item(block)
    return _escape_plain_text(block.text.rstrip("\n")), ()


def _has_semantic_inline_content(inlines: tuple[Inline, ...]) -> bool:
    return any(
        inline.label.strip() if isinstance(inline, InlineLink) else inline.text.strip()
        for inline in inlines
    )


def _is_semantically_usable(block: Block) -> bool:
    if isinstance(block, PageBreakBlock | HardPageBreakBlock):
        return False
    if isinstance(block, TableBlock):
        return any(cell.strip() for row in block.grid for cell in row)
    if isinstance(block, SpannedTableBlock):
        return any(cell.text.strip() for cell in block.cells)
    if isinstance(block, MarkdownBlock):
        return bool(block.markdown.strip())
    if isinstance(block, ParagraphBlock | HeadingBlock | ListItemBlock):
        return _has_semantic_inline_content(block.inlines)
    return bool(block.text.strip())


def _build_candidate(rendered: list[str], value: str) -> str:
    return "\n\n".join([*rendered, value]) + "\n"


def render_markdown(document: ParsedDocument, *, max_output_chars: int) -> RenderResult:
    if not any(_is_semantically_usable(block) for block in document.blocks):
        raise NoUsableContentError("document produced no usable content")

    rendered: list[str] = []
    warnings = list(document.warnings)
    semantic_blocks = 0

    index = 0
    while index < len(document.blocks):
        block = document.blocks[index]
        block_index = index + 1
        if isinstance(block, ListItemBlock):
            run: list[str] = []
            run_semantic_blocks = 0
            stop_at_block: int | None = None

            while index < len(document.blocks):
                list_block = document.blocks[index]
                if not isinstance(list_block, ListItemBlock):
                    break
                value, block_warnings = _render_list_item(list_block)
                if value:
                    candidate = _build_candidate(rendered, "\n".join([*run, value]))
                    if len(candidate) > max_output_chars:
                        if semantic_blocks == 0 and run_semantic_blocks == 0:
                            raise LimitExceededError(
                                "no complete block fits within max_output_chars"
                            )
                        stop_at_block = index + 1
                        break
                    run.append(value)
                    warnings.extend(block_warnings)
                    if _is_semantically_usable(list_block):
                        run_semantic_blocks += 1
                index += 1

            if run:
                rendered.append("\n".join(run))
                semantic_blocks += run_semantic_blocks
            if stop_at_block is not None:
                warnings.append(
                    WarningRecord(
                        code="output_truncated",
                        message=f"output stopped before block {stop_at_block}",
                    )
                )
                break
            continue

        value, block_warnings = _render_block(block)
        if value:
            candidate = _build_candidate(rendered, value)
            if len(candidate) > max_output_chars:
                if semantic_blocks == 0:
                    raise LimitExceededError("no complete block fits within max_output_chars")
                warnings.append(
                    WarningRecord(
                        code="output_truncated",
                        message=f"output stopped before block {block_index}",
                    )
                )
                break
            rendered.append(value)
            warnings.extend(block_warnings)
            if _is_semantically_usable(block):
                semantic_blocks += 1
        index += 1

    return RenderResult(markdown="\n\n".join(rendered) + "\n", warnings=tuple(warnings))
