from copilot.config import Settings, get_settings


def test_defaults():
    s = Settings()
    assert s.port == 8400
    assert s.ollama_url == "http://localhost:11434"
    assert s.top_k == 3


def test_cached():
    assert get_settings() is get_settings()
