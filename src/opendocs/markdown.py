from __future__ import annotations

import re

from opendocs._models import MarkdownBlock, ParsedDocument, RenderResult, TextBlock, WarningRecord
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


def _render_block(block: TextBlock | MarkdownBlock) -> str:
    if isinstance(block, MarkdownBlock):
        return block.markdown.rstrip("\n")
    return _escape_plain_text(block.text.rstrip("\n"))


def render_markdown(document: ParsedDocument, *, max_output_chars: int) -> RenderResult:
    rendered: list[str] = []
    warnings = list(document.warnings)

    for index, block in enumerate(document.blocks, start=1):
        value = _render_block(block)
        if not value:
            continue
        candidate = "\n\n".join([*rendered, value]) + "\n"
        if len(candidate) > max_output_chars:
            if not rendered:
                raise LimitExceededError("no complete block fits within max_output_chars")
            warnings.append(
                WarningRecord(
                    code="output_truncated",
                    message=f"output stopped before block {index}",
                )
            )
            break
        rendered.append(value)

    if not rendered:
        raise NoUsableContentError("document produced no usable content")

    return RenderResult(markdown="\n\n".join(rendered) + "\n", warnings=tuple(warnings))
