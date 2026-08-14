from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from opendocs._models import HeadingBlock, InlineText
from opendocs.parsers.xlsx.media import build_xlsx_visual_requests
from opendocs.parsers.xlsx.models import (
    XlsxChartSlot,
    XlsxDocument,
    XlsxSheet,
    XlsxSheetKind,
    XlsxSheetState,
)
from opendocs.vision.base import VisionRequest, VisionResult, VisionTextElement


class RecordingVision:
    def __init__(self) -> None:
        self.requests: list[VisionRequest] = []

    async def analyze(self, request: VisionRequest) -> VisionResult:
        self.requests.append(request)
        return VisionResult((VisionTextElement("视觉解释: 收入总体上升", request.source_index),))


@pytest.mark.asyncio
async def test_xlsx_visual_request_seam_uses_fake_client_and_bounded_chart_prompt(
    tmp_path: Path,
) -> None:
    artifact_name = "chart.png"
    image = Image.new("RGB", (32, 16), "white")
    try:
        image.save(tmp_path / artifact_name, "PNG")
    finally:
        image.close()
    document = XlsxDocument(
        (
            XlsxSheet(
                1,
                "Data",
                XlsxSheetKind.WORKSHEET,
                XlsxSheetState.VISIBLE,
                (
                    XlsxChartSlot(
                        1,
                        "D2",
                        artifact_name,
                        "a" * 64,
                        (HeadingBlock(2, (InlineText("Revenue"),)),),
                    ),
                ),
            ),
        )
    )
    specs = build_xlsx_visual_requests(document, tmp_path)
    vision = RecordingVision()

    result = await vision.analyze(specs[0].to_vision_request())

    assert result.elements == (VisionTextElement("视觉解释: 收入总体上升", 0),)
    assert len(vision.requests) == 1
    assert vision.requests[0].image_path == tmp_path / artifact_name
    assert "视觉解释" in vision.requests[0].prompt
    assert "Excel 外观还原" in vision.requests[0].prompt
