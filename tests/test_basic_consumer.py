import asyncio
import importlib.util
import inspect
import tomllib
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

from opendocs import ParseOptions

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/basic_consumer"


def _load_example() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "opendocs_basic_consumer",
        EXAMPLE / "main.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_example_depends_on_the_release_not_repository_internals() -> None:
    project = tomllib.loads((EXAMPLE / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    serialized = (EXAMPLE / "pyproject.toml").read_text(encoding="utf-8")

    assert dependencies == ["opendocs-sdk==0.1.0"]
    assert "path =" not in serialized
    assert "../" not in serialized


def test_sync_example_parses_one_local_file_to_markdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_example()
    source = tmp_path / "notes.txt"
    source.write_text("source", encoding="utf-8")
    output = tmp_path / "out"
    monkeypatch.setattr(module, "parse", lambda path, **kwargs: f"# {Path(path).stem}\n")

    written = module.parse_sync(
        source,
        output,
        options=ParseOptions(vision_concurrency=3),
        vision=None,
    )

    assert written == output / "notes.md"
    assert written.read_text(encoding="utf-8") == "# notes\n"


@pytest.mark.asyncio
async def test_async_example_uses_caller_semaphore_for_documents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_example()
    sources = [tmp_path / f"doc-{index}.txt" for index in range(5)]
    for source in sources:
        source.write_text("source", encoding="utf-8")
    active = 0
    peak = 0
    observed_vision_limits: list[int] = []

    async def fake_aparse(
        path: Path,
        *,
        options: ParseOptions,
        vision: object,
    ) -> str:
        del path, vision
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        observed_vision_limits.append(options.vision_concurrency)
        try:
            await asyncio.sleep(0.01)
            return "# parsed\n"
        finally:
            active -= 1

    monkeypatch.setattr(module, "aparse", fake_aparse)
    written = await module.parse_many(
        sources,
        tmp_path / "out",
        document_concurrency=2,
        options=ParseOptions(vision_concurrency=4),
        vision=None,
    )

    assert len(written) == 5
    assert peak == 2
    assert observed_vision_limits == [4] * 5


@pytest.mark.parametrize(
    "source",
    [
        "https://example.com/document.pdf",
        "http://example.com/document.pdf",
        "s3://bucket/document.pdf",
        "oss://bucket/document.pdf",
    ],
)
def test_example_rejects_remote_sources(source: str) -> None:
    module = _load_example()

    with pytest.raises(ValueError, match="local"):
        module.require_local_path(source)


def test_example_has_no_application_or_private_sdk_coupling() -> None:
    source = (EXAMPLE / "main.py").read_text(encoding="utf-8")
    module = _load_example()

    assert "43x" not in source
    assert "langchain" not in source.lower()
    assert "opendocs." not in source
    assert "requests" not in source
    assert cast(str, inspect.getdoc(module)).startswith("Independent")


def test_public_docs_match_alpha_platform_and_concurrency_contracts() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    combined = "\n".join((readme, changelog, contributing))

    assert "Alpha" in combined
    assert "Ubuntu" in combined
    assert "macOS" in combined
    assert "Windows" in combined
    assert "vision_concurrency" in combined
    assert "asyncio.Semaphore" in combined
    assert "performance SLA" in combined
    assert "opendocs-sdk==0.1.0" in combined
