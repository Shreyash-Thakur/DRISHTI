from twin.config import Settings, get_settings


def test_defaults():
    s = Settings()
    assert s.asn == 65000
    assert str(s.topology_path).endswith("topology.json")


def test_cached():
    assert get_settings() is get_settings()
