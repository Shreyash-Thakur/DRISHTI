# Link Degradation (failing optic / dirty fiber)

## Symptoms

Rising CRC and input errors on a physical interface, climbing packet loss, and
jitter increasing to roughly three times baseline. Syslogs of the form
`%LINK-3-ERRORS: CRC/input errors increasing` that arrive at an accelerating
cadence as the optic degrades. Throughput may still look normal early on — the
error counters move first.

## Likely causes

A failing or dirty optic/SFP, a bent or contaminated fiber, a marginal patch
cable, or a transceiver seated poorly. On core links this is usually a single
degrading transceiver rather than a config problem.

## Recommended checks

- Read the optical transmit/receive power on both ends of the link and compare to
  the transceiver's rated range; a low or drifting Rx level points at the optic or
  fiber.
- Check interface error counters (CRC, input errors, runts) and their rate of
  change, not just the totals.
- Clean or reseat the fiber connectors; if errors persist, swap the SFP/optic on
  the suspect end, then the fiber, one change at a time.
- If the link is a core member of an ECMP bundle, consider draining it (raise its
  metric) so traffic reroutes while you replace hardware.

## Mitigations

Drain and reroute around the degrading link before it fails hard. Replace the
optic or fiber during a maintenance window. Do not clear counters until after you
have captured the error rate for the incident record.
