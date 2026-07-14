# Reading a Cascade (root cause vs. blast radius)

## Root cause vs. symptoms

DRISHTI's RCA engine groups correlated symptoms into one incident and names the
most likely root-cause graph element. Trust the earliest, most central symptom as
the origin: causes precede effects, and a core (P) node failure explains far more
of the network than a leaf (CE) symptom. The listed symptoms are what the network
reported; the root cause is where to act.

## Blast radius and tunnels

The cascade is the ordered set of elements at risk, nearest first. Pay special
attention to CE-to-CE IPsec tunnels: they ride the PE and P-core path, so a fault
on any node along that path threatens both tunnels even though the tunnel endpoints
look healthy. BGP sessions on or adjacent to the root cause are next to feel it.

## Prioritizing action

Act at the root cause first — fixing it collapses the whole cascade. Order your
attention by hops from the root: elements one hop away will degrade before those
two hops out. Use the predicted time-to-impact (when the predictive engine is
available) to decide whether you have minutes or seconds.

## When confidence is low

If root-cause confidence is near 0.5, two elements are competing to explain the
incident. Treat both as suspects, and look for the earliest symptom to break the
tie.
