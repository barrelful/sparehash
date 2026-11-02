# SpareHash v0.3.0 implementation plan

Revised scope: a functional experimental updater, not a deployment framework.
Planning only; no implementation or installation changes have been performed.

Repository inspection baseline: `main` at
`64726ac395e2be9bdb39bef4ba86796dd33f600c` (v0.2.1), confirmed against GitHub
on 2026-11-02. The owner approves the general architecture; this revision narrows
its recovery, signing, cleanup and validation requirements.

This is the same plan, revised in place. Plan mode permits saving only under
`~/.opencode/plan/`; the repository remains unchanged. When implementation begins,
the approved plan can be copied to tracked `docs/development/v030-plan.md`.

## 1. Scope and decisions

Required: `--version`, `update --check`, online and verified local application
updates, safe failure/interruption recovery, directly related cleanup, accurate
user documentation, and a lightweight release workflow.

Decisions that keep v0.3 small:

- One authoritative version, one signed source-archive format, one update journal
  and one previous-application backup.
- Ordinary installation/activation failures automatically restore the old app.
  `update --recover` restores an incomplete update; it does not choose among
  historical versions or attempt general roll-forward repair.
- **No manual rollback after a successfully completed update.** A retained backup
  is recovery material, not a public version-switching feature.
- Keep configuration formats and the existing installation layout. Application
  updates do not update XMRig, regenerate credentials or change startup preferences.
- No v0.2.1 migration: the owner will remove it and freshly install v0.3.0.
  Updates support v0.3.0 onward, without old-alpha compatibility adapters.
- Defer the separate XMRig command to a possible v0.3.1, broad historical-doc
  reorganisation, unrelated QA cleanup, background updates and architectural rewrites.

Runtime remains standard-library-only, Python >=3.11. Reuse system GPG rather
than add a Python cryptography dependency. No new distribution channel, security
policy module or privileged root is needed.

## 2. Existing code to reuse or replace

| Files | Implementation direction |
| --- | --- |
| `sparehash/__init__.py`, `pyproject.toml`, `__main__.py` | Version is duplicated as `0.2.1`; no public version/commit reporting exists. Keep lightweight dispatch and make package metadata derive from one version literal. |
| `xmrig_release.py`, `xmrig_verify.py` | Reuse bounded HTTPS transport and isolated GPG verification techniques. Repository selection and signer pins must remain separate for SpareHash and XMRig. Do not pass user originals to verification helpers that delete failed inputs. |
| `controller.py`, `controller_io.py`, `lifecycle_lease.py`, `cli_lifecycle.py` | Reuse restart-surviving update holds and existing lifecycle IPC. Manual pause is independent. The existing `cli_lifecycle.update` updates the miner, not SpareHash; leave that command unexposed. |
| `runtime_jobs.empty()`, runtime cleanup, systemd units | Reuse actual miner-cgroup-empty proof and owned hugepage cleanup. `install.empty_proof()` checks service activity only and is insufficient for publication. |
| `layout.py`, `deployment.Tree`, `install_manifest.py`, receipt modules | Keep protected path operations, Atomic redirect handling, store locking and ownership receipts. App and miner share a manifest: change only app-owned records. |
| `bootstrap.py`, `systemd.py`, selected `install.py` helpers | Reuse inventory/copying, launcher generation and unit rendering/verification. Never call the complete installer to update: it also selects XMRig and changes config/startup state. |
| `upgrade.py`, `migration.py` | Rework `upgrade.py` into the one app transaction. Existing code replaces source only, omits deployed unit/launcher updates and needs receipt fixes. `migration.py` is a legacy completion engine with an incompatible journal at the same `migration.json` path; remove it when superseded. |
| Existing tests, `qa/release_smoke.py`, `.github/workflows/ci.yml` | Extend useful fixtures/checks. Keep Python 3.11–3.14 CI, typing, focused lint, docs/artifact checks and zero runtime Python dependencies. Do not build another QA framework. |

Release discovery must account for a concrete repository fact: published releases
are GitHub pre-releases. At inspection `/releases/latest` returned 404, whereas
`/releases` listed v0.2.1. Its assets have checksums but no application signature.
A signed Git tag alone does not authenticate arbitrary attached release assets.

## 3. Version identity and release authentication

### Version and commit

- Keep `__version__` in lightweight `sparehash/__init__.py` as the sole version
  literal; configure Hatch dynamic version metadata from that file.
- The release builder generates `sparehash/_build.py` containing the full source
  commit, before packaging. It does not duplicate the version. Build from an
  exact clean commit/tag, not a moving `main` reference.
- Official source archives and package artifacts carry that stamp. Installed
  `--version` reads only public package data and works without Git. An unstamped
  development checkout may honestly say `commit unknown`; build/stamp it before
  using it as an identifiable installation. Do not add runtime Git probing.
- Replace misleading generic `v1`/`v0.2.0` feature messages with version-neutral
  wording. Do not rewrite legitimate historical version references.

### Brief signing comparison and choice

| Option | Assessment |
| --- | --- |
| HTTPS plus GitHub checksums | Smallest implementation, but no independent signer authentication. Insufficient for downloaded code run as root. |
| Detached GPG signature on the complete archive | Authenticates all archive bytes with existing system tooling. No signed per-file inventory/parser is necessary. Local updates need an adjacent signature file. |
| Previous signed per-file manifest inside the archive | Defensible and self-contained, but introduces an inventory schema and verification logic we do not need if the complete archive is signed. |
| Sigstore/attestations or another signing ecosystem | Potentially useful later, but adds tooling/trust procedures without a present benefit over GPG. |

**Choose a detached GPG archive signature.** Publish the existing complete-source
asset `sparehash-X.Y.Z-source.tar.gz` and its `.asc` signature. Optional
`SHA256SUMS` remains a convenience, not the authenticity check. This changes the
earlier embedded-manifest proposal specifically to remove unnecessary machinery.

Bundle the maintainer public key and pin its full primary fingerprint in the
installed application, separately from XMRig's key. The owner confirms/provides
that public key once; reuse the existing maintainer signing key if suitable.
No secret key in the repository or CI, keyserver lookup, signing service or
rotation framework. The current installation verifies the next release; a key
supplied solely by an untrusted candidate never establishes trust.
Fresh-install instructions must likewise verify the archive with an independently
confirmed key/fingerprint before `sudo ./install.sh`; a key inside that same
download is not sufficient initial authentication.

Verification pipeline, shared by online and offline updates:

1. Download/copy archive and signature into private root-owned staging. Snapshot
   regular local inputs with no-follow reads; never delete the user's originals.
2. Verify the exact staged archive using isolated GPG, the pinned signer, fixed
   environment and bounded execution. No candidate code runs before this succeeds.
3. Safely extract into private staging: reject traversal/absolute paths, links,
   special files and duplicates; bound compressed/expanded sizes and file counts.
   Reuse existing archive safety patterns and choose ordinary product file modes.
4. Check the staged version/commit and required source/templates. Online selection
   must match the authenticated artifact's version. Then candidate preflight code
   may run; never execute arbitrary release hooks or rerun its installer.

One local build command stamps identity, builds, signs and verifies the archive.
For offline use, copy `release.tar.gz` and `release.tar.gz.asc` together, including
when renaming them. This small two-file requirement is the signing simplification's
explicit usability trade-off. No unsigned or skip-verification option.

## 4. CLI and discovery

| Command | Behaviour |
| --- | --- |
| `sparehash --version` | `SpareHash 0.3.0 (commit abc1234)`; no root, network, IPC, Git, host detection or private config/state access. |
| `sparehash update --check` | Read public installed identity; report installed/latest version and release URL. No installation changes or controller dependency. Availability information is not an artifact-authenticity claim. |
| `sudo sparehash update` | Download, verify, stage, install and verify the newest release; restore prior controller activity. Already-current is a successful no-op. The explicit root command is consent; no extra prompts. |
| `sudo sparehash update --from ./release.tar.gz` | Same updater, fully offline; require adjacent `./release.tar.gz.asc`. |
| `sudo sparehash update --recover` | Recover the one pending transaction locally. Restore the old app if installation was incomplete; finish finalisation only if already durably committed. No pending transaction means no version change. |

Make check/from/recover mutually exclusive. Use exit 0 for a successful check,
including update available; exit 2 for syntax/non-root mutation refusals; exit 1
for trust/network/update failures. A failed update that successfully restores the
old app still exits nonzero and reports that outcome. On interruption, retain
recovery material and print the recovery command. No `--rollback` or `--force`.

Use `barrelful/sparehash/releases`, selecting the highest published numeric
`vMAJOR.MINOR.PATCH` with the source/signature pair, including GitHub-marked
pre-releases. Compare integer tuples; ignore drafts/nonmatching development tags.
Use bounded discovery/downloads and existing GitHub HTTPS redirect restrictions.
Network/rate-limit errors are errors, not "up to date". Never downgrade or silently
fall back after signature failure. Equal-version/equal-identity offline updates
are no-ops; conflicting identities at the same version refuse.

## 5. Small update transaction and recovery

Keep existing runtime paths, resolved through `Layout` on Bazzite. Use private
children under existing roots: one staging directory under `/opt/sparehash`,
`/opt/sparehash/.update-backup/` for the previous app, and
`/var/lib/sparehash/update.json` for the one bounded journal. No legacy journal
conversion. Preserve unknown/foreign contents rather than deleting broadly.

### Normal update

1. **Prepare.** Authenticate/stage before downtime. Require a ready receipt-owned
   v0.3+ installation and compatible candidate. Refuse retirement, partial removal,
   unrelated holds or modified/foreign app files/units. Reserve the transaction
   under the existing store lock; record original app identity, controller
   active/inactive and boot preference. A journal prevents competing mutations.
2. **Stop safely.** With a running controller, use its durable update hold and
   stopped/empty acknowledgement. Stop controller/miner and independently confirm
   `runtime_jobs.empty()` and owned cleanup. With a stopped controller, establish
   the same durable inhibition offline and confirm empty; no need to start it
   just for IPC. An active but unreachable controller must be stopped/proven empty
   or the update refuses. Never substitute manual PAUSE/RESUME for update inhibition.
3. **Back up, then install.** After quiescence, preserve the old complete app,
   launcher, deployed units and app receipt information in the single protected
   backup. Check config/credential/manual-state preservation at this point so a
   pause requested before stopping is retained. Finish and verify the backup
   and durably flush it before marking installation started. Execute the updater
   from this known-good source copy to avoid mixed imports. Replace staged source, launcher and both
   host-rendered units using protected renames/file replacement; daemon-reload.
4. **Update receipts and check.** Refresh app identity and all affected receipts,
   including added/deleted modules and directory identities. New owned files get
   proper removal authority. Preserve miner/config/account records and avoid an
   ever-growing action history. Check installed version, files/modes, config
   readability and unit verification. If previously active, start the new controller
   under the hold and require readiness/IPC with mining still inhibited. If
   previously inactive, use offline checks and leave it stopped.
5. **Commit.** Once checks pass, durably record commitment before releasing this
   update's inhibition. Restore activity, not obsolete mining decisions: the
   controller re-evaluates current blockers/dwell. Boot enablement never changes.
   Keep one previous app backup, clean transaction-owned staging/journal and
   report the installed version. Interrupted finalisation is completed by recovery,
   not treated as permission for post-success rollback.

Do not rewrite `config.toml`, `xmrig.json`, `xmrig.runtime.json` or manual
`state.json`; do not rotate tokens or alter XMRig/current, accounts or gaming
hooks. The updater must not use `sparehash start` to restore activity because
that command also enables boot startup; use the underlying service operation.

### Recovery rules — restore, do not build a repair framework

Use a few journal states: `prepared`, `installing`, `restoring`, `committed`.
Record only operation/backup identity, prior activity, relevant inventories/receipts
and outcome needed for recovery. No per-file progress history or general migration
state machine. Durable temp-write/fsync/replace is sufficient for the record.

- Before `installing`, no live app files have changed. Recovery verifies the old
  installation and restores its operational state; an unfinished backup is not
  mistaken for a ready recovery source.
- During `installing` or `restoring`, ordinary failures automatically restore the
  complete backed-up app/launcher/units and refresh app receipts. `--recover`
  performs the same restoration after a killed process or reboot. Always restore
  from the intact backup rather than infer which individual writes to roll forward.
  Keep it until restoration and checks finish; repeated recovery can repeat safely.
- `committed` means the installed app and receipts passed checks, whether the
  outcome was an update or restoration. Recovery only completes inhibition release
  and cleanup. An obsolete retained backup cannot initiate a new restoration.
- If stop proof, backup integrity or restoration cannot be established, leave
  mining inhibited, keep evidence and report the concrete failure. No force-clear.

Incomplete/corrupt update records must inhibit startup/miner authorization even
across reboot, including when the separate hold is missing. Generic lifecycle
abort/completion cannot release an incomplete app update. Keep status/health IPC
available under inhibition. Clear only this operation's matching hold after verified
commit/restoration; unrelated holds and retirement remain authoritative. Check
the journal under short store locks for updater/install/uninstall conflicts; do
not hold a manifest lock while waiting for controller startup or IPC.

Before replacing files, print and document the broken-CLI fallback:

```sh
sudo /usr/bin/python3 -I -B /opt/sparehash/.update-backup/src/sparehash/__main__.py update --recover
```

This invokes the protected known-good application, not the failed candidate or
a separately maintained bootstrap updater. It must work with a broken/missing
live CLI/source. Without a pending journal it does not restore an old version.
At the next update, replace the obsolete backup before publication; no backup
generations, post-success selector or retention/rotation subsystem. Uninstall
removes only validated owned updater leftovers as well as current app files.

## 6. Four implementation milestones

Dependencies are simply **1 -> 2 -> 3 -> 4**. Use short-lived branches/PRs into
`main`; no long-lived development branch or separate approval ceremony per task.
Align obsolete v0.2 task/branch instructions in `AGENTS.md` with the approved plan
when implementation starts; retain its VM safety/evidence requirements.

### Milestone 1: Version and release identity

Deliver `--version`, authoritative version metadata, stamped source identity,
archive/signature contract and one local build/sign command. Likely files:
`__init__.py`, new `_build.py`, `__main__.py`, `pyproject.toml`, `uv.lock`,
`bootstrap.py`, a small `app_release.py` trust helper and `qa/build_release.py`.

Acceptance: official artifacts and installed no-Git trees agree on version/full
commit; rootless/offline reporting needs no private data; owner-confirmed key pins
are separate from XMRig; the builder produces verifiable signatures in one command.

### Milestone 2: Update discovery

Deliver `update --check`, release selection, bounded downloads, local-input staging
and shared verification/extraction. Likely files: `__main__.py`, small
`cli_update.py`/`app_release.py`, narrowly reused transport/GPG helpers and focused
CLI/release tests. No application/service changes yet; do not claim plain update
works before milestone 3.

Acceptance: experimental GitHub releases are discoverable, version comparison and
network errors are correct, check is unprivileged/read-only, and untrusted or
damaged online/local inputs fail before code execution or installation changes.

### Milestone 3: Application updates and recovery

Rework `upgrade.py` around the transaction above, wire root update/from/recover,
and make necessary hold/startup/receipt/uninstall changes. Likely files:
`cli_update.py`, `upgrade.py`, `controller.py`, `controller_io.py`,
`lifecycle_lease.py`, `bootstrap.py`, `systemd.py`, manifest/receipt helpers and
`cli_uninstall.py`. Port useful existing transaction/preservation tests rather
than retain the legacy completion engine beside the new one.

Acceptance: source/live units/launcher and ownership update correctly; stopped and
running installations retain config/manual/startup state; ordinary failures restore
the old app; `--recover` works after interruption and through the known-good-copy
fallback. XMRig stays untouched. No post-success rollback interface is implemented.

### Milestone 4: Cleanup, validation and release

Remove superseded updater code, fix essential docs, run focused real-distro
acceptance and CI, then prepare v0.3.0 artifacts/release notes. Reuse the existing
VM smoke fixtures, adding at most one focused update driver if needed.

Acceptance: the seven scenarios below pass on both disposable baselines; relevant
host-safe tests, typing/lint/docs/build checks and CI pass; installation/update/
recovery instructions match implemented behaviour. Publish only with owner approval.

## 7. Focused verification and stopping rule

Implementation uses `uv sync --locked` once, then `.venv/bin/python -m pytest`.
Read `docs/development/vm-testing.md` before integration. Run the following on
disposable **Bazzite and Ubuntu** VMs, reusing guests/scenario setup where practical:

1. Successful signed update with the controller running, including a source,
   deployed-unit and receipt change.
2. Successful update with the controller stopped; it remains stopped.
3. Config/credentials, manual pause and startup-state preservation. Include the
   important active-but-boot-disabled case; verify XMRig identity is unchanged.
4. Invalid/untrusted release rejected before live installation/service changes.
5. Interrupted publication followed by successful `--recover`; exercise a reboot
   and the direct known-good-copy command with the live CLI unavailable.
6. Signed candidate fails activation; previous version is automatically restored,
   verified and returned to prior activity without unwanted mining.
7. Successful receipt-aware uninstall after an update, including owned updater
   leftovers. No post-success rollback/reinstall matrix is required.

These are seven scenarios, not seven new VM provisioning runs per distro.
Use a signed local test successor; do not publish a fake release just for testing.
Unit tests additionally cover rootless version/check behaviour, signature tampering,
safe extraction, integer version selection, receipt additions/deletions, incomplete
journal inhibition, write failure, interrupted restoration and repeatable recovery.
Test representative important boundaries, not every permutation of every write.
Retain applicable controller/manifest/security regressions and run the host-safe
suite before release. No new gaming or CPU benchmarking unless that code changes.

Keep lab guest networking disabled, Bazzite SELinux enforcing, no real wallets,
public pools/mining or host storage attached; use the existing non-hashing miner
fixture. Collect actual results and discard guests. Ubuntu is conventional-family
evidence, not Mint-specific certification. Test keys stay in test fixtures/source,
not production bypass flags; also verify the final production-signed artifact.

Because the lab has no networking, use read-only real GitHub download/signature
verification outside the guest plus real offline updater/recovery inside each
guest; both paths share the tested pipeline. State that limitation honestly.
If true online end-to-end VM coverage is requested, seek an approved disposable
environment rather than changing lab policy. Lab failures are blockers to the
required VM checks, not permission to test destructively on the workstation.

**Stopping rule:** once updates work, essential safety boundaries hold, ordinary
failures recover, and the relevant VM checks and CI pass, v0.3.0 is ready for an
experimental release. Known serious security bugs block release; speculative
perfection, exhaustive fault matrices and unrelated validation do not.

## 8. Essential cleanup and documentation only

- Remove the superseded `migration.py` engine and unreachable upgrade/recover/
  completion/preservation plumbing in `install.py` after moving useful checks.
  Remove `complete-upgrade`/obsolete installer upgrade flags; no legacy adapters.
  Update their exclusive tests to exercise the new supported updater instead.
- Correct app/module/directory receipts and their action references as required
  for successful uninstall. Keep live schema parsers, controller safeguards,
  legacy removal hooks and `deployment_spec.py` unless the updater directly
  supersedes a piece. Leave unrelated `receipt_migration.py`/QA helper cleanup
  for later rather than expanding the release diff.
- Remove inconsistent current version metadata/messages and deferred-update claims.
  Do not implement deferred benchmark/config-edit workflows or refactor the CLI
  framework. Preserve XMRig verification and avoid accidental app/miner naming mixups.
- Update `README.md` quick start and CLI table, existing `docs/platforms.md`
  installation/removal guidance, `SECURITY.md`, `CONTRIBUTING.md`, `CHANGELOG.md`
  and one `docs/release-0.3.0.md`. Add one `docs/updating.md` for signature files,
  online/offline usage, preserved state and broken-CLI recovery. Configuration/CLI
  reference can stay in README for now; no documentation restructure is required.
- Keep historical docs where they are. Add a short historical notice/link only
  where an old page could mislead current installation/update instructions. Do
  not archive/rewrite the full history or clean unrelated recovery/benchmark tools.
- Adapt `qa/check_readme.py` only enough for new flags/docs. Correct touched live
  path inaccuracies, retain legal notices and empty runtime dependencies. No
  global lint, packaging or workflow reorganisation.

## 9. Lightweight release workflow

1. Develop on a short-lived branch, run relevant checks, open a PR and merge once
   CI passes. `main` remains the only maintained branch; no issue/proposal required
   for each ordinary fix.
2. Update the single version source and concise release notes in the release PR;
   tag the merged commit `vX.Y.Z`. Use the next patch version for fixes, never
   replace published tags/assets to change a release.
3. From that clean tag, one command such as
   `python3 qa/build_release.py --tag v0.3.0 --sign <fingerprint>` stamps, builds,
   signs and verifies the source archive and prepares existing package artifacts.
   Keep keys local. Do not add a signing service or CI secret-key setup.
4. Publish archive plus `.asc` and useful notes through a GitHub draft release,
   then make it public as an experimental pre-release once assets are complete.
   Use `gh` or the web UI; no promotion pipeline is necessary.
5. Initially, manually uninstall v0.2.1 and install v0.3.0 on the two owner machines.
   For subsequent releases, run check/update and inspect version/status. No
   automated workstation rollout or post-success rollback guarantee.

Keep existing CI; add only identity/artifact/signature-builder checks that catch
real release mistakes. Tag-triggered publishing automation, separate XMRig
updates, broad repo cleanup and more elaborate compatibility/recovery features
can follow if actual use demonstrates a need.
