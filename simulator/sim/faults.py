"""Fault injection engine.

Each active fault ramps gradually over `ramp_seconds` (progress 0 → 1), then
holds at full effect for `hold_seconds` before auto-expiring. The gradual ramp
is the whole point: it produces the *precursor* patterns the Phase-2 ML models
must learn to detect before user-visible impact.
"""
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sim.topology import Topology

logger = logging.getLogger(__name__)

SCENARIOS: dict[str, dict[str, Any]] = {
    "congestion_ramp": {
        "description": "Utilization ramps up on the target node's interfaces; "
                       "latency and loss follow once the link saturates.",
        "defaults": {"util_increase_pct": 45.0, "ramp_seconds": 600, "hold_seconds": 300},
    },
    "bgp_flap_precursor": {
        "description": "Accelerating keepalive-delay / input-error syslogs and jitter "
                       "creep on the target node, ending in a BGP session flap burst.",
        "defaults": {"ramp_seconds": 480, "hold_seconds": 180},
    },
    "link_degradation": {
        "description": "Packet loss and jitter ramp on the target interface (failing "
                       "optic / dirty fiber), with CRC-error syslogs.",
        "defaults": {"max_loss_pct": 8.0, "ramp_seconds": 600, "hold_seconds": 300},
    },
}


@dataclass
class Modifiers:
    """Per-tick adjustments a fault applies on top of baseline metrics."""
    util_add: float = 0.0
    latency_mult: float = 1.0
    jitter_mult: float = 1.0
    loss_add: float = 0.0


@dataclass
class ActiveFault:
    fault_id: str
    scenario: str
    node_id: str
    interface: str | None
    started_at: datetime
    params: dict[str, Any]
    # per-fault bookkeeping for event emission (last emit time, milestones fired)
    state: dict[str, Any] = field(default_factory=dict)

    def progress(self, now: datetime) -> float:
        elapsed = (now - self.started_at).total_seconds()
        return max(0.0, min(1.0, elapsed / self.params["ramp_seconds"]))

    def expired(self, now: datetime) -> bool:
        elapsed = (now - self.started_at).total_seconds()
        return elapsed > self.params["ramp_seconds"] + self.params["hold_seconds"]

    def describe(self) -> dict[str, Any]:
        return {
            "fault_id": self.fault_id,
            "scenario": self.scenario,
            "node_id": self.node_id,
            "interface": self.interface,
            "started_at": self.started_at.isoformat(),
            "params": self.params,
        }


class FaultEngine:
    def __init__(self, topology: Topology) -> None:
        self._topology = topology
        self._faults: dict[str, ActiveFault] = {}

    # -- management -----------------------------------------------------

    def inject(
        self,
        scenario: str,
        node_id: str,
        now: datetime,
        interface: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> ActiveFault:
        if scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario '{scenario}' (available: {sorted(SCENARIOS)})")
        if node_id not in self._topology.node_ids:
            raise ValueError(f"unknown node '{node_id}'")
        merged = {**SCENARIOS[scenario]["defaults"], **(params or {})}
        fault = ActiveFault(
            fault_id=uuid.uuid4().hex[:8],
            scenario=scenario,
            node_id=node_id,
            interface=interface,
            started_at=now,
            params=merged,
        )
        self._faults[fault.fault_id] = fault
        logger.info("Injected fault %s: %s on %s", fault.fault_id, scenario, node_id)
        return fault

    def clear(self, fault_id: str) -> ActiveFault | None:
        return self._faults.pop(fault_id, None)

    def clear_all(self) -> int:
        n = len(self._faults)
        self._faults.clear()
        return n

    def active(self) -> list[ActiveFault]:
        return list(self._faults.values())

    # -- effect on metrics ----------------------------------------------

    def modifiers_for(self, node_id: str, interface: str | None, now: datetime) -> Modifiers:
        mods = Modifiers()
        for fault in self._faults.values():
            if fault.node_id != node_id:
                continue
            if fault.interface and interface and fault.interface != interface:
                continue
            p = fault.progress(now)
            if fault.scenario == "congestion_ramp":
                mods.util_add += p * fault.params["util_increase_pct"]
            elif fault.scenario == "link_degradation":
                mods.loss_add += p * fault.params["max_loss_pct"]
                mods.jitter_mult *= 1 + 2.0 * p
                mods.latency_mult *= 1 + 0.5 * p
            elif fault.scenario == "bgp_flap_precursor":
                mods.jitter_mult *= 1 + 1.5 * p
                mods.loss_add += 0.5 * p
        return mods

    # -- events (precursors, milestones, flaps, expiry) -------------------

    def tick_events(self, now: datetime) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for fault in list(self._faults.values()):
            if fault.expired(now):
                del self._faults[fault.fault_id]
                events.append(self._event(
                    now, fault, "info", "system",
                    f"Fault scenario '{fault.scenario}' ({fault.fault_id}) expired on {fault.node_id}",
                ))
                continue
            emit = getattr(self, f"_tick_{fault.scenario}")
            events.extend(emit(fault, now))
        return events

    def _tick_congestion_ramp(self, fault: ActiveFault, now: datetime) -> list[dict[str, Any]]:
        events = []
        p = fault.progress(now)
        target = fault.interface or "uplinks"
        if p >= 0.5 and not fault.state.get("warned"):
            fault.state["warned"] = True
            events.append(self._event(
                now, fault, "warning", "syslog",
                f"%TRAFFIC-4-HIGHUTIL: Sustained high utilization on {target} of {fault.node_id}",
            ))
        if p >= 0.9 and not fault.state.get("critical"):
            fault.state["critical"] = True
            events.append(self._event(
                now, fault, "error", "syslog",
                f"%QOS-3-OUTPUTDROPS: Output queue drops increasing on {target} of {fault.node_id}",
            ))
        return events

    def _tick_link_degradation(self, fault: ActiveFault, now: datetime) -> list[dict[str, Any]]:
        p = fault.progress(now)
        # CRC-error syslogs, accelerating as the optic degrades
        interval = max(10.0, 45.0 * (1 - p) + 10.0)
        if not self._due(fault, "crc", now, interval):
            return []
        errors = int(50 + 2000 * p ** 2)
        target = fault.interface or "interface"
        return [self._event(
            now, fault, "warning", "syslog",
            f"%LINK-3-ERRORS: CRC/input errors increasing on {target} of {fault.node_id} "
            f"({errors} errors in last interval)",
            extra={"crc_errors": errors},
        )]

    def _tick_bgp_flap_precursor(self, fault: ActiveFault, now: datetime) -> list[dict[str, Any]]:
        session = self._topology.bgp_peer_of(fault.node_id)
        if session is None:
            return []
        session_id, peer = session
        p = fault.progress(now)
        events = []
        if p < 1.0:
            # precursor phase: warnings arrive faster and faster
            interval = max(5.0, 60.0 * (1 - p))
            if self._due(fault, "precursor", now, interval):
                idx = fault.state.get("precursor_count", 0)
                fault.state["precursor_count"] = idx + 1
                pool = [
                    f"%BGP-5-KEEPALIVE: Keepalive processing delayed for neighbor {peer}",
                    f"%BGP-4-HOLDTIMER: Hold timer approaching expiry for neighbor {peer}",
                    f"%LINEPROTO-4-INPUTERR: Input errors rising on session path to {peer}",
                ]
                events.append(self._event(
                    now, fault, "warning", "bgp", pool[idx % len(pool)],
                    extra={"session_id": session_id, "peer": peer},
                ))
        else:
            # hold phase: the actual flap burst
            if self._due(fault, "flap", now, 25.0):
                going_down = not fault.state.get("session_down", False)
                fault.state["session_down"] = going_down
                if going_down:
                    events.append(self._event(
                        now, fault, "error", "bgp",
                        f"%BGP-5-ADJCHANGE: neighbor {peer} Down - hold timer expired",
                        extra={"session_id": session_id, "peer": peer, "state": "down"},
                    ))
                else:
                    events.append(self._event(
                        now, fault, "warning", "bgp",
                        f"%BGP-5-ADJCHANGE: neighbor {peer} Up",
                        extra={"session_id": session_id, "peer": peer, "state": "up"},
                    ))
        return events

    def _due(self, fault: ActiveFault, key: str, now: datetime, interval_s: float) -> bool:
        last: datetime | None = fault.state.get(f"last_{key}")
        if last is not None and (now - last).total_seconds() < interval_s:
            return False
        fault.state[f"last_{key}"] = now
        return True

    def _event(
        self,
        now: datetime,
        fault: ActiveFault,
        severity: str,
        event_type: str,
        message: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "ts": now.isoformat(),
            "node_id": fault.node_id,
            "severity": severity,
            "event_type": event_type,
            "message": message,
            "details": {"fault_id": fault.fault_id, "scenario": fault.scenario, **(extra or {})},
        }
