import tomllib
from pathlib import Path

import pytest

_LOCAL_MANIFEST = Path(__file__).with_name("corpus.local.toml")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--corpus-dir",
        dest="corpus_dir",
        action="store",
        type=str,
        default=None,
        help="private corpus directory, or @local to read tests/corpus.local.toml",
    )


def _resolve_local_corpus_dir(local_manifest: Path | None = None) -> str:
    if local_manifest is None:
        local_manifest = _LOCAL_MANIFEST

    if not local_manifest.is_file():
        raise pytest.UsageError(f"local corpus manifest not found: {local_manifest}")

    with local_manifest.open("rb") as handle:
        payload = tomllib.load(handle)

    value = payload.get("corpus_dir")
    if not isinstance(value, str) or not value:
        raise pytest.UsageError("corpus.local.toml must define a non-empty corpus_dir")

    return value


def _resolve_corpus_dir(value: str | None, local_manifest: Path | None = None) -> Path | None:
    if value is None:
        return None
    if value == "@local":
        value = _resolve_local_corpus_dir(local_manifest)
    return Path(value).expanduser()


@pytest.fixture(scope="session")
def corpus_dir(pytestconfig: pytest.Config) -> Path | None:
    return _resolve_corpus_dir(pytestconfig.getoption("corpus_dir"))
