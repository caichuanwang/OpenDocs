import shutil
import subprocess
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from scripts.check_release_artifacts import (
    ArtifactError,
    inspect_release_artifacts,
    main,
    verify_checksums,
)
from scripts.release_smoke import run_smoke

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def release_dist(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("release-dist")
    uv = shutil.which("uv")
    assert uv is not None
    subprocess.run(
        [uv, "build", "--out-dir", str(output)],
        check=True,
        cwd=ROOT,
    )
    return output


def test_artifact_checker_requires_exact_wheel_and_sdist_with_matching_metadata(
    release_dist: Path,
) -> None:
    result = inspect_release_artifacts(release_dist, expected_version="0.1.0")

    assert result.wheel.name == "opendocs_sdk-0.1.0-py3-none-any.whl"
    assert result.sdist.name == "opendocs_sdk-0.1.0.tar.gz"
    assert result.version == "0.1.0"
    assert result.checksums.name == "SHA256SUMS"
    verify_checksums(release_dist, result.checksums)


def test_artifact_checker_rejects_missing_or_duplicate_distributions(
    release_dist: Path,
    tmp_path: Path,
) -> None:
    wheel = next(release_dist.glob("*.whl"))
    shutil.copy2(wheel, tmp_path / wheel.name)
    with pytest.raises(ArtifactError, match="source distribution"):
        inspect_release_artifacts(tmp_path, expected_version="0.1.0")

    sdist = next(release_dist.glob("*.tar.gz"))
    shutil.copy2(sdist, tmp_path / sdist.name)
    shutil.copy2(wheel, tmp_path / "opendocs_sdk-0.1.0-2-py3-none-any.whl")
    with pytest.raises(ArtifactError, match="exactly one wheel"):
        inspect_release_artifacts(tmp_path, expected_version="0.1.0")


def test_artifact_checker_rejects_version_mismatch(release_dist: Path) -> None:
    with pytest.raises(ArtifactError, match=r"0\.1\.1"):
        inspect_release_artifacts(release_dist, expected_version="0.1.1")


def test_artifact_checker_cli_verifies_existing_checksums_without_replacing_them(
    release_dist: Path,
    tmp_path: Path,
) -> None:
    for pattern in ("*.whl", "*.tar.gz"):
        shutil.copy2(next(release_dist.glob(pattern)), tmp_path)
    result = inspect_release_artifacts(tmp_path, expected_version="0.1.0")
    invalid = result.checksums.read_text(encoding="ascii").replace(
        result.checksums.read_text(encoding="ascii").split("  ", maxsplit=1)[0],
        "0" * 64,
        1,
    )
    result.checksums.write_text(invalid, encoding="ascii")

    with pytest.raises(ArtifactError, match="checksum mismatch"):
        main([str(tmp_path), "--verify-checksums"])

    assert result.checksums.read_text(encoding="ascii") == invalid


def test_artifact_checker_rejects_private_or_unexpected_wheel_content(
    release_dist: Path,
    tmp_path: Path,
) -> None:
    wheel = next(release_dist.glob("*.whl"))
    unsafe_wheel = tmp_path / wheel.name
    shutil.copy2(wheel, unsafe_wheel)
    with ZipFile(unsafe_wheel, "a", compression=ZIP_DEFLATED) as archive:
        archive.writestr("tests/corpus/private.pdf", b"private")
    shutil.copy2(next(release_dist.glob("*.tar.gz")), tmp_path)

    with pytest.raises(ArtifactError, match="prohibited"):
        inspect_release_artifacts(tmp_path, expected_version="0.1.0")


def test_release_smoke_covers_native_formats_without_model_configuration(
    tmp_path: Path,
) -> None:
    results = run_smoke(tmp_path, expected_version="0.1.0")

    assert results == {
        "async": True,
        "docx": True,
        "markdown": True,
        "pdf": True,
        "pptx": True,
        "text": True,
    }
