# S4: release-surface and QA alignment

> Historical S4 closure record. The subsequent [candidate report](../release-0.2-candidate.md)
> tracks S5 verification and supersedes this stage's remaining-blocker status;
> earlier failures and evidence below are retained, not retroactively relabelled.

**S4 implementation/local gates are complete; final Ubuntu VM QA is blocked by
the lab guest-agent transport.** Final pinned Bazzite QA passed. This is not
combined final-image acceptance, a frozen candidate, RC or gameplay certification.

## Changes

- Public `update`, `complete-upgrade`, `config edit`, `benchmark` and installer
  `--upgrade`, `--recover`, `--complete-upgrade`, `--reconfigure` refuse with exit
  2 before host inspection/effects. Empty operation IDs cannot bypass refusal.
  Help labels these modes deferred; unsupported-host remediation no longer
  suggests an unavailable native packaging channel. Existing migration holds,
  journals, algorithms and private-tree regressions remain, not a supported
  public alternate route. No upgrade/rollback implementation was expanded.
  Ordinary installation additionally checks the protected CLI read-only and
  refuses an existing installation before capability detection or retirement
  clearing. Completed removal deletes the CLI, preserving explicit reinstall.
- Preflight's validated release lines now match the two evidenced images:
  Ubuntu `24.04` and Bazzite `44`. Debian/Mint, older/newer Ubuntu/Bazzite and
  other families do not inherit validation. Documentation distinguishes release
  ID matching from exact-image authentication and actual hardware/gameplay proof.
- Replaced the broken release-smoke entrypoint with the S3-proven synthetic path:
  `qa/qa_release_smoke.sh` → `qa/release_smoke.py`. It requires explicit `--yes`,
  disposable-VM opt-in, a verified source SHA-256, fresh paths, systemd/root and
  no guest NIC. It fixes helper PATH and clears inherited import/proxy/product
  overrides before privileged actions. No lab controller is shipped or required.
- The owned, GPL-licensed `qa/fixtures/synthetic_xmrig.py` never hashes or contacts
  a pool. The smoke uses actual installation, probes, protected runtime/IPC/systemd,
  file-backed sensor callbacks, stop proof and strict receipt removal. It tests
  foreign-content refusal, pause-preserving reinstall and purge, ends uninstalled,
  and marks success only after cleanup and journal/AVC checks. It does not reboot;
  actual reboot acceptance remains the separately recorded S3 gate.
- Root README, security/platform/upgrade/QA/developer docs and metadata now state
  exact installer exits, the real user/group/permission model, scoped image evidence,
  best-effort polling/freshness/dwell and fixed failure reasons, removal retention
  semantics, and explicit upgrade/rollback/native-packaging/benchmark deferrals.
  Historical packaging proposals/transaction code remain research. The owner's
  dirty `docs/release-0.2-status.md` is not edited.
- CI now declares Python 3.11–3.14, locked development installation, scoped lint,
  README inventory, typing, isolated builds and stdlib artifact inspection through
  `qa/check_artifacts.py`. Wheels/sdists carry GPL/attribution; no mandatory runtime
  dependency is added. Hosted GitHub CI has not been triggered (no push).

The previous entrypoint, upgrade guide and all touched existing source/test/docs
were preserved in targeted archives under
`~/sparehash-archive/s4-release-alignment-20261026-223247/`, including additional
test/doc snapshots. T0 and prior stage archives/evidence are retained.

## Local validation

All versions used separate locked development environments, not extra runtime
dependencies. A stable 3.14.9 was installed privately for the matrix; the older
local uv initially selected an RC interpreter, which was not counted as stable
acceptance. No interpreter or dependency override is needed by the product.

| Interpreter | Complete unit suite |
| --- | --- |
| CPython 3.11.13 | 1,159 tests; 608 subtests passed |
| CPython 3.12.3 | 1,159 tests; 608 subtests passed |
| CPython 3.13.5 | 1,159 tests; 608 subtests passed |
| CPython 3.14.9 | 1,159 tests; 608 subtests passed |

Mypy is clean across 56 production files under all four interpreters. Locked-tool scoped lint, README inventory
and `git diff --check` pass. Other touched older files retain pre-existing Ruff
findings without new findings. Offline wheel/sdist builds and source/GPL/notice/
no-runtime-dependency inspection pass; final artifacts are built from the selected
working-tree snapshot, not misrepresented as a clean committed RC.

One initial matrix run exposed an older INI no-op test comparing access times.
Reads may legitimately update atime. The corrected regression still checks exact
content/backup bytes, inode, mode, owner/group, size, mtime and ctime. A bootstrap
preflight fixture was also made independent of the host's distro. No production
protection or safety assertion was weakened to obtain the matrix pass.
The final installation guard also exposed retirement fixtures that had not
isolated the protected CLI lookup. They now model that CLI as absent (completed
removal), while retaining every hold, corruption and operation-ID assertion;
the separate existing-CLI regression verifies refusal before retirement clearing.

## VM evidence and limits

Development versions of the entrypoint passed on both available image families.
The final source SHA-256 is:

```text
2f9e83812c8f3c7e0c8067854bd232448817b7917dcdd8251cda4f72c6535150
```

| Snapshot / image | Result |
| --- | --- |
| Previous hardened Bazzite snapshot (`f754c9b…`) | 11 checks and cleanup passed; Enforcing, no observed AVCs |
| Previous hardened Bazzite synthetic game → empty under foreground load | 1.012 s |
| Previous hardened Bazzite controller loss → empty | 0.019 s |
| Final installation-guard Bazzite snapshot | 11 checks and cleanup passed; Enforcing, no observed AVCs |
| Final Bazzite synthetic game → empty under foreground load | 1.012 s |
| Final Bazzite controller loss → empty | 0.021 s |
| Ubuntu development snapshot before QA environment hardening | 11 checks and cleanup passed; game → empty 1.007 s |
| Previous hardened Ubuntu attempt | Guest-agent timeout; product/test outcome unknown, not accepted |
| Final installation-guard Ubuntu snapshot | Not run: transport blocker remains; not accepted |

The final Ubuntu failure was the lab's `guest agent didn't respond ... within
'5' seconds` error, not a guest test verdict. It was immediately forced off and
discarded. An earlier Ubuntu transport failure was likewise discarded. No agent
confinement, baseline, resource limit, launcher or host policy was changed, and
the earlier successful Ubuntu snapshot is not counted as final-source acceptance.
All owned VMs, including successful Bazzite/development runs, are stopped and
discarded; final lab status is empty.

**Blocker / proposed next step:** the owner/lab maintainer should revalidate
Ubuntu guest-agent command/transfer reliability, then rerun the selected pinned
payload through the documented entrypoint in a fresh Noble disposable. Do not
weaken security or substitute mocked distro evidence. Final combined VM/RC
acceptance remains incomplete until that succeeds; do not silently advance S5.

The Steam detector is Lutris-derived with pinned GPL attribution; the synthetic
miner is SpareHash-owned, not upstream XMRig. These runs are not signed-upstream
artifact, real mining-performance, physical battery/thermal, native/Proton,
KDE/Gaming Mode or arbitrary foreground-workload certification. Prior research
QA suites remain historical/broader tools, not required default release gates.

Private evidence is gitignored under `.omo/evidence/v020/task-2/s4-release/`, with
source identities, interpreter/test logs, failed attempts, VM summaries/journals,
lint baseline comparisons and artifact/recovery checks. The scanner/controller,
miner containment and uninstaller policy remain unchanged by S4. No host
installation change, commit, push, RC publication or S5 work is performed.
Work remains on `atlas/v020-task-2-g1-feasibility`; the complete preceding S3
lifecycle/reboot evidence remains valid for its recorded snapshot and is not
rewritten as final S4-source evidence.
