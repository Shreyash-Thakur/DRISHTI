from ml.config import Settings, get_settings


def test_default_settings_match_port_convention():
    settings = Settings()
    assert settings.port == 8200
    assert settings.backend_ws_url == "ws://localhost:8000/ws/live"
    assert settings.precursor_threshold == 0.5


def test_get_settings_is_cached():
    assert get_settings() is get_settings()
