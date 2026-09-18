"""Shared pytest configuration."""
import pytest

def pytest_addoption(parser):
    parser.addoption("--run-live", action="store_true", default=False,
                     help="Run live LLM tests requiring GEMINI_API_KEY")

def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-live"):
        skip_live = pytest.mark.skip(reason="need --run-live to run")
        for item in items:
            if "live" in item.keywords:
                item.add_marker(skip_live)
