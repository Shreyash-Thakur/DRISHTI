"""CLI: data/topology.json -> <out_dir>/drishti.clab.yml + per-node FRR configs.
Run: `python -m twin.generate`. Deploy on a containerlab host with the printed
command (containerlab + an FRR image are NOT required to generate)."""
from __future__ import annotations

from pathlib import Path

from twin.addressing import load_topology
from twin.clab import build_clab, to_yaml
from twin.config import Settings, get_settings
from twin.frr import frr_config


def write_lab(topology: dict, settings: Settings) -> Path:
    out = Path(settings.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    clab = build_clab(topology, settings.node_image, settings.mgmt_subnet)
    clab_path = out / "drishti.clab.yml"
    clab_path.write_text(to_yaml(clab))
    for node in topology["nodes"]:
        cfg_dir = out / "configs" / node["id"]
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "frr.conf").write_text(frr_config(node["id"], topology, settings.asn))
    return clab_path


def main() -> None:
    settings = get_settings()
    topology = load_topology(settings.topology_path)
    clab_path = write_lab(topology, settings)
    print(f"wrote {clab_path} and per-node FRR configs under {settings.out_dir}/configs/")
    print(f"deploy on a containerlab host with: containerlab deploy -t {clab_path}")


if __name__ == "__main__":
    main()
