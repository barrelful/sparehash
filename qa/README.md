# SpareHash QA guide

This directory contains integration test suites for SpareHash. These tests
exercise real systemd, real cgroups, real hardware sensors, and real process
lifecycle. They are destructive and must never run on a production host.

## VM isolation requirement

Every suite in `qa/` assumes a disposable systemd VM. The VM must be:

- A fresh Debian or Ubuntu install with systemd as PID 1
- No existing SpareHash, XMRig, or mining software
- No production data, credentials, or network services
- Snapshottable so you can revert after each run

Never run these suites on your personal machine, a server with real data, or a
VM that shares storage with production workloads.

## Suites

| Suite | What it tests | Destructive? |
|---|---|---|
| `qa_power_shim.sh` | Battery unplug -> miner stop within 5s; UPower hide -> sysfs fallback | Yes: stops/starts miner |
| `qa_thermal.sh` | Fixture temperatures 84/86/80/74 -> no-trip/trip/hold/clear | No: fixture only |
| `qa_gaming.sh` | Double-start idempotency, missed-end recovery | No: fixture only |
| `qa_contention.sh` | Miner under CPU stress -> yield proof, recovery within 30s | Yes: runs stress-ng |
| `qa_security.sh` | Loopback-only API, permission matrix, tampered-binary rejection | Yes: creates users, changes ownership |
| `qa_install.sh` | One-command install, post-install assertions | Yes: installs to /opt, /etc, /var |
| `run_all.sh` | Runs every suite, writes evidence logs, exits non-zero on failure | Yes: all of the above |

## Running the suites

From the repository root inside the disposable VM:

```
sudo qa/run_all.sh
```

Each suite writes its evidence to `qa/evidence/qa-<name>.log`. The runner
exits 0 only when every suite passes. Total runtime target: under 15 minutes.

After the run, capture the state:

```
sparehash status
systemctl status sparehash.service sparehash-xmrig.service
```

## What the suites do not test

- Real pool mining (forbidden on any host)
- Real GameMode with a running Steam client
- Real battery hotplug on physical hardware
- Multi-NUMA hugepage reservation
- 1GiB hugepage support
- GPU mining (not supported)

## Checker

`qa/check_readme.py` verifies that every CLI command and config key in the
source code appears in the README. Run it after any documentation change:

```
python3 qa/check_readme.py
```

The checker exits 0 when the README is complete. It exits non-zero and names
every missing entry when something is absent.

## Shims and fixtures

The `shims/` directory contains Python scripts that emulate `gdbus`, `busctl`,
and `gamemoded` outputs. The `fixtures/` directory contains sysfs trees, hwmon
trees, answers files, and release API JSON. These are used by the suites to
avoid depending on real hardware or network access.

## Cleanup

Each suite cleans up after itself. The runner ends with a `sparehash status`
capture. If a suite crashes mid-run, the VM may be left in a partial state.
Revert to your snapshot before the next run.
