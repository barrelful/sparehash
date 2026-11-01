# SpareHash v0.2.1 (Experimental)

v0.2.1 is an **Experimental/Beta pre-release**, not production-stable. It fixes
the UNVERIFIED-platform installation blocker in [issue #3](https://github.com/barrelful/sparehash/issues/3).
The v0.2.0 tag is unchanged. The underlying mining/safety architecture and
[v0.2.0 limitations](release-0.2.0.md) remain, except for this installation fix.

## Fix

`--acknowledge-unverified` explicitly accepts experimental compatibility for
read-only preflight or installation. Eligible Mint/other UNVERIFIED hosts can
reach READY, while their support label stays **UNVERIFIED**. Generic `--yes`
does not supply compatibility acknowledgment; unattended installation still
requires separate `--answers FILE --yes` effects consent.

This flag does not override unsupported hosts, missing evidence, unsafe
capabilities, existing-install refusal, receipts, retirement or lifecycle holds.
No gaming, scheduling, battery, thermal, hugepage or privilege policy was weakened.
No GPU mining, MSR access or security-policy modification was introduced.

## Validation and remaining gaps

The implementation at `2a03c6a401d3e3c91dea5834f904678842b3ddb1` passed 1,266
local tests plus 565 subtests, typing, scoped lint, README checks and artifact
checks. All five hosted CI jobs passed, covering Python 3.11–3.14 and packaging:
https://github.com/barrelful/sparehash/actions/runs/41334502720 .
Release-alignment changes after that commit are documentation-only; the GitHub
release records the exact final commit, final CI run and artifact checksums.

### Physical Mint 22.3

On one Core i5-8265U Mint 22.3 machine (Python 3.12.3, systemd 255):

- Owner completed receipt-aware non-purge removal and explicit staged reinstall.
  All 58 deployed Python/template files matched the reviewed source, and all 61
  protected file receipts matched. Signed XMRig 6.26.0 installation succeeded.
- Actual mining/API operation worked, with an observed snapshot around 720 H/s
  and 65°C. These are observations, not performance or thermal guarantees.
- Direct process inspection confirmed unprivileged execution, zero effective
  capabilities, NoNewPrivs, seccomp, and all 12 observed threads using
  SCHED_IDLE/nice 19; cgroup idle mode was active.
- Manual pause survived controller restart. Physical AC unplug inhibited mining
  even after clearing manual pause; AC reconnection restored fresh power readings.
- Hugepage allocation was **partial: 48 of 1,280 requested pages**. Observed
  allocation/restoration was **0 → 48 → 0**; no forced reservation was attempted.
- A transient stale gaming scan on battery safely inhibited mining. A later AC
  snapshot showed healthy scanner recovery. Sustained scanner timing is unverified.
- Last observed state: controller active, mining manually paused, boot startup
  disabled. No boot enablement was performed by this deployment.

Mint remains **UNVERIFIED**: one machine does not certify every Mint installation.
Real Mint gameplay, thermal fault injection and physical reboot/autostart were
not tested. No simulated sensor fault, synthetic game or benchmark was used as
evidence of physical gameplay or thermal behavior.

### Disposable VMs

- **Bazzite 44:** all 11 synthetic release-smoke checks passed, collected summary
  `complete=true`, SELinux Enforcing. Includes install, idle priority, simulated
  safety inputs, controller loss, removal, pause-preserving reinstall and purge.
  Full audit had two stock `tuned_t` denial lines and no other denial lines.
- **Ubuntu 24.04:** v0.2.1 smoke **INCONCLUSIVE** after guest-agent disconnection
  during execution; collection was unavailable. No pass or product-failure cause
  is inferred. Prior v0.2.0 dual-image/reboot passes are historical evidence only.
- Both owned guests were forced off/discarded. These v0.2.1 smoke runs did not
  perform reboot acceptance. No baseline, networking or security guard was relaxed.

## Installation

From a separate checkout if you have local work:

```sh
git clone --branch v0.2.1 --depth 1 https://github.com/barrelful/sparehash.git
cd sparehash
git rev-parse HEAD
sudo ./install.sh --preflight
```

For Mint or another UNVERIFIED host, review [platforms](platforms.md) and use:

```sh
sudo ./install.sh --preflight --acknowledge-unverified
sudo ./install.sh --acknowledge-unverified --no-start --no-enable
```

For validated release lines, omit the acknowledgment flag. Supply credentials
only in the local interactive wizard. Verify installed files, sensors and unit
containment before `sudo systemctl start sparehash.service`. Do not enable boot
startup until supervised checks pass; `sparehash start` also enables boot startup.

**No in-place upgrade is supported.** Existing installations require completed
receipt-aware removal before explicit reinstall. Normal removal retains config:

```sh
sudo sparehash uninstall
```

Review older uninstall implementations before using them. Modified/foreign files,
partial removal and unresolved holds can require maintainer assistance; never
erase receipts, installation directories or lifecycle records to force progress.
After completed removal only:

```sh
sudo ./install.sh --clear-lifecycle-latch --no-start --no-enable
# Add --acknowledge-unverified on UNVERIFIED platforms.
# Add --operation-id ID only if the retirement guidance requires it.
```

`--purge` additionally removes unchanged receipt-owned configuration, not all
state or foreign files. Public update/rollback/config-edit/benchmark workflows
remain deferred. See [owner testing](testing-0.2-owner.md) and
[SECURITY.md](../SECURITY.md). Keep wallets, tokens and private logs out of reports.
