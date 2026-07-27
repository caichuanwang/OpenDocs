from __future__ import annotations

import pytest

from opendocs.options import ParseOptions, VisionConfig


def test_parse_options_defaults_match_documented_values() -> None:
    assert ParseOptions() == ParseOptions(
        timeout=900,
        max_pages=300,
        max_output_chars=400_000,
        vision_concurrency=4,
    )


@pytest.mark.parametrize(
    ("field_name", "kwargs"),
    [
        ("timeout", {"timeout": 0}),
        ("max_pages", {"max_pages": 0}),
        ("max_output_chars", {"max_output_chars": 0}),
        ("vision_concurrency", {"vision_concurrency": 0}),
    ],
)
def test_parse_options_rejects_zero_values(field_name: str, kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError, match=field_name):
        ParseOptions(**kwargs)


def test_parse_options_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout"):
        ParseOptions(timeout=-1)


def test_vision_config_requires_non_blank_model() -> None:
    with pytest.raises(ValueError, match="model"):
        VisionConfig(model="  ")


def test_vision_config_defaults_match_documented_values() -> None:
    assert VisionConfig(model="openai/vision-model") == VisionConfig(
        model="openai/vision-model",
        api_key=None,
        api_base=None,
        timeout=120,
        max_retries=2,
    )


def test_vision_config_rejects_non_positive_timeout() -> None:
    with pytest.raises(ValueError, match="timeout"):
        VisionConfig(model="openai/vision-model", timeout=0)


def test_vision_config_rejects_negative_max_retries() -> None:
    with pytest.raises(ValueError, match="max_retries"):
        VisionConfig(model="openai/vision-model", max_retries=-1)
