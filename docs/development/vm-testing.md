# SpareHash VM testing environment

This document describes the disposable-virtual-machine environment used to run
destructive SpareHash installation, upgrade, uninstallation and lifecycle tests.
It is the standard integration-testing environment for SpareHash development.

Production behavior on the user's actual workstation is never validated by
running these tests against the workstation directly. All destructive tests
must be run inside a disposable VM from the lab.

## 1. Architecture and scope

The test environment is a separate, dedicated QEMU/KVM-based VM lab maintained
on a host workstation by the project maintainer. It is independent of the
user's real operating system. Two baseline operating systems are available:

| Baseline | Resources | Administration | Purpose |
|---|---|---|---|
| `ubuntu-noble-20261103-v3` | 4 vCPUs, 6 GiB RAM, 40 GiB disk | Authenticated serial console | Current v0.3 Ubuntu acceptance |
| `ubuntu-noble-20260926-v1` | 4 vCPUs, 6 GiB RAM, 40 GiB disk | QEMU guest agent | Historical Ubuntu baseline |
| `bazzite-kde-44-20261012-v1` | 4 vCPUs, 8 GiB RAM, 80 GiB disk | Authenticated virtual serial console | Atomic / SELinux-enforcing testing |

Both baselines have been validated for boot, command execution, file transfer,
controlled shutdown, forced recovery and clean disposal. SELinux remains
enforcing in the Bazzite baseline and atomic-filesystem redirects
(`/opt -> var/opt`, `/usr/local -> ../var/usrlocal`) are present.

### Owner-authorized v0.3 Ubuntu administration repair (2026-11-03)

The owner separately authorized repair/reprovisioning within the designated lab.
The original two baselines are preserved. A fresh Ubuntu overlay was provisioned
with normal password-authenticated serial administration and sealed separately
as `ubuntu-noble-20261103-serial-v2` (same CPU/RAM/disk limits, no NIC, shares or
passthrough). Its console readiness does not depend on QGA. Disposable console
credentials remain only in the private lab credential store, never source/evidence.
The private launcher supports this recorded serial transport; no lab controller
or runtime dependency is added to SpareHash. See `v030-progress.md` for acceptance.
This scoped authorization does not permit unrelated VM/data changes or weaker
networking, storage, wallet/mining or SELinux/AppArmor isolation.

**Closure:** serial-v2 administration works, but the v2 baseline was not
validated for acceptance; its Ubuntu systemd/PID1 crash reproduced outside the
product. All affected follow-up guests were discarded and baseline hashes
revalidated. Do not treat serial-v2 as a passed acceptance baseline; see
`v030-progress.md` for the historical evidence and failure analysis.

### 2026-11-03 v3 replacement

The owner authorized creating a replacement baseline from a newly downloaded
official Ubuntu 24.04 cloud image. The image SHA-256 was verified against the
signed Ubuntu `SHA256SUMS`:

```text
04e8f2461a16a44e78a370284471f29616cdd9baf1893df25807b71f985b6c94
```

The new baseline `ubuntu-noble-20261103-v3` uses the same 4-vCPU/6-GiB/40-GiB
limits, no NIC, no host share, no passthrough, and authenticated serial
administration. Its CPU model is pinned to `EPYC-Rome` instead of forwarding every
host feature; this avoids the host CPU-feature compatibility path associated with
the earlier instability. Baseline disk/NVRAM/firmware hashes are recorded in its
`baseline.json`.

Before any SpareHash installation, the v3 guest passed repeated cold boots,
`systemctl daemon-reload`, service start/stop, and 30 `fwupd-refresh` / reload
cycles followed by a 120-second survival window. The final v0.3 Ubuntu seven-
scenario acceptance then passed on a fresh clone using the signed offline fixture:
including forced power-off publication recovery and the broken-live-CLI backup
path. Bazzite remained Enforcing with no AVCs in its separate run. Evidence is
under the private lab results and `.omo/evidence/v030/milestone-4/ubuntu-final-v3/`
on the development host.

The VM lab **cannot certify**:

- Real Steam Gaming Mode gameplay lifecycle.
- Gamescope session and signal behavior under load.
- Native or Proton game launch/exit semantics.
- Physical thermal and scheduler behavior.
- The user's actual hardware, real wallets, or real mining pools.

For those, separate, owner-supervised testing is required. The VM lab is for
filesystem, packaging, lifecycle and SELinux-integration regressions only.

## 2. Accessing the lab

Lab tools live on the host and are not part of the SpareHash source tree.
Do not commit the launcher or any lab controller into this repository.

The launcher is fetched once into a private, gitignored local directory
**outside** the SpareHash project tree:

```sh
install -d -m 0700 "$HOME/sparehash-lab-client"
scp -o ForwardAgent=no bazzite-lab:/var/home/vmdev/sparehash-lab/tools/lab_remote.py \
    "$HOME/sparehash-lab-client/"
```

Define the launcher path:

```sh
LAB="$HOME/sparehash-lab-client/lab_remote.py"
```

The launcher is a self-contained Python 3.11+ script. It does not depend on any
SpareHash package and does not import from this repository.

Verify access and the lab state:

```sh
python3 "$LAB" status
```

The authoritative lab documentation, baseline manifests, and reproducibility
records remain on the host. They are read directly from the host over SSH
when needed:

```text
/var/home/vmdev/sparehash-lab/README.md
/var/home/vmdev/sparehash-lab/reproducibility.json
/var/home/vmdev/sparehash-lab/tools/SHA256SUMS
/var/home/vmdev/sparehash-lab/baselines/
/var/home/vmdev/sparehash-lab/results/
```

Do not copy those host files into the repository; they are host-internal.

## 3. Standard lifecycle

Run **one** disposable instance at a time. Always perform cleanup, including
on failure or cancellation.

```sh
# Ubuntu (current v3 serial baseline)
python3 "$LAB" create ubuntu-noble-20261103-v3 ubuntu-test-001
python3 "$LAB" start ubuntu-test-001
python3 "$LAB" exec ubuntu-test-001 30 /usr/bin/id
python3 "$LAB" stop ubuntu-test-001
python3 "$LAB" discard ubuntu-test-001

# Bazzite
python3 "$LAB" create bazzite-kde-44-20261012-v1 bazzite-test-001
python3 "$LAB" start bazzite-test-001
python3 "$LAB" exec bazzite-test-001 30 /usr/bin/getenforce
python3 "$LAB" stop bazzite-test-001
python3 "$LAB" discard bazzite-test-001
```

Inspect the launcher's `--help` output for the full command set, which includes
`collect`, `put`, `prepare`, `force`, `pause`, `seal`, and `discard` for
more advanced workflows.

## 4. Privileged command execution

The two baselines differ in how root operations are reached:

- **Ubuntu v3** uses an authenticated serial console. Console operations are
  slower than the historical QGA path but do not depend on a guest agent. The
  older `ubuntu-noble-20260926-v1` baseline remains available for historical
  comparison.
- **Bazzite KDE** uses an authenticated virtual serial console. Each `exec`
  call has approximately 24 seconds of authentication and logout overhead.
  Avoid many small commands; transfer scripts and run them in batches.

`exec` takes a timeout argument in seconds and returns the captured stdout
once the command finishes. Output is limited to 64 KiB per call. File
transfers and `collect` payloads are limited to 8 MiB.

## 5. Files in and out

Use `put RUN ARTIFACT_NAME GUEST_PATH` to copy a single file into a guest.
The first argument is the run name; the second is an **artifact name** that
must already exist under the host's `lab/artifacts/payloads/` directory
(restricted source — the launcher will not accept an arbitrary local path).
Copy or `scp` the file into that directory on the host first, then `put` it.

Use `collect RUN GUEST_PATH LABEL` to copy a single file (up to 8 MiB) out of
a guest into the host's `lab/results/RUN/` tree. Use `tar -C / -czf ...` or
similar inside the guest to bundle larger result trees into a single
archive before `collect`, respecting the 8 MiB cap.

### Transferring the SpareHash source tree

`put` moves single files, not directory trees, and the installer needs the
full source package (Python modules, unit files, templates), not only
`install.sh`. To run an installation test:

1. On the dev host, create a bounded source archive from the exact commit
   under test (for example `git archive --format=tar.gz <sha> -o sparehash-src.tar.gz`).
2. `scp` that archive into the host's `lab/artifacts/payloads/` directory.
3. `put RUN sparehash-src.tar.gz /var/tmp/sparehash-src.tar.gz`.
4. Inside the guest, extract to a scratch directory
   (`tar -C /var/tmp -xzf /var/tmp/sparehash-src.tar.gz`) and run the
   installer from the extracted tree.

Keep archives within the 8 MiB transfer cap and the 64 KiB per-command
output cap. Verify the extracted tree's commit (`git -C /var/tmp/sparehash
rev-parse HEAD` when the `.git` is included, or record the source SHA
alongside the evidence).

### SELinux labels on Bazzite

Do **not** invent or apply a custom SELinux policy type (for example a
`sparehash_var_lib_t`). No such type is established by the lab's gathered
evidence, and creating policy modules is explicitly out of scope. Write
guest evidence to a neutral path such as `/var/tmp/sparehash-evidence/`
(default `tmp_t` labeling) and `collect` it out. If a test triggers a real
AVC, record the AVC and let the product be repaired; never weaken SELinux to
make a test pass.

## 6. Required environment inside a guest

Before any SpareHash test:

- Confirm the system Python is at least 3.11. The Bazzite baseline ships
  Python 3.14.x; Ubuntu Noble ships Python 3.12.x. Both satisfy the project's
  `requires-python = ">=3.11"` without an interpreter override.
- Confirm `git` and `curl` are present.
- For Bazzite, confirm `getenforce` reports `Enforcing` and **do not** relax
  SELinux, `setenforce`, or generate custom policy modules. If the test
  triggers a real AVC, record the AVC and let the agent repair the product;
  do not weaken the policy to make the test pass.
- Confirm `systemctl --version` is at least 249 (Ubuntu Noble) / 257
  (Bazzite). SpareHash relies on `BindsTo=`, `PartOf=`, `KillMode=`,
  `LoadCredential=` and protected runtime directories.

Pre-stage the XMRig artifact on the test bench **before** running the
installer. Guest networking is disabled; the installer must NOT download
anything inside the guest. XMRig must be supplied as a verified offline
artifact: either a pre-built local binary with its SHA-256 and GPG
signature pinned, or a mock/synthetic artifact for offline tests. Stage
the artifact under the host's `lab/artifacts/payloads/` and `put` it into
the guest before running the installer (see §5 for the `put` syntax).

## 7. Running the v0.1.1 baseline

This is the historical initial-baseline procedure. Its verified records already
exist; do not repeat preservation or baseline tests on every cleanup/review task.
Subsequent tests must identify their own selected source and disposable image.

Before resuming v0.2.0 implementation, establish a v0.1.1 baseline so
regressions are detectable.

On Ubuntu:

```sh
# 1. fresh disposable
python3 "$LAB" create ubuntu-noble-20260926-v1 ubuntu-baseline-001
python3 "$LAB" start ubuntu-baseline-001

# 2. stage the v0.1.1 source tree (tag 335804f, sha
#    335804fa068fb16251fbfa9dacba44a46e5b5622) per §5
python3 "$LAB" put ubuntu-baseline-001 sparehash-src.tar.gz /var/tmp/sparehash-src.tar.gz
python3 "$LAB" exec ubuntu-baseline-001 30 /usr/bin/tar -C /var/tmp -xzf /var/tmp/sparehash-src.tar.gz
# 3. install from the extracted tree
python3 "$LAB" exec ubuntu-baseline-001 120 /usr/bin/sh /var/tmp/install.sh
python3 "$LAB" exec ubuntu-baseline-001 30 /usr/bin/systemctl is-active sparehash.service
python3 "$LAB" exec ubuntu-baseline-001 30 /usr/bin/systemctl is-enabled sparehash.service
python3 "$LAB" exec ubuntu-baseline-001 30 /usr/bin/test -f /var/lib/sparehash/install-manifest.json
python3 "$LAB" exec ubuntu-baseline-001 30 /usr/bin/test -f /var/lib/sparehash/state.json
python3 "$LAB" exec ubuntu-baseline-001 30 /usr/bin/test -f /etc/sparehash/config.toml
python3 "$LAB" exec ubuntu-baseline-001 30 /usr/bin/test -f /etc/sparehash/xmrig.json
python3 "$LAB" exec ubuntu-baseline-001 30 /usr/bin/test -f /etc/systemd/system/sparehash.service
python3 "$LAB" exec ubuntu-baseline-001 30 /usr/bin/test -f /etc/systemd/system/sparehash-xmrig.service
python3 "$LAB" exec ubuntu-baseline-001 30 /usr/bin/ls -lZ /opt/sparehash /etc/sparehash /var/lib/sparehash
# uninstall
python3 "$LAB" exec ubuntu-baseline-001 30 /usr/local/bin/sparehash uninstall --purge
python3 "$LAB" exec ubuntu-baseline-001 30 /usr/bin/ls -l /opt/sparehash || true
python3 "$LAB" stop ubuntu-baseline-001
python3 "$LAB" discard ubuntu-baseline-001
```

On Bazzite (fresh disposable; install must reproduce the known atomic
incompatibilities and refuse safely):

```sh
python3 "$LAB" create bazzite-kde-44-20261012-v1 bazzite-baseline-001
python3 "$LAB" start bazzite-baseline-001
# v0.1.1 install is expected to fail closed at the redirect-grammar
# preflight; record the exact failure mode, exit code, and audit text.
# stage the v0.1.1 source tree per §5
python3 "$LAB" put bazzite-baseline-001 sparehash-src.tar.gz /var/tmp/sparehash-src.tar.gz
python3 "$LAB" exec bazzite-baseline-001 30 /usr/bin/tar -C /var/tmp -xzf /var/tmp/sparehash-src.tar.gz
python3 "$LAB" exec bazzite-baseline-001 120 /usr/bin/sh /var/tmp/install.sh; echo "exit=$?"
# confirm no host state was changed
python3 "$LAB" exec bazzite-baseline-001 30 /usr/bin/getenforce
python3 "$LAB" exec bazzite-baseline-001 30 /usr/bin/ls -lZ /opt /usr/local
python3 "$LAB" stop bazzite-baseline-001
python3 "$LAB" discard bazzite-baseline-001
```

These baseline runs must be performed **first**, before any v0.2.0
installation or migration is tested. The recorded evidence becomes the
regression floor for the plan's Wave 5 acceptance (Tasks 25–30 in the
v0.2.0 plan).

## 8. v0.2.0 development on the lab

After the baseline, iterate against the lab:

- **Host and capability detection** (Tasks 4, 5): test on a fresh
  Ubuntu + a fresh Bazzite; confirm the layout/host/preflight modules
  agree with the actual atomic redirects on Bazzite and with
  /etc/os-release on Ubuntu.
- **Trusted path resolution** (Tasks 6, 7): feed synthetic
  redirect-only /opt and /usr/local layouts into a test and assert the
  pinned descriptor survives. The on-disk tests cannot exhaust the
  Bazzite redirect grammar but can prove the Bazzite-allowed
  relative and absolute variants.
- **Installation and uninstallation** (Tasks 10, 22): full lifecycle on
  fresh Ubuntu and Bazzite. Verify that Bazzite installs successfully
  when the trusted redirect grammar accepts the host's `../var/usrlocal`
  literal.
- **Historical upgrade/migration research** (old Tasks 19–23, 28): public
  upgrades, rollback, reconfiguration and completion are deferred in the
  revision-3 experimental v0.2 scope. Their CLI flags now refuse before effects.
  Retain internal private-tree safety tests and historical evidence; do not
  attempt a privileged migration or clear an old hold to bypass that refusal.
  See `docs/upgrade-v020.md` for current status.
- **Systemd lifecycle** (Tasks 11, 12, 18): confirm the unit hardening,
  `BindsTo=`, `ExecCondition=+/...`, and the static miner unit. Verify
  the mining service restarts cleanly across `systemctl daemon-reload`.
- **Controller and miner privilege separation** (Tasks 11, 12): confirm
  the miner runs as the unprivileged `xmrig` user, with the controller
  remaining root, and that nobody/nogroup is no longer assumed.
- **SELinux compatibility** (Task 3, Task 11 evidence): confirm the
  unlabeled probe identity executes, the `xmrig_config.validate_rendered`
  private-network sandbox holds under enforcing policy, and that AVCs
  are recorded and addressed, not silenced.
- **Safety blocker behavior** (`gaming_scan` / controller /
  `runtime`): run the unit-test suite on the host after every change
  that touches the gaming reducer or the controller.

Each session collects one evidence directory per task
(`.omo/evidence/v020/task-N/{happy.log,failure.log,regression.log,summary.json}`)
on the development host. Plan branches carry the new code; the evidence
lives in the local .omo tree.

## 9. Recovering from failed or unresponsive guests

If `exec` times out:

- `force RUN` immediately powers off the guest if it is wedged.
- `stop RUN` (or `force RUN`) must precede `discard RUN`: `discard`
  refuses active guests. Then `discard RUN` reclaims the overlay and frees
  the resources.
- `pause RUN` suspends a guest for recovery testing (it is a suspend, not
  a product-level pause); `start RUN` can resume a suspended guest without
  a full re-provision.
- Descendants of timed-out commands are reaped when the guest is discarded.
  Trust only evidence from instances you control end-to-end.

`prepare RUN` and `seal RUN BASELINE` are baseline-sealing operations, not
ordinary test recovery: `prepare` resets cloud-init/machine identity before
sealing, and `seal` flattens a powered-off baseline. Do not list them as
recovery steps (see §12).

If the Bazzite guest becomes unresponsive over the serial console, prefer
`force RUN` and a fresh `discard` + `create` cycle over repeated
authentication round-trips.

## 10. Operational constraints

- Run all destructive SpareHash installation, upgrade and uninstallation
  tests **only** inside a disposable guest.
- Never run those tests on the physical Bazzite host.
- Use the `vmdev` SSH account only; it has no host sudo.
- Do not modify the hypervisor configuration, baseline disks, resource
  limits or host security settings.
- Do not attach host directories, block devices, Nitrokeys, SSH agents or
  personal credentials.
- Keep guest networking disabled.
- Use only disposable test credentials.
- Do not connect to public mining pools or perform real mining.
- Use mock services or verified offline artifacts.
- Preserve SELinux enforcing in the Bazzite guest.
- Do not relax the Bazzite guest-agent confinement.
- Use the authenticated serial-console path for privileged Bazzite
  administration.
- Normally run only one disposable VM at a time.
- Always perform cleanup, including on failure or cancellation.

## 11. Known limits

- Bazzite serial commands have ~24 seconds of authentication/logout
  overhead per operation. Transfer scripts; run in batches.
- Command output is capped at 64 KiB; file transfers and `collect`
  payloads at 8 MiB. Test artifacts beyond this must be tar-bundled.
- A timed-out `exec` may leave descendants. Discarding the instance is
  the reliable reset.
- The VM lab cannot certify real Steam/Gamescope/Proton lifecycle or
  physical thermal behavior. Use separate, supervised tests for those.
- The host's `lab/artifacts/payloads/` source is the only path the
  launcher will accept for `put`. No other ingress to a guest is
  supported.

## 12. Avoiding contamination of sealed baselines

The baselines are sealed, hash-checked, and mode 0444. **Do not modify
them**. Always start a new run from a sealed baseline via `create`. If a
test appears to alter a baseline, you are looking at the overlay, not the
baseline itself. Use `discard` to release the overlay and verify the
baseline directory is unchanged with the host's recorded hashes.

Do not re-run `prepare` on a `*-ready` builder directory expecting it to
behave as an ordinary run. The launcher intentionally refuses to discard
builder directories; use a fresh `BASELINE` for ordinary tests.

## 13. How a future agent should use the lab

1. **Read this file first** and confirm the launcher is at
   `$HOME/sparehash-lab-client/lab_remote.py` and `python3 "$LAB" status`
   returns the expected baselines.
2. Use `ubuntu-noble-20261103-v3` and the current Bazzite baseline for new
   v0.3 acceptance; the v0.1.1/v0.2 baseline procedures are historical and
   need not be repeated.
3. Create one fresh disposable clone at a time, record the exact source and
   fixture identities, and run the required focused tests for the change.
4. For update/lifecycle acceptance, use the signed offline fixture and the
   current `qa/update_acceptance.py`; do not run destructive tests on the
   user's workstation.
5. **Re-baseline** any baseline that produces a `prepare` mismatch
   before trusting its results.
6. **Record durable evidence** under `.omo/evidence/v030/` on the
   development host. Keep generated test results and machine-specific files
   out of version control.
7. **Escalate to the owner** if a concrete requirement surfaces that the
   VM lab cannot satisfy (e.g. real Steam session lifecycle, physical
   Ryzen sensor accuracy).

## 14. Security and disclosure

The SpareHash repository is public. Do not commit private SSH keys,
credentials, personal network addresses, passwords, production wallets, or
other sensitive host details.

- The launcher is stored outside the SpareHash repository, in a
  gitignored local directory owned by the developer.
- The authoritative host records (README, baseline manifests,
  reproducibility) are read over SSH on demand and not committed.
- SpareHash's runtime source tree does not depend on the launcher. The
  launcher is a developer convenience, not a runtime dependency.
- Test wallet and pool credentials used in the VM lab are disposable
  test credentials only; never use real wallet or pool credentials.

## 15. See also

- `AGENTS.md` — agent instructions, including a pointer to this file.
- `docs/architecture.md` — SpareHash architecture, including the
  filesystem-trust and gaming-coverage sections that the lab must
  validate.
- `.omo/plans/sparehash-v020-cross-distribution.md` — the approved v0.2.0
  plan whose Tasks 3, 25–30, and F3 specifically require this lab.
