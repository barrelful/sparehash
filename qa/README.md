# SpareHash QA guide

This directory contains QA documentation and a README inventory checker for
SpareHash. Integration test suites are planned for Todo27 and will exercise
real systemd, real cgroups, real hardware sensors, and real process lifecycle.
They are destructive and must never run on a production host.

## Current state

The `qa/` directory currently contains:

- `.gitkeep`: placeholder to keep the directory in version control
- `README.md`: this file
- `check_readme.py`: inventory checker that verifies every CLI command and
  config key appears in the README

Integration test suites are specified in the plan (Todo27) but not yet
implemented. Do not attempt to run `qa/run_all.sh` or any `qa_*.sh` script;
they do not exist yet.

## Planned suites (Todo27)

The following suites are specified but not yet available:

| Suite | What it will test | Destructive? |
|---|---|---|
| `qa_power_shim.sh` | Battery unplug -> miner stop within 5s; UPower hide -> sysfs fallback | Yes: stops/starts miner |
| `qa_thermal.sh` | Fixture temperatures 84/86/80/74 -> no-trip/trip/hold/clear | No: fixture only |
| `qa_gaming.sh` | Double-start idempotency, missed-end recovery | No: fixture only |
| `qa_contention.sh` | Miner under CPU stress -> yield proof, recovery within 30s | Yes: runs stress-ng |
| `qa_security.sh` | Loopback-only API, permission matrix, tampered-binary rejection | Yes: creates users, changes ownership |
| `qa_install.sh` | One-command install, post-install assertions | Yes: installs to /opt, /etc, /var |
| `run_all.sh` | Runs every suite, writes evidence logs, exits non-zero on failure | Yes: all of the above |

These suites will require a disposable systemd VM and must never run on a
production host. The VM must be:

- A fresh Debian or Ubuntu install with systemd as PID 1
- No existing SpareHash, XMRig, or mining software
- No production data, credentials, or network services
- Snapshottable so you can revert after each run

Never run these suites on your personal machine, a server with real data, or a
VM that shares storage with production workloads.

## Running the suites (when available)

Once Todo27 is complete, from the repository root inside the disposable VM:

```
sudo qa/run_all.sh
```

Each suite will write its evidence to `qa/evidence/qa-<name>.log`. The runner
will exit 0 only when every suite passes. Total runtime target: under 15 minutes.

After the run, capture the state:

```
sparehash status
systemctl status sparehash.service sparehash-xmrig.service
```

## What the suites will not test

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

## Shims and fixtures (planned)

The planned `shims/` directory will contain Python scripts that emulate `gdbus`,
`busctl`, and `gamemoded` outputs. The planned `fixtures/` directory will
contain sysfs trees, hwmon trees, answers files, and release API JSON. These
will be used by the suites to avoid depending on real hardware or network access.

## Cleanup (when suites are available)

Each suite will clean up after itself. The runner will end with a
`sparehash status` capture. If a suite crashes mid-run, the VM may be left in a
partial state. Revert to your snapshot before the next run.
