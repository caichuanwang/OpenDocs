import json
import os
import shutil
import subprocess
import sys
from dataclasses import fields
from importlib.metadata import metadata
from importlib.resources import files
from inspect import Parameter, signature
from pathlib import Path
from typing import get_type_hints

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
    assert opendocs.__version__ == "0.2.0"


def test_package_public_all_stays_stable() -> None:
    assert opendocs.__all__ == EXPECTED_PUBLIC_ALL


def test_package_includes_the_typing_marker() -> None:
    assert files("opendocs").joinpath("py.typed").is_file()


def test_distribution_uses_unambiguous_pypi_name() -> None:
    distribution = metadata("opendocs-sdk")

    assert distribution["Name"] == "opendocs-sdk"
    assert distribution["Version"] == opendocs.__version__


def test_distribution_declares_the_accepted_alpha_metadata() -> None:
    distribution = metadata("opendocs-sdk")
    classifiers = distribution.get_all("Classifier") or []

    assert distribution["Requires-Python"] == ">=3.11"
    assert distribution["License-Expression"] == "MIT"
    assert "Development Status :: 3 - Alpha" in classifiers
    assert not any("Microsoft :: Windows" in classifier for classifier in classifiers)
    assert not any(classifier.startswith("Development Status :: 5") for classifier in classifiers)


def test_distribution_declares_locked_runtime_dependency_ranges() -> None:
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
    assert requirements["openpyxl"].specifier == SpecifierSet(">=3.1.5,<3.2")
    assert requirements["defusedxml"].specifier == SpecifierSet(">=0.7.1,<1")


def test_public_parse_contracts_stay_compatible_for_the_alpha_line() -> None:
    expected_parameters = {
        "source": (Parameter.POSITIONAL_OR_KEYWORD, Parameter.empty),
        "options": (Parameter.KEYWORD_ONLY, None),
        "vision": (Parameter.KEYWORD_ONLY, None),
    }

    for function in (opendocs.parse, opendocs.aparse):
        parameters = signature(function).parameters
        assert {
            name: (parameter.kind, parameter.default) for name, parameter in parameters.items()
        } == expected_parameters
        assert get_type_hints(function)["return"] is str


def test_public_options_keep_the_accepted_defaults_and_no_global_caps() -> None:
    options = opendocs.ParseOptions()

    assert options.timeout == 900
    assert options.max_pages == 300
    assert options.max_output_chars == 400_000
    assert options.vision_concurrency == 4
    assert opendocs.VisionConfig(model="test").timeout == 120
    assert opendocs.VisionConfig(model="test").max_retries == 2

    forbidden_fields = {
        "global_semaphore",
        "max_vision_calls",
        "max_model_calls",
        "max_tokens",
        "max_cost",
        "max_currency",
    }
    assert forbidden_fields.isdisjoint(field.name for field in fields(opendocs.ParseOptions))
    assert forbidden_fields.isdisjoint(field.name for field in fields(opendocs.VisionConfig))


def test_built_wheel_installs_xlsx_dependencies_and_parser_in_an_isolated_environment(
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
    environment = tmp_path / "isolated"
    subprocess.run(
        [uv_executable, "venv", "--python", sys.executable, str(environment)],
        check=True,
        cwd=tmp_path,
    )
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run(
        [uv_executable, "pip", "install", "--python", str(python), str(wheel_path)],
        check=True,
        cwd=tmp_path,
    )
    completed = subprocess.run(
        [
            str(python),
            "-c",
            (
                "import json; import defusedxml; import docx; import opendocs; import openpyxl; "
                "import opendocs.parsers.xlsx as xlsx; import pptx; "
                "print(json.dumps({'opendocs_file': opendocs.__file__, "
                "'version': opendocs.__version__, 'defusedxml_file': defusedxml.__file__, "
                "'docx': docx.__name__, 'openpyxl_file': openpyxl.__file__, "
                "'pptx': pptx.__name__, 'xlsx_file': xlsx.__file__}))"
            ),
        ],
        check=True,
        capture_output=True,
        cwd=tmp_path,
        env=os.environ | {"PYTHONNOUSERSITE": "1"},
        text=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["version"] == opendocs.__version__
    assert payload["docx"] == "docx"
    assert payload["pptx"] == "pptx"
    assert str(environment) in payload["opendocs_file"]
    assert str(environment) in payload["xlsx_file"]
    assert str(environment) in payload["openpyxl_file"]
    assert str(environment) in payload["defusedxml_file"]
