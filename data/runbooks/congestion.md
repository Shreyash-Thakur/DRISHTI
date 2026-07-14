# Congestion / Sustained High Utilization

## Symptoms

Interface utilization climbing and staying high, followed by latency, jitter, and
packet loss rising once the link runs hot. Syslogs such as
`%TRAFFIC-4-HIGHUTIL` (sustained high utilization) and, as the link saturates,
`%QOS-3-OUTPUTDROPS` (output queue drops increasing). The output queue drops are
the point where user traffic starts to hurt.

## Likely causes

A genuine traffic surge, a large flow or backup job, a rerouted path concentrating
traffic onto one link, or loss of a parallel core link forcing all traffic onto
the survivor.

## Recommended checks

- Identify the top talkers / top flows on the hot interface.
- Confirm whether a parallel link recently went down and pushed its traffic here.
- Check the QoS policy: are the right classes being prioritized, and are drops
  hitting scavenger/best-effort or business-critical traffic?
- Compare against capacity: is this a transient spike or a sustained trend that
  needs more bandwidth?

## Mitigations

Reroute or load-balance across an alternate core path, tighten or correct the QoS
policy so drops fall on low-priority classes, throttle or reschedule bulk
transfers, and plan a capacity upgrade if the trend is sustained.
