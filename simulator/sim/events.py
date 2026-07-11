"""Steady-state background events — the routine syslog/OSPF chatter a real
NOC sees, so fault-driven events aren't the only ones in the stream."""
import random
from datetime import datetime

from sim.topology import Topology

_BACKGROUND_POOL: list[tuple[str, str, str]] = [
    # (event_type, severity, message template)
    ("syslog", "info", "%SYS-5-CONFIG_I: Configured from console by admin on vty0"),
    ("syslog", "info", "%NTP-6-PEERSYNC: Synchronized to NTP server 10.255.0.100"),
    ("syslog", "info", "%SEC_LOGIN-5-LOGIN_SUCCESS: Login Success on vty0"),
    ("ospf", "info", "%OSPF-5-ADJCHG: Neighbor adjacency refreshed, state FULL"),
    ("syslog", "info", "%SNMP-5-COLDSTART: SNMP agent poll cycle completed"),
]


class BackgroundEvents:
    def __init__(self, topology: Topology, seed: int) -> None:
        self._nodes = sorted(topology.node_ids)
        self._rng = random.Random(seed + 1)

    def tick(self, now: datetime) -> list[dict]:
        # ~4% chance per tick keeps chatter sparse (one event every ~2 minutes)
        if self._rng.random() > 0.04:
            return []
        event_type, severity, message = self._rng.choice(_BACKGROUND_POOL)
        return [{
            "ts": now.isoformat(),
            "node_id": self._rng.choice(self._nodes),
            "severity": severity,
            "event_type": event_type,
            "message": message,
            "details": {"origin": "background"},
        }]
