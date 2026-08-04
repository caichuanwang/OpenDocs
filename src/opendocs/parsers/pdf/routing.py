from __future__ import annotations

from opendocs._models import BBox
from opendocs.errors import LimitExceededError
from opendocs.parsers.pdf.extract import bbox_area, intersection_area, union_area
from opendocs.parsers.pdf.models import PageFacts, PageRoute, PageRouteDecision, VisualRegion

SIGNIFICANT_REGION_AREA_MIN = 0.05
FULL_PAGE_IMAGE_AREA_MIN = 0.85
FULL_VISION_UNION_AREA_MIN = 0.60
MAX_REGIONS_PER_PAGE = 4
MAX_VISUAL_CANDIDATES_PER_PAGE = 1_024
REGION_PADDING = 0.015
REGION_MERGE_GAP = 0.02
VECTOR_OBJECT_COUNT_MIN = 30


def _expand(bbox: BBox) -> BBox:
    return BBox(
        max(0.0, bbox.left - REGION_PADDING),
        max(0.0, bbox.top - REGION_PADDING),
        min(1.0, bbox.right + REGION_PADDING),
        min(1.0, bbox.bottom + REGION_PADDING),
    )


def _should_merge(left: BBox, right: BBox) -> bool:
    horizontal = max(0.0, max(left.left, right.left) - min(left.right, right.right))
    vertical = max(0.0, max(left.top, right.top) - min(left.bottom, right.bottom))
    return intersection_area(left, right) > 0 or (
        horizontal <= REGION_MERGE_GAP and vertical <= REGION_MERGE_GAP
    )


def build_visual_regions(
    candidates: list[tuple[BBox, str]],
) -> tuple[VisualRegion, ...]:
    if len(candidates) > MAX_VISUAL_CANDIDATES_PER_PAGE:
        raise LimitExceededError("PDF visual region candidates exceed the resource budget")
    merged = [(_expand(bbox), {reason}, index) for index, (bbox, reason) in enumerate(candidates)]
    while True:
        for left_index, (left, reasons, source_index) in enumerate(merged):
            for right_index in range(left_index + 1, len(merged)):
                right, right_reasons, right_source = merged[right_index]
                if not _should_merge(left, right):
                    continue
                merged[left_index] = (
                    BBox(
                        min(left.left, right.left),
                        min(left.top, right.top),
                        max(left.right, right.right),
                        max(left.bottom, right.bottom),
                    ),
                    reasons | right_reasons,
                    min(source_index, right_source),
                )
                merged.pop(right_index)
                break
            else:
                continue
            break
        else:
            break
    reason_order = (
        "image",
        "table_structure_uncertain",
        "dense_drawing",
        "drawing",
        "reading_order_ambiguous",
    )
    return tuple(
        VisualRegion(bbox, tuple(reason for reason in reason_order if reason in reasons), index)
        for index, (bbox, reasons, _) in enumerate(
            sorted(merged, key=lambda item: (item[0].top, item[0].left, item[2]))
        )
    )


def route_page(page: PageFacts) -> PageRouteDecision:
    has_text = page.quality.non_whitespace_chars > 0
    regions = page.visual_regions
    if page.native_extraction_failed:
        route, reasons = PageRoute.FULL_VISION, ("native_text_unreliable",)
    elif not has_text and not regions:
        route, reasons = PageRoute.BLANK, ("no_text_or_visual_objects",)
    elif not has_text:
        route, reasons = PageRoute.FULL_VISION, ("visual_only_page",)
    elif not page.native_text_reliable:
        route, reasons = PageRoute.FULL_VISION, ("native_text_unreliable",)
    elif page.image_area_ratio >= FULL_PAGE_IMAGE_AREA_MIN:
        route, reasons = PageRoute.FULL_VISION, ("full_page_image",)
    elif (
        len(regions) > MAX_REGIONS_PER_PAGE
        or union_area([region.bbox for region in regions]) >= FULL_VISION_UNION_AREA_MIN
    ):
        route, reasons = PageRoute.FULL_VISION, ("visual_regions_cover_page",)
    elif regions:
        route, reasons = PageRoute.HYBRID, ("localized_visual_regions",)
    else:
        route, reasons = PageRoute.NATIVE, ("native_content_reliable",)
    return PageRouteDecision(
        page.page_number,
        route,
        () if route in {PageRoute.NATIVE, PageRoute.BLANK} else regions,
        reasons,
    )


def significant_image(bbox: BBox) -> bool:
    return bbox_area(bbox) >= SIGNIFICANT_REGION_AREA_MIN
