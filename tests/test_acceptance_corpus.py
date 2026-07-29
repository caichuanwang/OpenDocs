import hashlib
import re
import tomllib
from pathlib import Path
from typing import TypedDict, cast

import pytest

from tests import conftest as test_conftest


class CorpusFile(TypedDict):
    name: str
    sha256: str
    milestone: str


def _manifest_usage_error(manifest: Path, message: str) -> pytest.UsageError:
    return pytest.UsageError(f"{manifest}: {message}")


def _valid_name(name: str) -> bool:
    return (
        bool(name)
        and name not in {".", ".."}
        and Path(name).name == name
        and "/" not in name
        and "\\" not in name
    )


def _entries(manifest: Path | None = None) -> list[CorpusFile]:
    if manifest is None:
        manifest = Path(__file__).with_name("corpus.example.toml")

    try:
        with manifest.open("rb") as handle:
            payload = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise _manifest_usage_error(manifest, f"malformed TOML in {manifest}: {exc}") from exc

    if payload.get("schema_version") != 1:
        raise _manifest_usage_error(manifest, "schema_version must be 1")

    files = payload.get("files")
    if not isinstance(files, list):
        raise _manifest_usage_error(manifest, "files must be a list")

    entries: list[CorpusFile] = []
    for index, entry in enumerate(files):
        if not isinstance(entry, dict):
            raise _manifest_usage_error(manifest, f"files[{index}] must be a table")

        name = entry.get("name")
        sha256 = entry.get("sha256")
        milestone = entry.get("milestone")
        if not all(isinstance(value, str) and value for value in (name, sha256, milestone)):
            raise _manifest_usage_error(
                manifest,
                f"files[{index}] must define non-empty name, sha256, and milestone",
            )
        name = cast(str, name)
        sha256 = cast(str, sha256)
        milestone = cast(str, milestone)
        if not _valid_name(name):
            raise _manifest_usage_error(
                manifest,
                f"files[{index}].name must be a non-empty basename",
            )
        if not re.fullmatch(r"[0-9a-f]{64}", sha256):
            raise _manifest_usage_error(
                manifest,
                f"files[{index}] must define a lowercase 64-character hex sha256",
            )

        entries.append(cast(CorpusFile, entry))

    return entries


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_local_manifest_rejects_malformed_toml(tmp_path: Path) -> None:
    manifest = tmp_path / "corpus.local.toml"
    manifest.write_text('corpus_dir = "Downloads"\n[', encoding="utf-8")

    with pytest.raises(pytest.UsageError, match=f"malformed TOML in {manifest}"):
        test_conftest._resolve_local_corpus_dir(manifest)


def test_local_manifest_rejects_whitespace_only_corpus_dir(tmp_path: Path) -> None:
    manifest = tmp_path / "corpus.local.toml"
    manifest.write_text('corpus_dir = "   "\n', encoding="utf-8")

    with pytest.raises(
        pytest.UsageError,
        match="must define a non-empty corpus_dir",
    ):
        test_conftest._resolve_local_corpus_dir(manifest)


def test_public_manifest_rejects_malformed_toml(tmp_path: Path) -> None:
    manifest = tmp_path / "corpus.example.toml"
    manifest.write_text('schema_version = 1\n[[files]]\nname = "doc.pdf"\n[', encoding="utf-8")

    with pytest.raises(pytest.UsageError, match=f"malformed TOML in {manifest}"):
        _entries(manifest)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("sha256", "ABC", "must define a lowercase 64-character hex sha256"),
        ("name", "/tmp/doc.pdf", "name must be a non-empty basename"),
        ("name", "../doc.pdf", "name must be a non-empty basename"),
        ("name", "nested/doc.pdf", "name must be a non-empty basename"),
    ],
)
def test_public_manifest_rejects_invalid_entry_fields(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    entry = {
        "name": "doc.pdf",
        "sha256": "a" * 64,
        "milestone": "M1",
    }
    entry[field] = value
    manifest = tmp_path / "corpus.example.toml"
    manifest.write_text(
        "\n".join(
            [
                "schema_version = 1",
                "",
                "[[files]]",
                f'name = "{entry["name"]}"',
                f'sha256 = "{entry["sha256"]}"',
                f'milestone = "{entry["milestone"]}"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(pytest.UsageError, match=message):
        _entries(manifest)


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
