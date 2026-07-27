import hashlib
import tomllib
from pathlib import Path
from typing import TypedDict, cast

import pytest


class CorpusFile(TypedDict):
    name: str
    sha256: str
    milestone: str


def _usage_error(message: str) -> pytest.UsageError:
    return pytest.UsageError(f"tests/corpus.example.toml: {message}")


def _entries() -> list[CorpusFile]:
    manifest = Path(__file__).with_name("corpus.example.toml")
    with manifest.open("rb") as handle:
        payload = tomllib.load(handle)

    if payload.get("schema_version") != 1:
        raise _usage_error("schema_version must be 1")

    files = payload.get("files")
    if not isinstance(files, list):
        raise _usage_error("files must be a list")

    entries: list[CorpusFile] = []
    for index, entry in enumerate(files):
        if not isinstance(entry, dict):
            raise _usage_error(f"files[{index}] must be a table")

        name = entry.get("name")
        sha256 = entry.get("sha256")
        milestone = entry.get("milestone")
        if not all(isinstance(value, str) and value for value in (name, sha256, milestone)):
            raise _usage_error(f"files[{index}] must define non-empty name, sha256, and milestone")

        entries.append(cast(CorpusFile, entry))

    return entries


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@pytest.mark.parametrize("entry", _entries(), ids=lambda entry: entry["name"])
def test_private_corpus_matches_manifest(
    entry: CorpusFile,
    corpus_dir: Path | None,
) -> None:
    if corpus_dir is None:
        pytest.skip("pass --corpus-dir to verify the private acceptance corpus")

    path = corpus_dir / entry["name"]
    assert path.is_file(), f"missing acceptance file: {path}"
    assert _sha256(path) == entry["sha256"], f"hash mismatch: {path}"
