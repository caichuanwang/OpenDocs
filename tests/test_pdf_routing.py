from __future__ import annotations

import pytest

from opendocs._models import BBox
from opendocs.errors import LimitExceededError
from opendocs.parsers.pdf.extract import measure_text_quality
from opendocs.parsers.pdf.models import PageFacts, PageRoute, VisualRegion
from opendocs.parsers.pdf.routing import (
    FULL_PAGE_IMAGE_AREA_MIN,
    FULL_VISION_UNION_AREA_MIN,
    MAX_REGIONS_PER_PAGE,
    MAX_VISUAL_CANDIDATES_PER_PAGE,
    build_visual_regions,
    route_page,
)


def _page(
    *,
    text: str = "native text",
    regions: tuple[VisualRegion, ...] = (),
    reliable: bool = True,
    extraction_failed: bool = False,
    image_area: float = 0.0,
) -> PageFacts:
    box = BBox(0.0, 0.0, 100.0, 100.0)
    return PageFacts(
        page_number=1,
        media_box=box,
        crop_box=box,
        rotation=0,
        display_width=100.0,
        display_height=100.0,
        words=(),
        tables=(),
        native_candidates=(),
        visual_regions=regions,
        quality=measure_text_quality(text),
        image_area_ratio=image_area,
        drawing_object_count=0,
        native_extraction_failed=extraction_failed,
        reading_order_ambiguous=False,
        native_text_reliable=reliable,
    )


def _region(left: float, right: float, index: int = 0) -> VisualRegion:
    return VisualRegion(BBox(left, 0.0, right, 1.0), ("image",), index)


@pytest.mark.parametrize(
    ("page", "route", "reason"),
    [
        (_page(text=""), PageRoute.BLANK, "no_text_or_visual_objects"),
        (_page(text="", extraction_failed=True), PageRoute.FULL_VISION, "native_text_unreliable"),
        (_page(text="", regions=(_region(0.1, 0.2),)), PageRoute.FULL_VISION, "visual_only_page"),
        (_page(reliable=False), PageRoute.FULL_VISION, "native_text_unreliable"),
        (_page(image_area=FULL_PAGE_IMAGE_AREA_MIN), PageRoute.FULL_VISION, "full_page_image"),
        (_page(regions=(_region(0.1, 0.2),)), PageRoute.HYBRID, "localized_visual_regions"),
        (_page(), PageRoute.NATIVE, "native_content_reliable"),
    ],
)
def test_exact_route_matrix(page: PageFacts, route: PageRoute, reason: str) -> None:
    decision = route_page(page)

    assert decision.route is route
    assert decision.reasons == (reason,)
    if route is PageRoute.HYBRID:
        assert decision.regions
    if route in {PageRoute.NATIVE, PageRoute.BLANK}:
        assert decision.regions == ()


def test_region_count_and_union_thresholds_are_inclusive() -> None:
    too_many = tuple(
        _region(index * 0.1, index * 0.1 + 0.05, index) for index in range(MAX_REGIONS_PER_PAGE + 1)
    )
    union = (_region(0.0, FULL_VISION_UNION_AREA_MIN),)

    assert route_page(_page(regions=too_many)).route is PageRoute.FULL_VISION
    assert route_page(_page(regions=union)).route is PageRoute.FULL_VISION
    assert (
        route_page(_page(regions=(_region(0.0, FULL_VISION_UNION_AREA_MIN - 1e-9),))).route
        is PageRoute.HYBRID
    )


def test_visual_regions_merge_in_stable_source_order_with_reason_union() -> None:
    regions = build_visual_regions(
        [
            (BBox(0.1, 0.1, 0.3, 0.3), "image"),
            (BBox(0.29, 0.1, 0.5, 0.3), "table_structure_uncertain"),
            (BBox(0.8, 0.7, 0.9, 0.9), "dense_drawing"),
        ]
    )

    assert len(regions) == 2
    assert regions[0].reasons == ("image", "table_structure_uncertain")
    assert regions[1].reasons == ("dense_drawing",)


def test_visual_region_builder_rejects_candidates_beyond_resource_budget() -> None:
    candidates = [
        (BBox(0.1, 0.1, 0.2, 0.2), "reading_order_ambiguous")
        for _ in range(MAX_VISUAL_CANDIDATES_PER_PAGE + 1)
    ]

    with pytest.raises(LimitExceededError, match="visual region candidates"):
        build_visual_regions(candidates)
