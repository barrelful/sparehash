# SpareHash QA guide

This directory contains integration test suites for SpareHash. They exercise
real systemd, cgroups, controller IPC, and process lifecycle with controlled
power, thermal sensor, and GameMode protocol fixtures.
**Many suites are destructive and must never run on a production host.**

## Current state

The `qa/` directory contains:

- `README.md`: this file
- `check_readme.py`: inventory checker that verifies every CLI command and
  config key appears in the README
- `run_all.sh`: required-suite runner, with install/idempotency checks first and
  uninstall last
- `spin.py`: CPU-spin helper for contention tests
- `qa_*.sh`: destructive or non-destructive integration suites
- `shims/`: Python scripts that emulate `gdbus`, `busctl`, and `gamemoded`
- `fixtures/`: sysfs trees, hwmon trees, answers files, and release API JSON
- `lib/runtime.sh`: VM preflight and service-scoped fixture ownership
- `local_pool.py`: bounded loopback-only synthetic RandomX job fixture; rejects
  shares and never forwards them to a real pool

This guide describes acceptance contracts, not a completed VM validation or a
release approval. Check the current harness before running while repairs are in
progress, especially provider/config overlays and local-pool startup.

## Suite inventory

| Suite | What it tests | Destructive? |
|---|---|---|
| `qa_release_smoke.sh` | Frozen candidate: install, hashing, pause, battery, thermal, gaming, controller loss, offline uninstall | Yes: installs/removes, kills controller |
| `qa_power_shim.sh` | Battery unplug -> miner stop within 5s; UPower hide -> sysfs fallback | Yes: stops/starts miner |
| `qa_thermal.sh` | Fixture temperatures 84/86/80/74 -> no-trip/trip/hold/clear | Yes: changes fixtures, stops/starts miner |
| `qa_gaming.sh` | Acknowledged double-start idempotency and provider-driven missed-end recovery | Yes: real IPC, stops/starts miner |
| `qa_contention.sh` | Miner under CPU stress -> yield proof, recovery within 30s | Yes: runs stress-ng |
| `qa_security.sh` | Loopback-only API, permission matrix, tampered-binary rejection | Yes: creates users, changes ownership |
| `qa_install.sh` | One-command install, post-install assertions | Yes: installs to /opt, /etc, /var |
| `qa_idempotent_install.sh` | Re-running installer preserves token/config | Yes: installs to /opt, /etc, /var |
| `qa_uninstall.sh` | Uninstall removes only owned artifacts | Yes: installs/removes identities and units |
| `qa_controller_lifecycle.sh` | Controller SIGKILL -> miner cgroup empty; restart re-evaluates | Yes: kills controller |
| `qa_status_output.sh` | Status JSON contract and field presence | No: read-only |

The runner checks unattended reinstall and configuration/token preservation
directly; it doesn't invoke the standalone `qa_install.sh` or
`qa_idempotent_install.sh` scripts. Gaming provider coverage must be exercised,
not treated as a pass when no provider/session was observed.

## Running the suites

### Required minimal-release acceptance

`qa_release_smoke.sh` is the required eight-checkpoint acceptance procedure;
`run_all.sh` remains the broader harness and is not a minimal-release requirement.
Use a fresh disposable VM with the candidate already installed and stopped.
The smoke reinstalls using a generated repo-external dummy-wallet answers file,
then enables service-only fixtures before any mining. It ends uninstalled with
default config and retirement retained; never manually clear the lifecycle latch.

From the clean candidate checkout inside the VM:

```bash
sudo env SPAREHASH_QA_DISPOSABLE_VM=YES \
  CANDIDATE_SHA=<full-frozen-40-character-SHA> \
  EVIDENCE_DIR=/var/tmp/sparehash-v010-evidence bash qa/qa_release_smoke.sh
```

`CONTROLLER_BIN` defaults to `/usr/local/bin/sparehash`. The evidence directory
must not exist; the default is `/var/tmp/sparehash-v010-evidence`. For an extracted
candidate sdist without Git metadata, independently verify its artifact/payload
against the frozen commit, and copy the transferred `candidate.sha` into its
root before running. A checkout checks HEAD against `CANDIDATE_SHA`; installed
Python and template bytes must match the source payload in either case.

The script requires root, systemd PID 1, `nsenter`, `ss`, and the installed
candidate. It records PASS/FAIL per checkpoint, same-PID hash counts, journals,
and `vm-summary.json`. Any failed checkpoint aborts; unvisited checkpoints are
NOT_RUN. Cleanup stops services before removing service-only fixtures/pool and
preserves evidence. A foreign sentinel remains for inspection. No real-pool
mining or donation traffic is permitted: jobs are synthetic on loopback only.
Successful offline checks are not a claim that this VM acceptance has run.

### Broader harness

These suites require a disposable systemd VM and must never run on a production
host. The VM must be:

- A disposable Debian or Ubuntu VM with systemd as PID 1
- The candidate SpareHash and verified XMRig already installed, with no unrelated
  mining software
- No production data, credentials, or network services
- Snapshottable so you can revert after each run
- Root access, `nsenter`, and `stress-ng` available

Never run these suites on your personal machine, a server with real data, or a
VM that shares storage with production workloads.

Prepare an unattended TOML answers file at an absolute path outside the
repository, for example `/var/tmp/sparehash-qa-answers.toml`. It must use
`pool.host = "127.0.0.1"`, `pool.port = 3333`, and `pool.tls = false`, not a
real pool. Install the candidate with that file before invoking the runner;
leave the controller stopped until the controlled harness is ready.

From the repository root inside that VM, explicitly pass the safety opt-in and
the existing answers file through sudo:

```
sudo env SPAREHASH_QA_DISPOSABLE_VM=YES \
  QA_ANSWERS_FILE=/var/tmp/sparehash-qa-answers.toml bash qa/run_all.sh
```

`EVIDENCE_DIR` selects the evidence directory; the current `run_all.sh` default
is `/omo/evidence`. Suite logs use `qa-<script-name-without-.sh>.log`, with a
separate `qa-install-idempotency.log`. Check the runner for the current default
before collecting evidence. It exits non-zero on required-suite failure or
reported skipped coverage.

The runner captures `final-status.json` while the miner is ready, before
destructive uninstall. A successful run ends uninstalled, not mining; don't
expect a working `sparehash status` command afterward.

## Controlled fixture contract

Fixtures belong under `/run/sparehash-qa`, with temporary service drop-ins under
`/run/systemd/system`. Bind mounts apply only inside the relevant services'
mount namespaces. Keep `PrivateTmp=yes`; never mount fixtures over host-global
sysfs, tools, or configuration.

Controlled provider configuration and zero-donation miner JSON belong in these
service-only overlays. Don't hand-edit installed configuration or install
receipts to make QA pass. Before starting the controller, the harness must start
the bounded local pool fixture on `127.0.0.1:3333` and ensure every effective
miner JSON uses only that non-TLS pool with `donate-level=0`. Its synthetic
RandomX jobs have no chain value; submitted shares are rejected. Real-pool
mining and donation traffic are forbidden during destructive QA.

Contention acceptance requires:

- Miner CPU usage <=10% of normalized total CPU during 60 seconds of CPU stress.
- Foreground throughput degradation <=25% against a matched stress-only baseline.
- Actual hashing recovery within 30 seconds after stress ends, with hash-count
  progress and >=20% normalized CPU usage, not just an active service.

## What the suites do not test

- Real pool mining (forbidden during destructive QA)
- Real GameMode with a running Steam client
- Real battery hotplug on physical hardware
- Physical thermal sensor accuracy; sensor inputs are controlled fixtures
- Multi-NUMA hugepage reservation
- 1GiB hugepage support
- GPU mining (not supported)

## Checker

`qa/check_readme.py` verifies that every CLI command and config key in the
source code appears in the README. Run it after any documentation change:

```
uv run python qa/check_readme.py
```

The checker exits 0 when the README is complete. It exits non-zero and names
every missing entry when something is absent.

Public CI runs unit tests, the README checker, mypy, and wheel/sdist builds with
a no-runtime-dependencies check. It doesn't run destructive VM suites.

## Cleanup

The runtime owner removes its service drop-ins and fixtures after stopping the
stack. If stopping fails, fixtures are retained for diagnosis. A crash or failed
uninstall may leave a partial state with a durable lifecycle hold. Save evidence
outside the snapshot, then revert the VM before the next run.
