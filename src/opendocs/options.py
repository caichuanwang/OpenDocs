from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ParseOptions:
    timeout: float = 900
    max_pages: int = 300
    max_output_chars: int = 400_000
    vision_concurrency: int = 4

    def __post_init__(self) -> None:
        for name in (
            "timeout",
            "max_pages",
            "max_output_chars",
            "vision_concurrency",
        ):
            value = getattr(self, name)
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
        if not self.model.strip():
            raise ValueError("model must not be blank")
        if self.timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if self.max_retries < 0:
            raise ValueError("max_retries must be greater than or equal to zero")
