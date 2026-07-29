from __future__ import annotations

import html
import re

from opendocs._models import (
    Block,
    MarkdownBlock,
    PageBreakBlock,
    ParsedDocument,
    RenderResult,
    TableBlock,
    WarningRecord,
)
from opendocs.errors import LimitExceededError, NoUsableContentError

_INLINE_MARKDOWN = re.compile(r"([\\`*_\[\]<>])")
_BLOCK_MARKDOWN = re.compile(r"(?m)^([ \t]{0,3})([#>]|[-+](?=\s)|(\d+)([.)])(?=\s))")


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


def _render_block(block: Block) -> str:
    if isinstance(block, MarkdownBlock):
        return block.markdown.rstrip("\n")
    if isinstance(block, PageBreakBlock):
        return f"<!-- page: {block.page_number} -->"
    if isinstance(block, TableBlock):
        if block.header_rows == 1:
            return _render_pipe_table(block)
        return _render_html_table(block)
    return _escape_plain_text(block.text.rstrip("\n"))


def _is_semantically_usable(block: Block) -> bool:
    if isinstance(block, PageBreakBlock):
        return False
    if isinstance(block, TableBlock):
        return any(cell.strip() for row in block.grid for cell in row)
    if isinstance(block, MarkdownBlock):
        return bool(block.markdown.strip())
    return bool(block.text.strip())


def render_markdown(document: ParsedDocument, *, max_output_chars: int) -> RenderResult:
    if not any(_is_semantically_usable(block) for block in document.blocks):
        raise NoUsableContentError("document produced no usable content")

    rendered: list[str] = []
    warnings = list(document.warnings)
    semantic_blocks = 0

    for index, block in enumerate(document.blocks, start=1):
        value = _render_block(block)
        if not value:
            continue
        candidate = "\n\n".join([*rendered, value]) + "\n"
        if len(candidate) > max_output_chars:
            if semantic_blocks == 0:
                raise LimitExceededError("no complete block fits within max_output_chars")
            warnings.append(
                WarningRecord(
                    code="output_truncated",
                    message=f"output stopped before block {index}",
                )
            )
            break
        rendered.append(value)
        if _is_semantically_usable(block):
            semantic_blocks += 1

    return RenderResult(markdown="\n\n".join(rendered) + "\n", warnings=tuple(warnings))
