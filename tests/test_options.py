from __future__ import annotations

from typing import Any, cast

import pytest

from opendocs import ParseOptions, VisionConfig


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


@pytest.mark.parametrize(
    ("field_name", "kwargs"),
    [
        ("timeout", {"timeout": cast(Any, True)}),
        ("timeout", {"timeout": cast(Any, "900")}),
        ("max_pages", {"max_pages": cast(Any, True)}),
        ("max_pages", {"max_pages": cast(Any, 3.5)}),
        ("max_pages", {"max_pages": cast(Any, "300")}),
        ("max_output_chars", {"max_output_chars": cast(Any, True)}),
        ("max_output_chars", {"max_output_chars": cast(Any, 400_000.5)}),
        ("max_output_chars", {"max_output_chars": cast(Any, "400000")}),
        ("vision_concurrency", {"vision_concurrency": cast(Any, True)}),
        ("vision_concurrency", {"vision_concurrency": cast(Any, 4.5)}),
        ("vision_concurrency", {"vision_concurrency": cast(Any, "4")}),
    ],
)
def test_parse_options_rejects_invalid_field_types(
    field_name: str,
    kwargs: dict[str, Any],
) -> None:
    with pytest.raises(TypeError, match=field_name):
        ParseOptions(**kwargs)


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


@pytest.mark.parametrize(
    ("field_name", "kwargs"),
    [
        ("model", {"model": cast(Any, 123)}),
        ("api_key", {"model": "openai/vision-model", "api_key": cast(Any, 123)}),
        ("api_base", {"model": "openai/vision-model", "api_base": cast(Any, 123)}),
        ("timeout", {"model": "openai/vision-model", "timeout": cast(Any, True)}),
        ("timeout", {"model": "openai/vision-model", "timeout": cast(Any, "120")}),
        (
            "max_retries",
            {"model": "openai/vision-model", "max_retries": cast(Any, True)},
        ),
        (
            "max_retries",
            {"model": "openai/vision-model", "max_retries": cast(Any, 2.5)},
        ),
        (
            "max_retries",
            {"model": "openai/vision-model", "max_retries": cast(Any, "2")},
        ),
    ],
)
def test_vision_config_rejects_invalid_field_types(
    field_name: str,
    kwargs: dict[str, Any],
) -> None:
    with pytest.raises(TypeError, match=field_name):
        VisionConfig(**kwargs)
