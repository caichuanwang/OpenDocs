from importlib.metadata import metadata

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet

import opendocs


def test_package_exposes_a_version() -> None:
    assert opendocs.__version__ == "0.1.0"


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
