from __future__ import annotations

import argparse
import hashlib
import tarfile
import tomllib
from dataclasses import dataclass
from email.message import Message
from email.parser import BytesParser
from email.policy import default
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

EXPECTED_DEPENDENCIES = {
    "defusedxml<1,>=0.7.1",
    "litellm<2,>=1.93",
    "openpyxl<3.2,>=3.1.5",
    "pdfplumber<0.12,>=0.11.10",
    "pillow<13,>=12.3",
    "python-docx<2,>=1.1.2",
    "python-pptx<2,>=1.0.2",
}
REQUIRED_CLASSIFIERS = {
    "Development Status :: 3 - Alpha",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
}
PROHIBITED_PATH_PARTS = {
    ".git",
    ".github",
    ".omx",
    ".pypirc",
    ".pytest_cache",
    ".ruff_cache",
    ".serena",
    ".tox",
    ".venv",
    "__pycache__",
}
PROHIBITED_FRAGMENTS = {
    "api_key",
    "corpus.local",
    "credentials",
    "manifest.local",
    "m1-replay.local",
    "m2-acceptance.local",
    "m2-replay.local",
    "provider_payload",
}


class ArtifactError(ValueError):
    """Raised when release distributions violate the accepted package boundary."""


@dataclass(frozen=True, slots=True)
class ArtifactInspection:
    wheel: Path
    sdist: Path
    checksums: Path
    version: str


def inspect_release_artifacts(
    directory: Path,
    *,
    expected_version: str,
    verify_existing_checksums: bool = False,
) -> ArtifactInspection:
    wheels = sorted(directory.glob("*.whl"))
    sdists = sorted(directory.glob("*.tar.gz"))
    if len(wheels) != 1:
        raise ArtifactError("release directory must contain exactly one wheel")
    if len(sdists) != 1:
        raise ArtifactError("release directory must contain exactly one source distribution")
    wheel = wheels[0]
    sdist = sdists[0]
    expected_wheel_prefix = f"opendocs_sdk-{expected_version}-"
    expected_sdist_name = f"opendocs_sdk-{expected_version}.tar.gz"
    if not wheel.name.startswith(expected_wheel_prefix):
        raise ArtifactError(f"wheel name does not match version {expected_version}")
    if sdist.name != expected_sdist_name:
        raise ArtifactError(f"source distribution name does not match version {expected_version}")

    wheel_metadata = _inspect_wheel(wheel, expected_version)
    sdist_metadata = _inspect_sdist(sdist, expected_version)
    if _metadata_contract(wheel_metadata) != _metadata_contract(sdist_metadata):
        raise ArtifactError("wheel and source distribution metadata do not match")

    checksums = directory / "SHA256SUMS"
    if not verify_existing_checksums:
        _write_checksums((wheel, sdist), checksums)
    verify_checksums(directory, checksums)
    return ArtifactInspection(
        wheel=wheel,
        sdist=sdist,
        checksums=checksums,
        version=expected_version,
    )


def verify_checksums(directory: Path, checksums_path: Path) -> None:
    try:
        lines = checksums_path.read_text(encoding="ascii").splitlines()
    except OSError as error:
        raise ArtifactError("cannot read SHA256SUMS") from error
    expected_files = {
        path.name for pattern in ("*.whl", "*.tar.gz") for path in directory.glob(pattern)
    }
    seen: set[str] = set()
    for line in lines:
        parts = line.split("  ", maxsplit=1)
        if len(parts) != 2:
            raise ArtifactError("SHA256SUMS contains a malformed line")
        digest, filename = parts
        if filename in seen or filename not in expected_files:
            raise ArtifactError("SHA256SUMS contains an unexpected filename")
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ArtifactError("SHA256SUMS contains an invalid digest")
        path = directory / filename
        if _sha256(path) != digest:
            raise ArtifactError(f"checksum mismatch for {filename}")
        seen.add(filename)
    if seen != expected_files:
        raise ArtifactError("SHA256SUMS does not cover exactly the release artifacts")


def _inspect_wheel(path: Path, expected_version: str) -> Message:
    with ZipFile(path) as archive:
        names = archive.namelist()
        _validate_archive_names(names)
        _reject_prohibited_paths(names)
        metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise ArtifactError("wheel must contain exactly one METADATA file")
        metadata_name = metadata_names[0]
        dist_info = metadata_name.split("/", maxsplit=1)[0]
        top_level = {PurePosixPath(name).parts[0] for name in names}
        if top_level != {"opendocs", dist_info}:
            raise ArtifactError("wheel contains an unexpected top-level package")
        required = {
            "opendocs/__init__.py",
            "opendocs/parsers/xlsx/__init__.py",
            "opendocs/parsers/xlsx/parser.py",
            "opendocs/parsers/xlsx/preflight.py",
            "opendocs/py.typed",
            f"{dist_info}/WHEEL",
            f"{dist_info}/RECORD",
            f"{dist_info}/licenses/LICENSE",
        }
        if not required <= set(names):
            raise ArtifactError(f"wheel is missing required files: {sorted(required - set(names))}")
        metadata = BytesParser(policy=default).parsebytes(archive.read(metadata_name))
    _validate_metadata(metadata, expected_version)
    return metadata


def _inspect_sdist(path: Path, expected_version: str) -> Message:
    with tarfile.open(path, mode="r:gz") as archive:
        members = archive.getmembers()
        names = [member.name for member in members]
        _validate_archive_names(names)
        _reject_prohibited_paths(names)
        roots = {PurePosixPath(name).parts[0] for name in names}
        expected_root = f"opendocs_sdk-{expected_version}"
        if roots != {expected_root}:
            raise ArtifactError("source distribution has an unexpected archive root")
        required = {
            f"{expected_root}/CHANGELOG.md",
            f"{expected_root}/LICENSE",
            f"{expected_root}/README.md",
            f"{expected_root}/pyproject.toml",
            f"{expected_root}/src/opendocs/__init__.py",
            f"{expected_root}/src/opendocs/parsers/xlsx/__init__.py",
            f"{expected_root}/src/opendocs/parsers/xlsx/parser.py",
            f"{expected_root}/src/opendocs/parsers/xlsx/preflight.py",
            f"{expected_root}/src/opendocs/py.typed",
            f"{expected_root}/PKG-INFO",
        }
        if not required <= set(names):
            raise ArtifactError(
                f"source distribution is missing required files: {sorted(required - set(names))}"
            )
        source_packages = {
            PurePosixPath(name).parts[2]
            for name in names
            if len(PurePosixPath(name).parts) >= 3 and PurePosixPath(name).parts[1] == "src"
        }
        if source_packages != {"opendocs"}:
            raise ArtifactError("source distribution contains an unexpected source package")
        member = archive.getmember(f"{expected_root}/PKG-INFO")
        extracted = archive.extractfile(member)
        if extracted is None:
            raise ArtifactError("source distribution PKG-INFO is unreadable")
        metadata = BytesParser(policy=default).parsebytes(extracted.read())
    _validate_metadata(metadata, expected_version)
    return metadata


def _validate_metadata(metadata: Message, expected_version: str) -> None:
    expected = {
        "Name": "opendocs-sdk",
        "Version": expected_version,
        "Requires-Python": ">=3.11",
        "License-Expression": "MIT",
        "Description-Content-Type": "text/markdown",
    }
    for key, value in expected.items():
        if metadata[key] != value:
            raise ArtifactError(f"metadata {key} does not match {value!r}")
    classifiers = set(metadata.get_all("Classifier") or ())
    if not classifiers >= REQUIRED_CLASSIFIERS:
        raise ArtifactError("metadata classifiers are incomplete")
    if any("Microsoft :: Windows" in classifier for classifier in classifiers):
        raise ArtifactError("metadata must not claim Windows support")
    if set(metadata.get_all("Requires-Dist") or ()) != EXPECTED_DEPENDENCIES:
        raise ArtifactError("runtime dependencies do not match the accepted release contract")


def _metadata_contract(metadata: Message) -> tuple[object, ...]:
    return (
        metadata["Name"],
        metadata["Version"],
        metadata["Requires-Python"],
        metadata["License-Expression"],
        metadata["Description-Content-Type"],
        tuple(sorted(metadata.get_all("Classifier") or ())),
        tuple(sorted(metadata.get_all("Requires-Dist") or ())),
    )


def _validate_archive_names(names: list[str]) -> None:
    for name in names:
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or "\\" in name:
            raise ArtifactError("archive contains an unsafe path")


def _reject_prohibited_paths(names: list[str]) -> None:
    for name in names:
        lowered = name.lower()
        parts = {part.lower() for part in PurePosixPath(name).parts}
        if (
            parts & PROHIBITED_PATH_PARTS
            or any(fragment in lowered for fragment in PROHIBITED_FRAGMENTS)
            or "/benchmarks/document_parsing/private/" in f"/{lowered}/"
            or "/benchmarks/document_parsing/runs/" in f"/{lowered}/"
            or "/tests/corpus/" in f"/{lowered}/"
        ):
            raise ArtifactError(f"archive contains prohibited path {name!r}")


def _write_checksums(paths: tuple[Path, ...], output: Path) -> None:
    lines = [f"{_sha256(path)}  {path.name}" for path in sorted(paths)]
    output.write_text("\n".join(lines) + "\n", encoding="ascii")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate OpenDocs release distributions.")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--version")
    parser.add_argument(
        "--verify-checksums",
        action="store_true",
        help="verify the existing SHA256SUMS file instead of replacing it",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    expected_version = args.version or _project_version()
    result = inspect_release_artifacts(
        args.directory,
        expected_version=expected_version,
        verify_existing_checksums=args.verify_checksums,
    )
    print(f"validated {result.wheel.name} and {result.sdist.name}")
    print(f"checksums: {result.checksums}")
    return 0


def _project_version() -> str:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    with pyproject.open("rb") as source:
        payload = tomllib.load(source)
    return str(payload["project"]["version"])


if __name__ == "__main__":
    raise SystemExit(main())
