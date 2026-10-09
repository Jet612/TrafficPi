import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--hardware",
        action="store_true",
        default=False,
        help="also run tests that need the camera and the Hailo accelerator",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--hardware"):
        return
    skip = pytest.mark.skip(reason="needs hardware; run with --hardware")
    for item in items:
        if "hardware" in item.keywords:
            item.add_marker(skip)
