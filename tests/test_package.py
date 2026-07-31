import json
import os
import shutil
import subprocess
import sys
from importlib.metadata import metadata
from pathlib import Path

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet

import opendocs

EXPECTED_PUBLIC_ALL = [
    "CorruptDocumentError",
    "DocumentTimeoutError",
    "DocumentTypeMismatchError",
    "InvalidSourceError",
    "LimitExceededError",
    "ModelAuthenticationError",
    "ModelInvalidRequestError",
    "ModelInvalidResponseError",
    "ModelPermissionError",
    "ModelUnavailableError",
    "NoUsableContentError",
    "OpenDocsError",
    "OpenDocsErrorCode",
    "OpenDocsWarning",
    "ParseOptions",
    "RuntimeDependencyError",
    "SyncInAsyncContextError",
    "UnsupportedDocumentError",
    "VisionConfig",
    "VisionRequiredError",
    "__version__",
    "aparse",
    "parse",
]


def test_package_exposes_a_version() -> None:
    assert opendocs.__version__ == "0.1.0"


def test_package_public_all_stays_stable() -> None:
    assert opendocs.__all__ == EXPECTED_PUBLIC_ALL


def test_distribution_uses_unambiguous_pypi_name() -> None:
    distribution = metadata("opendocs-sdk")

    assert distribution["Name"] == "opendocs-sdk"
    assert distribution["Version"] == opendocs.__version__


def test_distribution_declares_locked_m1_runtime_dependency_ranges() -> None:
    requirements = {
        requirement.name.lower(): requirement
        for value in metadata("opendocs-sdk").get_all("Requires-Dist") or ()
        if (requirement := Requirement(value)).marker is None
    }

    assert requirements["pillow"].specifier == SpecifierSet(">=12.3,<13")
    assert requirements["pdfplumber"].specifier == SpecifierSet(">=0.11.10,<0.12")
    assert requirements["litellm"].specifier == SpecifierSet(">=1.93,<2")
    assert requirements["python-docx"].specifier == SpecifierSet(">=1.1.2,<2")
    assert requirements["python-pptx"].specifier == SpecifierSet(">=1.0.2,<2")


def test_built_wheel_imports_office_dependencies_from_an_isolated_path(
    tmp_path: Path,
) -> None:
    uv_executable = shutil.which("uv")
    assert uv_executable is not None
    subprocess.run(
        [uv_executable, "build", "--wheel", "--out-dir", str(tmp_path / "dist")],
        check=True,
        cwd=Path(__file__).resolve().parents[1],
    )
    wheel_path = next((tmp_path / "dist").glob("opendocs_sdk-*.whl"))
    env = os.environ | {"PYTHONPATH": str(wheel_path), "PYTHONNOUSERSITE": "1"}
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json; import docx; import opendocs; import pptx; "
                "print(json.dumps({'opendocs_file': opendocs.__file__, "
                "'version': opendocs.__version__, 'docx': docx.__name__, 'pptx': pptx.__name__}))"
            ),
        ],
        check=True,
        capture_output=True,
        cwd=tmp_path,
        env=env,
        text=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["version"] == opendocs.__version__
    assert payload["docx"] == "docx"
    assert payload["pptx"] == "pptx"
    assert ".whl/" in payload["opendocs_file"]
