# twin/ — Digital Twin Generator (Phase 5)

Generates a deployable **[Containerlab](https://containerlab.dev) digital twin** of
the DRISHTI network from the single source-of-truth `data/topology.json`:

- `drishti.clab.yml` — the Containerlab lab (one node per topology node, one link
  per topology link, each node running an FRR container), and
- per-node FRR startup configs (`configs/<node>/frr.conf`) — loopbacks, interface
  `/30`s, OSPF on the core, BGP sessions mirroring the topology.

On any host with the Containerlab toolchain, this stands the network up as **real
routers** so a proposed remediation can be validated before it touches production.

See the full design: [`docs/superpowers/specs/2026-07-14-phase5-digital-twin-generator-design.md`](../docs/superpowers/specs/2026-07-14-phase5-digital-twin-generator-design.md).

## Scope (read this first)

This package **generates** the lab artifacts — that part is pure-Python,
deterministic, offline, and unit-tested. **Running** the twin needs the
`containerlab` binary plus an FRR container image, which are *not* required to
generate and are *not* bundled here (they weren't available in the air-gapped dev
environment where this was built). So `containerlab deploy` is a documented manual
step for a toolchain-equipped host, not code in this repo. Nothing here pretends to
validate a twin that can't run in your environment.

## Generate

Requires Python 3.11+. From the **repo root**:

```bash
pip install -r twin/requirements.txt
python -m twin.generate            # writes twin/lab/drishti.clab.yml + configs/
```

`twin/lab/` is git-ignored (regenerable). Override defaults with `TWIN_*` env vars
(`TWIN_NODE_IMAGE`, `TWIN_OUT_DIR`, `TWIN_ASN`, `TWIN_MGMT_SUBNET`).

## Deploy (on a containerlab host)

```bash
containerlab deploy -t twin/lab/drishti.clab.yml
# inspect a node's routing once it converges:
docker exec clab-drishti-p-core-1 vtysh -c "show ip route"
```

## Addressing

- **Loopbacks** come straight from each node's `loopback` field in
  `data/topology.json` (e.g. `pe-east` → `10.255.0.1/32`).
- **Link `/30`s** are assigned deterministically from `10.0.0.0/16` in topology
  link order; endpoint `a` gets `.1`, `b` gets `.2`.
- The same node→interface (`eth1`, `eth2`, …) mapping is shared by the lab file and
  the FRR configs, so clab endpoints and FRR interface IPs always line up.

BGP uses a single ASN skeleton (all `remote-as <TWIN_ASN>`); refine eBGP AS numbers
on the toolchain host if you need true eBGP behavior.

## Running the tests

```bash
cd twin && pip install -r requirements.txt && pytest      # run from repo root
```
