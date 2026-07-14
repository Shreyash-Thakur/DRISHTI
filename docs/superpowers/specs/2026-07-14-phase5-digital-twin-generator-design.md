# Phase 5 — Digital Twin (Containerlab) — design

Status: proposed (authored autonomously under a "continue implementation, do not
stop" directive; see process/scope note at end)
Date: 2026-07-14

## Purpose

DRISHTI's roadmap Phase 5 is a **digital twin**: stand the 6-node topology up as
real routers so a proposed remediation can be validated *before* it touches
production. The industry-standard offline way to do this is
[Containerlab](https://containerlab.dev) driving FRR (free, open-source routing)
containers.

## Scope (important — read first)

Containerlab is a Go CLI that orchestrates **container images of network operating
systems** (FRR, etc.). Standing up and running a live twin therefore needs the
`containerlab` binary plus those images pulled locally. In the current air-gapped
dev environment **neither `containerlab` nor the images are present**, so the live
deploy/validate loop cannot be exercised or verified here.

This phase therefore delivers the **offline-buildable, fully-testable core**: a
generator that turns the single source-of-truth `data/topology.json` into

1. a Containerlab lab file (`drishti.clab.yml`), and
2. per-node FRR startup configs (loopbacks, interface IPs, OSPF core, BGP sessions)

so that on any machine that *does* have the Containerlab toolchain, `containerlab
deploy -t drishti.clab.yml` brings up a faithful twin of the DRISHTI network in one
command. The live deploy + on-twin fix validation are documented as a manual,
toolchain-dependent step — **not** implemented as unverifiable code here. This
keeps the phase honest: everything committed is deterministic and unit-tested;
nothing pretends to validate a twin that can't run in this environment.

## Non-goals

- **No live `containerlab deploy` / teardown orchestration in code.** The generator
  emits artifacts; running them is a documented CLI step. Wrapping a binary that
  isn't installed would be untestable scaffolding.
- **No automated fix-validation loop (this phase).** Defining "apply fix X to the
  twin, assert metric Y recovers" requires the running twin; deferred with a clear
  seam (the generator output is what such a loop would consume).
- **No new heavy dependencies.** YAML via `pyyaml` (already installed); everything
  else is pure Python.
- **Not an HTTP service.** Unlike Phases 2–4, the twin is infrastructure, not a
  request/response service — so it takes no `:85xx` port. It is a CLI + library.

## Architecture

New top-level package `twin/`:

```
twin/
  config.py     Settings (TWIN_ prefix): topology_path, out_dir, node_image,
                 mgmt_subnet, asn
  clab.py       build_clab(topology, node_image, mgmt_subnet) -> dict ;
                 to_yaml(clab: dict) -> str  (pure, deterministic)
  frr.py        frr_config(node_id, topology, asn) -> str  (per-node FRR startup
                 config: loopback, interface IPs, OSPF on core, iBGP/eBGP)
  addressing.py deterministic IP plan from topology (per-link /30s, loopbacks from
                 topology.json's loopback field) — shared by clab.py + frr.py
  generate.py   CLI: writes drishti.clab.yml + configs/<node>/frr.conf to out_dir
  tests/
  README.md
```

`twin/` reads the **same** `data/topology.json` every other component uses, so the
twin can never drift from the simulated/observed network.

## Addressing (`twin/addressing.py`)

Deterministic, so regeneration is stable and configs match the lab file:

- **Loopbacks:** taken directly from each node's `loopback` field in
  `topology.json` (e.g. `pe-east` → `10.255.0.1/32`).
- **Link subnets:** each link gets a `/30` from `10.0.0.0/16`, assigned in
  `topology.json` link order (`10.0.0.0/30` for the first link, `.4/30` for the
  second, …). Endpoint `a` gets `.1`, endpoint `b` gets `.2`. A pure function
  `link_addressing(topology) -> dict[link_id, {a_ip, b_ip, subnet}]`.

## Containerlab file (`twin/clab.py`)

`build_clab` returns the Containerlab topology as a dict (so it's trivially
assertable in tests before YAML serialization):

```yaml
name: drishti
topology:
  nodes:
    ce-site-a: {kind: linux, image: <node_image>, binds: [...frr.conf...]}
    ...            # one per topology node
  links:
    - endpoints: ["pe-east:eth1", "p-core-1:eth1"]   # one per topology link
    ...
```

- Node interface names are assigned deterministically per node
  (`eth1`, `eth2`, … in the order links reference that node) and the **same**
  mapping is reused by `frr.py` so interface IPs line up with clab endpoints.
- `image` and management subnet come from Settings.
- `to_yaml` serializes with `pyyaml` (sorted keys off, block style) and is asserted
  to round-trip.

## FRR config (`twin/frr.py`)

`frr_config(node_id, topology, asn)` emits a plain FRR `frr.conf`:

- `interface lo` with the node's loopback `/32`.
- one `interface ethN` block per link on the node, with the `/30` address from the
  addressing plan.
- `router ospf` advertising the core links + loopback (P and PE nodes) — this is
  the IGP the twin uses to actually route.
- `router bgp <asn>` with neighbors mirroring `topology.json`'s `bgp_sessions`
  (eBGP CE↔PE using per-link addresses, iBGP PE↔PE using loopbacks).

Pure string generation — fully unit-testable (assert the loopback line, the right
number of interface blocks, and each expected BGP neighbor appears).

## CLI (`twin/generate.py`)

`python -m twin.generate` (respects `TWIN_*` env / Settings):
1. load topology, compute addressing,
2. write `<out_dir>/drishti.clab.yml`,
3. write `<out_dir>/configs/<node>/frr.conf` for every node,
4. print the exact `containerlab deploy -t <out_dir>/drishti.clab.yml` command to
   run on a toolchain-equipped host.

Default `out_dir` is `twin/lab/` (git-ignored — generated artifact).

## Error handling

- Missing/malformed `topology.json` → a clear error, not a stack trace.
- A node with no `loopback` field → explicit error naming the node (addressing
  can't proceed without it).
- `out_dir` created if absent.

## Testing

- **`addressing.py`** — loopbacks pulled from topology; each link gets a distinct
  `/30`; `a`/`b` get `.1`/`.2`; deterministic across runs.
- **`clab.py`** — `build_clab` produces one node per topology node and one link per
  topology link; every node has an image + a bind to its `frr.conf`; `to_yaml`
  round-trips via `yaml.safe_load`.
- **`frr.py`** — a node's config contains its loopback `/32`, the right count of
  `interface ethN` blocks, and a `neighbor` line for each BGP session it
  participates in.
- **`generate.py`** — writing to a `tmp_path` produces `drishti.clab.yml` (valid
  YAML) + a `frr.conf` per node.
- **Manual (toolchain-dependent, documented, NOT run here):** on a host with
  `containerlab` + an FRR image, `containerlab deploy -t twin/lab/drishti.clab.yml`
  brings the twin up; `docker exec <node> vtysh -c "show ip route"` shows routes
  learned over the generated configs.

## Repo/docs updates

- `twin/README.md`: what the twin is, the scope caveat (toolchain needed to run),
  the generate command, the deploy command, and how to run the tests.
- Root `README.md`: roadmap item 5 marked as ✅ (generator) with a parenthetical
  that live deploy needs the containerlab toolchain; repo-layout + teammates bullet.
- `.gitignore`: ignore `twin/lab/` (generated).
- `docker-compose.yml`: **not** modified — the twin is not a compose service.

## Process / scope note

Authored autonomously under a standing "continue implementation, do not stop"
instruction. The deliberate scope limit — generator only, no live-deploy wrapper —
is driven by the hard offline constraint (no `containerlab`, no NOS images in this
environment) and the project's rule that committed work must be verifiable. The
generator is the genuine, reusable artifact the twin phase is built on; the
live-deploy step is documented for a toolchain-equipped host and left as a manual
command rather than unverifiable code.
