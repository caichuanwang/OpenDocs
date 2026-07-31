from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OfficeChecklist:
    format: str
    ordered_anchors: tuple[str, ...]
    required_boundaries: tuple[str, ...]
    minimum_table_count: int
    required_visual_slots: tuple[str, ...]
    allowed_warnings: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.format not in {"docx", "pptx"}:
            raise ValueError("Office checklist format must be docx or pptx")
        if not self.ordered_anchors:
            raise ValueError("Office checklist must contain at least one anchor")
        if self.minimum_table_count < 0:
            raise ValueError("minimum table count must not be negative")


@dataclass(frozen=True, slots=True)
class OfficeQualityResult:
    format: str
    mode: str
    check_results: tuple[tuple[str, bool], ...]
    warning_count: int
    table_count: int
    visual_slot_count: int
    passed: bool

    @property
    def checks(self) -> dict[str, bool]:
        return dict(self.check_results)

    def to_safe_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "format": self.format,
            "mode": self.mode,
            "checks": self.checks,
            "warning_count": self.warning_count,
            "table_count": self.table_count,
            "visual_slot_count": self.visual_slot_count,
            "passed": self.passed,
        }


def evaluate_office_structure(
    markdown: str,
    warnings: tuple[str, ...],
    checklist: OfficeChecklist,
    *,
    mode: str,
) -> OfficeQualityResult:
    if mode not in {"native", "replay", "live"}:
        raise ValueError("Office evaluation mode must be native, replay, or live")
    normalized_warnings = tuple(_normalize_warning(warning) for warning in warnings)
    allowed_warnings = {_normalize_warning(warning) for warning in checklist.allowed_warnings}
    anchors_present = all(anchor in markdown for anchor in checklist.ordered_anchors)
    positions = tuple(markdown.find(anchor) for anchor in checklist.ordered_anchors)
    anchor_order = anchors_present and tuple(sorted(positions)) == positions
    table_count = _count_markdown_tables(markdown)
    visual_slot_count = sum(slot in markdown for slot in checklist.required_visual_slots)
    checks = (
        ("anchors_present", anchors_present),
        ("anchor_order", anchor_order),
        (
            "boundaries",
            all(boundary in markdown for boundary in checklist.required_boundaries),
        ),
        ("tables", table_count >= checklist.minimum_table_count),
        (
            "visual_slots",
            visual_slot_count == len(checklist.required_visual_slots),
        ),
        (
            "warnings",
            all(warning in allowed_warnings for warning in normalized_warnings),
        ),
    )
    return OfficeQualityResult(
        format=checklist.format,
        mode=mode,
        check_results=checks,
        warning_count=len(normalized_warnings),
        table_count=table_count,
        visual_slot_count=visual_slot_count,
        passed=all(passed for _, passed in checks),
    )


def evaluate_office_determinism(
    markdown_runs: tuple[str, ...],
    warning_runs: tuple[tuple[str, ...], ...],
) -> bool:
    if len(markdown_runs) < 2 or len(markdown_runs) != len(warning_runs):
        raise ValueError("determinism requires at least two aligned runs")
    first_markdown = markdown_runs[0]
    first_warnings = tuple(_normalize_warning(value) for value in warning_runs[0])
    return all(markdown == first_markdown for markdown in markdown_runs[1:]) and all(
        tuple(_normalize_warning(value) for value in warnings) == first_warnings
        for warnings in warning_runs[1:]
    )


def _normalize_warning(value: str) -> str:
    return " ".join(value.split())


def _count_markdown_tables(markdown: str) -> int:
    lines = markdown.splitlines()
    return sum(
        "|" in lines[index] and _is_table_delimiter(lines[index + 1])
        for index in range(len(lines) - 1)
    )


def _is_table_delimiter(line: str) -> bool:
    cells = line.strip().strip("|").split("|")
    return len(cells) > 1 and all(
        re.fullmatch(r"\s*:?-{3,}:?\s*", cell) is not None for cell in cells
    )
