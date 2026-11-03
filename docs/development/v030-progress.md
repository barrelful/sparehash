# v0.3.0 implementation handover

Implementation is complete; **Ubuntu release acceptance remains blocked** below.
Production artifact signing is recorded separately; neither gate approves release. All PRs
use squash merge only after five green CI checks. GitHub links carry live final
merge/check status; no release approval is implied by merging preparation code.

| Milestone | PR | Implementation status |
| --- | --- | --- |
| 1: version / signed release identity | [#4](https://github.com/barrelful/sparehash/pull/4) | Merged after green CI |
| 2: discovery / verified staging | [#5](https://github.com/barrelful/sparehash/pull/5) | Merged after green CI |
| 3: app transaction / restoration recovery | [#6](https://github.com/barrelful/sparehash/pull/6) | Merged after green CI |
| 4: cleanup / acceptance / release preparation | [#7](https://github.com/barrelful/sparehash/pull/7) | Merged after green CI; follow-up below |
| Owner signer / exact-commit preparation | [#8](https://github.com/barrelful/sparehash/pull/8) | Squash-merged after five green CI checks; not release signoff |

## Historical milestone-4 validation (2026-11-02)

- Milestone-4 runtime source: `5b24684d156453e5701386197ea8e74ddaae420f`.
  Host-safe suite then: **1,258 passed / 567 subtests**. The subsequent follow-up
  below changes application public trust and release preparation, not just docs.
  Package typing, supervised-QA typing, focused lint, README inventory, stamped
  wheel/sdist/source/licence/dependency checks and isolated real GPG signing pass.
- All seven actual scenarios passed on **milestone-4 Bazzite**, SELinux Enforcing,
  zero captured AVCs, including power-cycle/broken-live-CLI backup recovery,
  automatic activation-failure restoration and receipt-aware uninstall.
- All seven passed on earlier Ubuntu/Bazzite source `a275f7a`. Intermediate M4
  Ubuntu `5aa3184` passed seven plus actual fresh-reinstall/receipt-reset/uninstall.
- **Milestone-4 Ubuntu: INCONCLUSIVE, not a pass.** Two disposable attempts lost
  the guest-agent transport; a controlled power-cycle still had no connected
  agent. No diagnostic product conclusion or mock substitution is claimed.
- Those historical guests were stopped/discarded. Networking stayed disabled;
  no host lifecycle/mining, real wallets/pools, policy changes or storage shares.
- Evidence (gitignored): `.omo/evidence/v030/milestone-3/{ubuntu,bazzite}/`,
  `milestone-4/{ubuntu-pre-final,bazzite-final}/`, and
  `milestone-4/ubuntu-final-blocker.json`; final fixture manifest/public test key
  and signed fixture bundle are also under milestone-4. An initial Bazzite serial
  transfer timeout was resolved by stop/restart and compact, runtime-identical
  signed fixtures, not by weakening lab policy.
- The real builder produced verified **test-key-only** complete source + `.asc`
  and fully stamped wheel/sdist at `/tmp/opencode/v030-verified-test-artifacts/`.
  No-Git identity agrees on that milestone's commit. Temporary isolated-clone test
  tags were removed; no project/origin v0.3.0 tag or published release exists.

## Owner-authorized follow-up (2026-11-03)

Current diagnostic status: Ubuntu v2 at product commit
`3fe79840c7aa41a507ed613946ee5003aa6b08ad` passed scenarios 1–4 and both
interrupted-publication/reboot-recovery checkpoints (5a/5b), but **not all seven**.
During scenario 6, PID 1 (`systemd`) segfaulted and the Ubuntu kernel panicked
(`Attempted to kill init`, exit `0x8b`, kernel `6.8.0-142-generic`). The private
lab console log was preserved before powering off the owned guest. This is an
actual guest OS crash, not merely a QGA/serial transport timeout. Scenarios 6–7
did not complete in that attempt; its underlying OS root cause remains unknown.
After controlled reboot, durable recovery succeeded. A diagnostic repeat of the
signed activation-failure update exited 1 and automatically restored the active
0.3.2 application with no pending journal. That single retry did not panic. The original
panic is not explained by that retry; console/journal/overlay evidence is retained
privately, and the first diagnostic guest was stopped/discarded. Fresh acceptance
on `v030-ubuntu-final`, using product source
`095b17d7986702b37bd76193ae4ed83dd9671c45` and independent fixture source digest
`4d8f671eb6f6b2145d3cfdf5ae99c31be4aef891ba1dc9c497b3c3cc52f74bf0`,
also panicked, before the first signed update (during the startup-preference
daemon reload). **This fresh run did not pass seven scenarios.** A bounded native
reproducer, with SpareHash stopped, also panicked while activating Ubuntu's
`fwupd-refresh.service` alongside `systemctl daemon-reload`. Thus the same crash
is reproducible outside the updater; its exact underlying OS defect is not yet
identified. The stopped overlay and console/journal evidence are preserved.
A separate provisioning overlay, with **no SpareHash installed**, was updated
offline using 13 Ubuntu archive-authenticated packages (all package SHA-256 values
matched the authenticated APT metadata), then rebooted into kernel
`6.8.0-147-generic` and systemd `255.4-1ubuntu8.18`. It also suffered the same
PID 1 panic. This bounded repair attempt failed; it was not sealed as a baseline
and no seven-scenario pass is claimed. There is no identified product-code defect
to patch from these results; the independently reproducible OS failure remains
a release blocker, not a reason to weaken isolation or substitute mocks.

All owned guests were stopped/discarded; the lab has no guest definitions.
Original and serial-v2 disk/NVRAM/firmware hashes were revalidated at closure.
Private native reproducer, console/journal, stopped-overlay and offline OS-install
records are retained on the designated lab. Local gitignored evidence is under
`.omo/evidence/v030/milestone-4/nitrokey-followup/`, including
`panic-evidence.tar.gz`, `diagnostic-final.tar.gz`, `fresh-failure.tar.gz`,
`current-security-native-panic-console.log` and `final-fixture-manifest.json`.

PR [#8](https://github.com/barrelful/sparehash/pull/8) is the focused public-key and
release-preparation follow-up, squash-merged as
`9de1e004594ff07dd856cfdaa837981e157f8dab`, not release signoff. Its
[main CI](https://github.com/barrelful/sparehash/actions/runs/41705076069) passed
all five checks, after [PR CI](https://github.com/barrelful/sparehash/actions/runs/41704889573).
Local suite: 1,262 passed / 567 subtests;
package typing, focused lint, README inventory and development artifact checks pass.

- Owner authorized designated-lab administration/provisioning repair. Original
  baselines are preserved; a fresh Ubuntu authenticated-serial baseline was sealed
  as `ubuntu-noble-20261103-serial-v2`, with unchanged isolation/resources. Serial
  administration works, but the baseline is **not** validated for acceptance. Private
  lab controller originals/checksums were backed up outside this repository.
- Nitrokey inspection independently matched its existing signing subkey
  `3BA6A67F3A744D30C11772D6E2BB137A69EC321F` to primary
  `23E9CE0E1C95324190571379766F2AE440E1019A`, suitable/unexpired. Owner authorized
  its public certificate/pin; no new production key or private export. Public
  pin and exact-commit builder preparation are merged in PR #8.

## Production signing outcome (2026-11-03)

**Verified, no outstanding hardware interaction.** After independent lab closure,
tests and green main CI, one bounded normal `--pinentry-mode ask` builder invocation
signed the complete archive of clean commit
`9de1e004594ff07dd856cfdaa837981e157f8dab` with the existing Nitrokey. The signature
issuer is `3BA6A67F3A744D30C11772D6E2BB137A69EC321F`. Verification passed using
`verify_archive(archive, signature)` with **no trust arguments**, both from the
repository and from the archive's extracted application. No private export, PIN
logging/supply, new production key or touch bypass was used.

Stamped source, wheel and sdist identities match that exact commit; GPL/attribution,
package source contents and zero runtime dependencies passed read-only checks.
The source archive has the detached production signature; wheel/sdist are stamped
packages, not separately detached-signed. Subsequent handover edits are docs only.

Artifacts (gitignored, not published):
`.omo/evidence/v030/milestone-4/nitrokey-followup/production-prepared-9de1e00/`.
Evidence: adjacent `production-verification.json`, plus artifact `SHA256SUMS`.

| Artifact | SHA-256 |
| --- | --- |
| `sparehash-0.3.0-source.tar.gz` | `7e78dcb76ef4f660a0b44a877c78b50ee0d12516c7e0f755390ed4166f926c4d` |
| `sparehash-0.3.0-source.tar.gz.asc` | `e7de399e5965b0046e9be4b6c071abd1b54fa9b8f1b77e9d0c1c8ce2496c1662` |
| `sparehash-0.3.0-py3-none-any.whl` | `367eee88a2aabd05edf1b8b40b4dfdf55f18035e8b33fd810661aa6d6b1a4782` |
| `sparehash-0.3.0.tar.gz` | `500213f0bd081695ccad596c704b19ecf04773f1d61a2bc597fb998b85971c34` |

Read-only verification from the repository root (no installation/card/PIN needed):

```sh
python3 - <<'PY'
from pathlib import Path
from sparehash.app_release import verify_archive
base = Path('.omo/evidence/v030/milestone-4/nitrokey-followup/production-prepared-9de1e00')
archive = base / 'sparehash-0.3.0-source.tar.gz'
verify_archive(archive, Path(str(archive) + '.asc'))
print('Production archive signature verified against bundled application trust')
PY
```

## Remaining release gates

The public production trust anchor is populated and independently matched to the
existing Nitrokey; it is not empty. Authenticated serial administration is working
without a QGA dependency. **Ubuntu seven-scenario acceptance is still blocked.**
Proposed next step: independently provision a pristine official Ubuntu image (not
derived from the affected lab baseline), reproduce the native OS case before any
SpareHash installation, and obtain an upstream debugger/backtrace if it persists.
Only then repeat all seven scenarios on the recorded runtime source. Do not
claim the diagnostic recovery/retry as a full acceptance run.
Production signing/default-trust verification is complete above; it does not
remove the Ubuntu release blocker. No further hardware interaction is required. Final tag and
publication require separate owner approval and are not part of this task.

Real read-only GitHub discovery was exercised; existing releases lack `.asc`.
Published production-signed download verification and online end-to-end guest coverage are
not claimed. GitHub HTTPS works; SSH writes timed out. OAuth lacks `workflow`
scope, so existing CI stayed unchanged; new tests run in its full suite and new
files were linted locally. No workstation lifecycle/mining has been performed.
