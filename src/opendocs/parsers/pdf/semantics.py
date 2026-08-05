from __future__ import annotations

import re

from opendocs._models import (
    Block,
    HeadingBlock,
    InlineText,
    ListItemBlock,
    ListKind,
    MarkdownBlock,
    ParagraphBlock,
    TextBlock,
)
from opendocs.parsers.pdf.models import NativeTextCandidate, PageFacts

_EDGE_TOP_MAX = 0.08
_EDGE_BOTTOM_MIN = 0.92
_EDGE_REPEAT_MIN_PAGES = 3
_EDGE_REPEAT_PERCENT = 60
_BULLET_LIST = re.compile(r"^[•◦▪‣\u2043*+-]\s+(.+)$")
_ORDERED_LIST = re.compile(r"^(\d{1,9})[.)]\s+(.+)$")
_PAGE_NUMBER = re.compile(r"^(?:page\s+)?\d+(?:\s+(?:of|/)\s+\d+)?$", re.IGNORECASE)
_MONOSPACE_MARKERS = ("mono", "courier", "consolas", "menlo", "fira code", "jetbrains")
_DEHYPHENATION_SUFFIXES = (
    "able",
    "al",
    "ation",
    "ed",
    "er",
    "est",
    "ible",
    "ing",
    "ity",
    "ive",
    "ly",
    "ment",
    "ness",
    "ous",
)


def _normalize_edge_text(text: str) -> str:
    return " ".join(text.casefold().split())


def edge_suppressions(pages: tuple[PageFacts, ...]) -> set[tuple[int, int]]:
    occurrences: dict[tuple[str, str], set[int]] = {}
    candidates: list[tuple[PageFacts, NativeTextCandidate, str]] = []
    for page in pages:
        for candidate in page.native_candidates:
            if not isinstance(candidate, NativeTextCandidate):
                continue
            edge = (
                "top"
                if candidate.bbox.top <= _EDGE_TOP_MAX
                else "bottom"
                if candidate.bbox.bottom >= _EDGE_BOTTOM_MIN
                else ""
            )
            if not edge:
                continue
            normalized = _normalize_edge_text(candidate.text)
            if not normalized:
                continue
            occurrences.setdefault((edge, normalized), set()).add(page.page_number)
            candidates.append((page, candidate, edge))

    required = max(
        _EDGE_REPEAT_MIN_PAGES,
        (len(pages) * _EDGE_REPEAT_PERCENT + 99) // 100,
    )
    suppressed: set[tuple[int, int]] = set()
    for page, candidate, edge in candidates:
        normalized = _normalize_edge_text(candidate.text)
        if len(occurrences[(edge, normalized)]) >= required or (
            edge == "bottom" and _PAGE_NUMBER.fullmatch(normalized)
        ):
            suppressed.add((page.page_number, candidate.source_index))
    return suppressed


def body_font_size(
    pages: tuple[PageFacts, ...],
    suppressed: set[tuple[int, int]],
) -> float | None:
    weights: dict[float, int] = {}
    for page in pages:
        for candidate in page.native_candidates:
            if not isinstance(candidate, NativeTextCandidate):
                continue
            font_size = candidate.font_size
            if (
                font_size is not None
                and (page.page_number, candidate.source_index) not in suppressed
                and candidate.bbox.top > _EDGE_TOP_MAX
                and candidate.bbox.bottom < _EDGE_BOTTOM_MIN
            ):
                size = float(round(font_size * 2) / 2)
                weights[size] = weights.get(size, 0) + len(candidate.text.strip())

    best_size: float | None = None
    best_weight = -1
    for size, weight in weights.items():
        if weight > best_weight or (
            weight == best_weight and (best_size is None or size < best_size)
        ):
            best_size = size
            best_weight = weight
    return best_size


def _is_monospace(candidate: NativeTextCandidate) -> bool:
    name = (candidate.font_name or "").casefold()
    return any(marker in name for marker in _MONOSPACE_MARKERS)


def _fenced_code(lines: list[str]) -> MarkdownBlock:
    longest = max(
        (len(match.group(0)) for line in lines for match in re.finditer(r"`+", line)),
        default=0,
    )
    fence = "`" * max(3, longest + 1)
    content = "\n".join(lines)
    return MarkdownBlock(f"{fence}\n{content}\n{fence}")


def _is_bold(candidate: NativeTextCandidate) -> bool:
    name = (candidate.font_name or "").casefold()
    return "bold" in name or "black" in name or "semibold" in name


def _heading_level(candidate: NativeTextCandidate, body_size: float | None) -> int | None:
    if body_size is None or candidate.font_size is None:
        return None
    text = candidate.text.strip()
    if not text or len(text) > 120 or text.endswith((".", "。", ";", "\uff1b")):
        return None
    ratio = candidate.font_size / body_size
    if ratio < 1.25 and not (ratio >= 1.12 and _is_bold(candidate) and len(text) <= 80):
        return None
    if ratio >= 1.8:
        return 1
    if ratio >= 1.45:
        return 2
    return 3


def _can_join_paragraph(left: NativeTextCandidate, right: NativeTextCandidate) -> bool:
    if left.font_size is None or right.font_size is None:
        return False
    if abs(left.font_size - right.font_size) > 0.5:
        return False
    vertical_gap = right.bbox.top - left.bbox.bottom
    return (
        -0.005 <= vertical_gap <= 0.04
        and abs(left.bbox.left - right.bbox.left) <= 0.04
        and abs(left.bbox.right - right.bbox.right) <= 0.35
    )


def _join_paragraph_text(left: str, right: str) -> str:
    hyphenated = re.search(r"([A-Za-z]{2,})-$", left)
    continuation = re.match(r"^([a-z]+)\b", right)
    if left.endswith("\u00ad") and continuation is not None:
        return left[:-1] + right
    if (
        hyphenated is not None
        and continuation is not None
        and continuation.group(1).endswith(_DEHYPHENATION_SUFFIXES)
    ):
        return left[:-1] + right
    if left.endswith("-"):
        return left + right
    closing_punctuation = ",.;:!?)]}\uff0c\u3002\uff1b\uff1a\uff01\uff1f"
    if left and right and left[-1] not in " \n" and right[0] not in closing_punctuation:
        return f"{left} {right}"
    return left + right


def _looks_like_code(text: str) -> bool:
    return bool(
        re.search(r"[{}()[\];=+\-*/<>]", text)
        or re.match(
            r"^\s*(?:def|class|import|from|return|if|elif|else|for|while|try|except)\b",
            text,
        )
    )


def structured_native_run(
    candidates: list[NativeTextCandidate],
    *,
    body_size: float | None,
) -> list[Block]:
    blocks: list[Block] = []
    index = 0
    next_list_id = 0
    active_list_id: int | None = None
    active_list_kind: ListKind | None = None
    while index < len(candidates):
        candidate = candidates[index]
        if _is_monospace(candidate) and _looks_like_code(candidate.text):
            code_lines = [candidate.text]
            cursor = index + 1
            while (
                cursor < len(candidates)
                and _is_monospace(candidates[cursor])
                and _looks_like_code(candidates[cursor].text)
            ):
                code_lines.append(candidates[cursor].text)
                cursor += 1
            blocks.append(_fenced_code(code_lines))
            index = cursor
            active_list_id = None
            active_list_kind = None
            continue

        level = _heading_level(candidate, body_size)
        if level is not None:
            blocks.append(HeadingBlock(level, (InlineText(candidate.text.strip()),)))
            index += 1
            active_list_id = None
            active_list_kind = None
            continue

        bullet = _BULLET_LIST.match(candidate.text.strip())
        ordered = _ORDERED_LIST.match(candidate.text.strip())
        if bullet is not None or ordered is not None:
            if bullet is not None:
                content = bullet.group(1)
                ordinal = 1
                kind = ListKind.BULLET
            elif ordered is not None:
                content = ordered.group(2)
                ordinal_text = ordered.group(1)
                ordinal = int(ordinal_text) if ordinal_text.isdecimal() else 1
                kind = ListKind.ORDERED
            else:
                raise AssertionError("list marker match disappeared")
            if active_list_id is None or active_list_kind is not kind:
                active_list_id = next_list_id
                active_list_kind = kind
                next_list_id += 1
            blocks.append(
                ListItemBlock(
                    active_list_id,
                    0,
                    kind,
                    ordinal,
                    (InlineText(content),),
                )
            )
            index += 1
            continue

        active_list_id = None
        active_list_kind = None
        if candidate.font_size is None:
            blocks.append(TextBlock(candidate.text))
            index += 1
            continue

        text = candidate.text.strip()
        previous = candidate
        index += 1
        while index < len(candidates):
            following = candidates[index]
            if (
                _is_monospace(following)
                or _heading_level(following, body_size) is not None
                or _BULLET_LIST.match(following.text.strip())
                or _ORDERED_LIST.match(following.text.strip())
                or not _can_join_paragraph(previous, following)
            ):
                break
            text = _join_paragraph_text(text, following.text.strip())
            previous = following
            index += 1
        blocks.append(ParagraphBlock((InlineText(text),)))
    return blocks
