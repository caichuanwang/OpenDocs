import re
from pathlib import Path
from typing import cast

import yaml

ROOT = Path(__file__).resolve().parents[1]
CI_PATH = ROOT / ".github/workflows/ci.yml"
RELEASE_PATH = ROOT / ".github/workflows/release.yml"
PINNED_ACTION = re.compile(r"^\s*uses:\s*[^./][^@]*@[0-9a-f]{40}\s+#\s+v\S+\s*$")


def _workflow(path: Path) -> dict[str, object]:
    loaded = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(loaded, dict)
    return cast(dict[str, object], loaded)


def _jobs(path: Path) -> dict[str, dict[str, object]]:
    jobs = _workflow(path)["jobs"]
    assert isinstance(jobs, dict)
    return cast(dict[str, dict[str, object]], jobs)


def _assert_remote_actions_are_pinned(path: Path) -> None:
    action_lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.lstrip().startswith("uses:")
        and not line.split("uses:", 1)[1].strip().startswith("./")
    ]

    assert action_lines
    assert all(PINNED_ACTION.fullmatch(line) for line in action_lines)


def test_ci_covers_the_release_blocking_os_and_python_matrix() -> None:
    jobs = _jobs(CI_PATH)
    test_job = jobs["test"]
    strategy = cast(dict[str, object], test_job["strategy"])
    matrix = cast(dict[str, list[str]], strategy["matrix"])

    assert matrix["os"] == ["ubuntu-latest", "macos-latest"]
    assert matrix["python-version"] == ["3.11", "3.12", "3.13"]
    assert test_job["runs-on"] == "${{ matrix.os }}"


def test_ci_installs_poppler_and_keeps_all_public_gates() -> None:
    source = CI_PATH.read_text(encoding="utf-8")

    assert "sudo apt-get install --yes poppler-utils" in source
    assert "brew install poppler" in source
    for command in (
        "pytest -q",
        "ruff check .",
        "ruff format --check .",
        "ty check src tests benchmarks scripts examples",
        "scripts/check_release_artifacts.py dist",
        "scripts/check_release_artifacts.py dist --verify-checksums",
        "scripts/release_smoke.py",
    ):
        assert command in source
    assert 'OPENAI_API_KEY: ""' in source
    assert 'ANTHROPIC_API_KEY: ""' in source


def test_ci_builds_once_and_smokes_the_same_artifact_on_both_operating_systems() -> None:
    jobs = _jobs(CI_PATH)
    source = CI_PATH.read_text(encoding="utf-8")
    smoke = jobs["artifact-smoke"]
    strategy = cast(dict[str, object], smoke["strategy"])
    matrix = cast(dict[str, list[str]], strategy["matrix"])

    assert source.count("uv build") == 1
    assert matrix["os"] == ["ubuntu-latest", "macos-latest"]
    assert smoke["needs"] == "build"
    assert source.count("name: release-dists") == 2
    assert "dist/*.whl" in source
    assert "dist/*.tar.gz" in source
    assert "dist/SHA256SUMS" in source


def test_ci_cancels_superseded_non_tag_runs_and_pins_actions() -> None:
    workflow = _workflow(CI_PATH)
    concurrency = cast(dict[str, str], workflow["concurrency"])

    assert concurrency["cancel-in-progress"] == "${{ github.ref_type != 'tag' }}"
    _assert_remote_actions_are_pinned(CI_PATH)


def test_release_workflow_is_tag_only_and_validates_source_identity() -> None:
    workflow = _workflow(RELEASE_PATH)
    trigger = cast(dict[str, object], workflow["on"])
    push = cast(dict[str, list[str]], trigger["push"])
    source = RELEASE_PATH.read_text(encoding="utf-8")

    assert push["tags"] == ["v*"]
    assert "workflow_dispatch" not in trigger
    assert 'git merge-base --is-ancestor "$GITHUB_SHA" origin/master' in source
    assert 'test "$GITHUB_REF_NAME" = "v$package_version"' in source
    assert "docs/releases/v0.1.0-evidence.md" in source
    assert source.count("uv build") == 1


def test_release_promotes_one_artifact_through_testpypi_pypi_and_release() -> None:
    jobs = _jobs(RELEASE_PATH)
    source = RELEASE_PATH.read_text(encoding="utf-8")

    assert source.count("name: release-dists") == 5
    assert jobs["testpypi"]["environment"] == "testpypi"
    assert jobs["pypi"]["environment"] == "pypi"
    assert "https://test.pypi.org/legacy/" in source
    assert "--index-url https://test.pypi.org/simple/" in source
    assert 'pip" install "$testpypi_wheel"' in source
    assert "expected[wheel.name]" in source
    assert "opendocs-sdk==0.1.0" in source
    assert jobs["pypi"]["needs"] == ["build", "testpypi"]
    assert jobs["public-smoke"]["needs"] == "pypi"
    assert jobs["github-release"]["needs"] == ["build", "public-smoke"]


def test_release_permissions_are_minimal_and_use_trusted_publishing() -> None:
    workflow = _workflow(RELEASE_PATH)
    jobs = _jobs(RELEASE_PATH)
    source = RELEASE_PATH.read_text(encoding="utf-8")

    assert workflow["permissions"] == {"contents": "read"}
    assert jobs["testpypi"]["permissions"] == {
        "contents": "read",
        "id-token": "write",
    }
    assert jobs["pypi"]["permissions"] == {
        "contents": "read",
        "id-token": "write",
    }
    assert jobs["github-release"]["permissions"] == {"contents": "write"}
    assert "PYPI_TOKEN" not in source
    assert "username:" not in source
    assert "password:" not in source
    assert "secrets." not in source


def test_release_public_smoke_is_cross_platform_and_gates_github_release() -> None:
    jobs = _jobs(RELEASE_PATH)
    smoke = jobs["public-smoke"]
    strategy = cast(dict[str, object], smoke["strategy"])
    matrix = cast(dict[str, list[str]], strategy["matrix"])
    source = RELEASE_PATH.read_text(encoding="utf-8")

    assert matrix["os"] == ["ubuntu-latest", "macos-latest"]
    assert "--index-url https://pypi.org/simple" in source
    assert "gh release create" in source
    _assert_remote_actions_are_pinned(RELEASE_PATH)
