# Disposable-VM release QA

**Destructive: never run on a workstation, production host or a VM with shared
production storage.** Read `docs/development/vm-testing.md` before integration.
Run one fresh disposable at a time, with no guest NIC or host filesystem sharing.
Keep SELinux Enforcing. No lab controller is a dependency of the shipped code.

## Required experimental v0.2 entrypoint

`qa/qa_release_smoke.sh` invokes `qa/release_smoke.py`. It is the single required
release smoke, adapted from S3's passing real-distro driver. It requires both the
VM opt-in and explicit `--yes`, plus a source SHA-256 checked before any effects.
It refuses an existing product/artifact/evidence tree rather than clobber it.
The wrapper fixes helper PATH; after consent and fresh-VM checks the driver clears
inherited import, proxy and product QA overrides before privileged fixture actions.

First verify the selected source/archive by your normal trusted process, then
calculate its inventory digest (read-only, no root or VM opt-in needed):

```sh
python3 qa/release_smoke.py --print-source-sha256
```

Transfer that expected digest independently alongside the exact payload. Inside
the fresh VM, run:

```sh
sudo env SPAREHASH_QA_DISPOSABLE_VM=YES bash qa/qa_release_smoke.sh \
  --yes --source-sha256 <verified-64-character-digest> \
  --evidence /var/tmp/sparehash-v020-evidence
```

The digest binds Python source, unit templates, installer, licence/notices,
packaging metadata, driver and synthetic fixture bytes. It is not an unchecked
`candidate.sha` label. S5 will additionally bind the frozen candidate commit and
artifacts; running this S4 smoke does not declare an RC or publish anything.

## Exercised path and evidence

The script executes the actual source installer with `--answers ... --yes
--use-existing`, verifies installed bytes, and uses real runtime/IPC/systemd and
receipt removal. It checks default best-effort scanning, unprivileged idle
scheduling, file-backed power/thermal callbacks, manual/lifecycle independence,
recognised synthetic reaper stop under foreground CPU load, scan-bound failure,
controller loss, strict foreign-content refusal, non-purge state/config retention,
pause-preserving reinstall and purge. The five-second recognised-game objective
is an assertion, not a published hard real-time guarantee.

The fixture `qa/fixtures/synthetic_xmrig.py` is SpareHash-owned, GPL-3.0-or-later,
**not upstream XMRig**. It never hashes, connects to a pool or forwards traffic;
its only listener is an authenticated loopback API reporting zero hashrate. It
passes through the real nobody/PrivateNetwork version/config probes and protected
unprivileged miner exec. This is not signed upstream-artifact or mining-performance
certification. The exercised Steam detector is Lutris-derived; attribution and
pinned revision are in `THIRD_PARTY_NOTICES.md`.

Sensor/config overlays are scoped to the controller mount namespace, using a
temporary `/run/systemd/system` drop-in and files in the evidence directory.
`PrivateTmp` and other shipped isolation remain enabled. Three-second test dwell
compresses scenarios; production defaults are unchanged. These are synthetic
values/argv, not physical hotplug, actual native/Proton games or KDE/Gaming Mode.

Evidence includes source identity, per-command logs, checkpoint timings,
controller/miner/kernel journals, observed AVC lines and `summary.json` with
`complete=true` **only if tests and cleanup pass**. Unvisited checks are absent,
not assumed passing. Failure is nonzero and preserves diagnostics. AVCs fail
acceptance; record them rather than weakening policy.

Success ends uninstalled/purged and retired with no running miner. Known foreign
sentinels and the external synthetic artifact remain as preservation evidence;
discard the VM afterward. Cleanup stops the owned services and proves empty
before removing only its drop-in; a failed stop retains fixtures. Do not manually
delete product receipts or safety holds to force a pass.

Actual reboot acceptance is separately recorded in the S3 report. This smoke
does not reboot the guest automatically. The maintainer must discard failed and
successful guests and preserve private evidence under `.omo/evidence/v020/`.

## Historical/broader QA

`run_all.sh`, the other `qa_*.sh` suites, GameMode shims, contention/local-pool
fixtures and `probe_gaming.py` remain archived research/broader test tools, not
required v0.2 release gates or installed dependencies. Do not infer current
gameplay coverage from them. The current entrypoint above is the supported
minimal acceptance route; do not run older destructive suites indiscriminately.

## Non-destructive checks

```sh
uv sync --locked
.venv/bin/python -m pytest
.venv/bin/python qa/check_readme.py
.venv/bin/mypy sparehash
uv build
.venv/bin/python qa/check_artifacts.py dist
```

CI covers Python 3.11–3.14, scoped release lint, documentation inventory and clean
builds with no mandatory runtime dependencies and GPL/notice inspection. CI does
not run privileged/destructive VM acceptance or certify hardware/gameplay.
