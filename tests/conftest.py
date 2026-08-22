import pytest


@pytest.fixture(autouse=True)
def isolate_test_environment(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """Isolate XDG directories for all tests so real user data is never touched or polluted."""
    tmp_data = tmp_path_factory.mktemp("xdg_data")
    tmp_config = tmp_path_factory.mktemp("xdg_config")
    tmp_cache = tmp_path_factory.mktemp("xdg_cache")

    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_data))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_config))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_cache))
