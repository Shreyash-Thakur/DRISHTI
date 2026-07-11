"""Synthesizes per-interface and per-tunnel metrics.

Baseline = role-dependent level + slow sinusoidal wobble + gaussian noise.
Latency, jitter and loss are *coupled to utilization* (they rise sharply once
a link runs hot), so a congestion_ramp fault naturally drags the other
metrics with it — exactly the correlated precursor signature ML needs.
"""
import math
import random
from dataclasses import dataclass
from datetime import datetime

from sim.faults import FaultEngine
from sim.topology import InterfaceSpec, Topology


@dataclass
class _InterfaceBaseline:
    spec: InterfaceSpec
    base_util: float
    base_latency: float
    phase: float  # radians, desynchronizes the wobble across interfaces


class MetricsGenerator:
    def __init__(self, topology: Topology, seed: int) -> None:
        self._topology = topology
        self._rng = random.Random(seed)
        self._interfaces = [
            _InterfaceBaseline(
                spec=spec,
                base_util=self._rng.uniform(30, 45) if spec.kind == "access" else self._rng.uniform(12, 22),
                base_latency=8.0 if spec.kind == "access" else 2.0,
                phase=self._rng.uniform(0, 2 * math.pi),
            )
            for spec in topology.interfaces
        ]

    def tick(self, now: datetime, faults: FaultEngine) -> tuple[list[dict], list[dict]]:
        return (
            [self._interface_sample(b, now, faults) for b in self._interfaces],
            [self._tunnel_sample(t, now, faults) for t in self._topology.tunnels],
        )

    def _interface_sample(self, b: _InterfaceBaseline, now: datetime, faults: FaultEngine) -> dict:
        mods = faults.modifiers_for(b.spec.node_id, b.spec.interface, now)
        minutes = now.timestamp() / 60.0

        # ~40-minute wobble stands in for diurnal variation at demo timescale
        util = b.base_util + 8.0 * math.sin(2 * math.pi * minutes / 40.0 + b.phase)
        util += self._rng.gauss(0, 2.5) + mods.util_add
        util = _clamp(util, 0.0, 100.0)

        congestion = max(0.0, util - 70.0) / 30.0  # 0 at 70% util, 1 at 100%
        latency = b.base_latency * (1 + 2.5 * congestion ** 2) + abs(self._rng.gauss(0, 0.3))
        latency *= mods.latency_mult

        jitter = max(0.05, latency * 0.08 + self._rng.gauss(0, 0.15))
        jitter *= mods.jitter_mult

        loss = 3.0 * max(0.0, (util - 85.0) / 15.0) ** 2  # ~0 until saturation
        loss += mods.loss_add + max(0.0, self._rng.gauss(0, 0.02))
        loss = _clamp(loss, 0.0, 100.0)

        return {
            "ts": now.isoformat(),
            "node_id": b.spec.node_id,
            "interface": b.spec.interface,
            "utilization_pct": round(util, 2),
            "latency_ms": round(latency, 3),
            "jitter_ms": round(jitter, 3),
            "packet_loss_pct": round(loss, 3),
        }

    def _tunnel_sample(self, tunnel: dict, now: datetime, faults: FaultEngine) -> dict:
        # Tunnel health tracks whatever faults affect its endpoint CE routers.
        src_mods = faults.modifiers_for(tunnel["src"], None, now)
        dst_mods = faults.modifiers_for(tunnel["dst"], None, now)
        loss = src_mods.loss_add + dst_mods.loss_add
        lat_mult = src_mods.latency_mult * dst_mods.latency_mult

        minutes = now.timestamp() / 60.0
        throughput = 60.0 + 25.0 * math.sin(2 * math.pi * minutes / 55.0) + self._rng.gauss(0, 4)
        throughput = max(0.0, throughput * (1 - loss / 25.0))
        latency = (18.0 + abs(self._rng.gauss(0, 1.2))) * lat_mult
        encap_errors = int(loss * 3) + (1 if self._rng.random() < 0.05 else 0)

        state = "up"
        if loss > 4.0:
            state = "down"
        elif loss > 1.0 or lat_mult > 1.4:
            state = "degraded"

        return {
            "ts": now.isoformat(),
            "tunnel_id": tunnel["id"],
            "src_node": tunnel["src"],
            "dst_node": tunnel["dst"],
            "state": state,
            "throughput_mbps": round(throughput, 2),
            "latency_ms": round(latency, 3),
            "encap_errors": encap_errors,
        }


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))
