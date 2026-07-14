import yaml

from twin.addressing import load_topology
from twin.config import Settings
from twin.generate import write_lab

TOPO = load_topology("data/topology.json")


def test_write_lab_produces_clab_and_per_node_configs(tmp_path):
    settings = Settings(out_dir=tmp_path)
    clab_path = write_lab(TOPO, settings)
    assert clab_path.exists()
    clab = yaml.safe_load(clab_path.read_text())          # valid YAML
    assert clab["name"] == "drishti"
    for node in TOPO["nodes"]:
        cfg = tmp_path / "configs" / node["id"] / "frr.conf"
        assert cfg.exists()
        assert f"hostname {node['id']}" in cfg.read_text()
