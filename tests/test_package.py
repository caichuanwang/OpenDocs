import opendocs


def test_package_exposes_a_version() -> None:
    assert opendocs.__version__ == "0.1.0"
