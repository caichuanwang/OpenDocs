from __future__ import annotations

from dataclasses import dataclass


def _require_real_number(name: str, value: object) -> float | int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be an int or float")
    return value


def _require_int(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int")
    return value


def _require_string(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a str")
    return value


def _require_optional_string(name: str, value: object) -> str | None:
    if value is None:
        return None
    return _require_string(name, value)


@dataclass(frozen=True, slots=True)
class ParseOptions:
    timeout: float = 900
    max_pages: int = 300
    max_output_chars: int = 400_000
    vision_concurrency: int = 4

    def __post_init__(self) -> None:
        timeout = _require_real_number("timeout", self.timeout)
        max_pages = _require_int("max_pages", self.max_pages)
        max_output_chars = _require_int("max_output_chars", self.max_output_chars)
        vision_concurrency = _require_int("vision_concurrency", self.vision_concurrency)

        for name, value in (
            ("timeout", timeout),
            ("max_pages", max_pages),
            ("max_output_chars", max_output_chars),
            ("vision_concurrency", vision_concurrency),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be greater than zero")


@dataclass(frozen=True, slots=True)
class VisionConfig:
    model: str
    api_key: str | None = None
    api_base: str | None = None
    timeout: float = 120
    max_retries: int = 2

    def __post_init__(self) -> None:
        model = _require_string("model", self.model)
        _require_optional_string("api_key", self.api_key)
        _require_optional_string("api_base", self.api_base)
        timeout = _require_real_number("timeout", self.timeout)
        max_retries = _require_int("max_retries", self.max_retries)

        if not model.strip():
            raise ValueError("model must not be blank")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if max_retries < 0:
            raise ValueError("max_retries must be greater than or equal to zero")
