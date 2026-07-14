from rca.config import Settings, get_settings


def test_default_settings_match_port_convention():
    settings = Settings()
    assert settings.port == 8300
    assert settings.backend_ws_url == "ws://localhost:8000/ws/live"
    assert settings.cascade_max_hops == 2


def test_get_settings_is_cached():
    assert get_settings() is get_settings()
