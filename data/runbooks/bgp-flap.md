# BGP Session Flap (precursor and flap burst)

## Symptoms

Accelerating warnings before the session drops: keepalive processing delayed, hold
timer approaching expiry, and input errors rising on the session path. These
`%BGP-5-KEEPALIVE` / `%BGP-4-HOLDTIMER` precursors speed up until the adjacency
flaps — `%BGP-5-ADJCHANGE: neighbor ... Down - hold timer expired`, then back Up,
repeatedly. Jitter creep on the path often accompanies the precursor phase.

## Likely causes

A congested or degrading link on the session path (so keepalives are delayed or
lost), high control-plane CPU on one peer, an MTU mismatch, or a flapping
underlying interface. A hold-timer expiry almost always means keepalives are not
getting through in time, not that BGP itself is misconfigured.

## Recommended checks

- Look for interface errors or congestion on the physical path between the two BGP
  peers — the precursor is usually an underlay problem surfacing as a BGP symptom.
- Check control-plane CPU and the BGP process on both peers.
- Verify the negotiated keepalive/hold timers and whether they were recently
  changed.
- Confirm MTU consistency along the path.

## Mitigations

Fix the underlying link or CPU problem rather than just raising the hold timer.
If a specific path member is degrading, drain it. Dampen the flapping session only
as a temporary measure while you address the root cause.
