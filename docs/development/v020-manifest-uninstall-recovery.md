# Manifest-size uninstall defect and proposed partial-removal recovery

Base: `71a925675ec87bd7ddb554b29df307a9cdbc3f6d`. This is an investigation and
bounded-reader correction, **not an approved physical recovery procedure**.
No physical-host connection, installed CLI execution or mutation was performed.

## Reported incident and confirmed code defect

The owner reports an installation from `f348ed165358668d8101cd121cf07ee2f001295f`
with a valid 81,416-byte canonical manifest, retirement set, the `leaf-receipts`
phase completed, 58 recorded removals, installed Python source absent and the
launcher still present. Services were stopped after the owner's completed CPU
measurement. These are owner-provided facts, not observations made by this task.
The benchmark results and private configuration must remain untouched.

`ManifestStore.read()` accepts schema-2 documents up to 256 KiB, and
`parse_manifest()` enforces schema-specific caps (64 KiB legacy, 256 KiB extended).
But `PathTrust._metadata()` previously applied a 64 KiB cap to both the marker
and the extended manifest. `remove_owned_versions()` reads the canonical store,
then asks `PathTrust.manifest()` for independent protected metadata authority.
The latter returns None for the owner's manifest size, producing the reported
`valid protected marker and manifest are required` error **after** leaf removal.

The same bounded reader is used by `records()` and, transitively,
`authorized_current()` / `current_snapshot()`. This affects managed-current
detection, render snapshots and install/reuse authorization too. The canonical
store already uses the correct extended bound; schema parsing must not be
relaxed or changed to make a large legacy document valid. Other 64 KiB limits
found in migration/upgrade/progress code govern different journals, not manifest
reads, and are not raised.

## Bounded-reader correction

- Marker: separate **64-byte** bound for the fixed `/opt/xmrig` identity text.
- Manifest: shared `MAX_METADATA_BYTES_V2` (**256 KiB**) for protected reads;
  canonical parsing continues to enforce the correct cap for each schema.
- Preserve pinned descriptors, O_NOFOLLOW/O_NONBLOCK, regular-file checks,
  protected ownership/ancestors and unchanged-descriptor checks. Also require
  single-link metadata and a complete read matching the original fstat size,
  consistent with canonical-store protections.
- No installed files are edited, adopted or restored by this change. No new
  privileged command or protected staging path is implemented by it.

Private-fixture regressions use a genuine canonical schema-2 manifest larger
than 64 KiB and smaller than 256 KiB, with valid receipts and sequenced actions.
They check canonical/protected equality, recorded/current authorization, binary
snapshotting and version removal retaining all installer receipts. Reinstating
the old reader cap reproduces the exact refusal before version mutation.
Malformed, missing, oversized, writable, wrong-owner, linked, special and
path-substituted metadata still refuses. Oversized legacy-schema metadata does
not gain the extended budget. This is **not** a full interrupted-uninstall VM
reproduction or physical recovery acceptance.

Local validation of this reader-only correction: **57 focused tests + 21
subtests**, including the new large-manifest cases; full suite **1,236 tests +
619 subtests**. Production typing passed on all 56 modules. No installed runtime,
service, recovery entrypoint or staging directory was used by these tests.

## Independent recovery blocker: the installed cleanup launcher

The current `cli_uninstall.uninstall()` verifies the installed launcher against
its exact receipt, then executes that launcher with `__cleanup__`. Its required
Python modules have already been removed in the reported partial installation.
Consequently, executing a newer CLI from elsewhere still reaches the broken
installed launcher. Receipt equality establishes file identity, **not executable
dependency availability**. The manifest-size fix alone is not a recovery command.

Current cleanup also targets the corrected protected runtime children; the
reported `f348ed1` installation used the older exact runtime paths. Recovery
must validate and clean the appropriate old-layout artifacts without adopting
arbitrary paths, re-owning directories or confusing new source with the installed
implementation. Hugepage cleanup must retain boot/ledger/empty-cgroup protections
and must not silently ignore a warning.

## Proposed mechanism — OWNER APPROVAL REQUIRED before implementation

1. A **one-shot, root-only `recover-uninstall` entrypoint**, restricted to resuming
   the reported retired, leaf-removal-completed installation. No purge/force,
   source restoration, configuration editing, start or resume operation.
2. Execute reviewed recovery code from
   **`/run/sparehash/recovery/<full-reviewed-commit>/`**. Recovery directories are
   root:root **0700**, ordinary source/archive files root:root **0600**. Existing
   `/run/sparehash` metadata is checked and left unchanged. No SELinux relabel,
   custom policy, installed launcher replacement or new persistent privileged
   prefix. Refuse unsafe/pre-existing staging objects rather than overwriting.
3. Stage a deterministic archive made from an exact reviewed Git commit. Copy it
   using system utilities into protected storage and verify its independently
   published SHA-256 **there**, before extraction/execution; validate its finite
   member inventory/no links or traversal. Imports use isolated Python (`-I -B`)
   anchored only to the protected verified source. No root execution/import from
   a user-writable checkout, and no trust in archive-supplied expected hashes.
   Exact bootstrap commands/digests remain pending implementation and VM checks.
4. Before removal, require both services inactive, fresh empty-miner proof,
   protected canonical schema/marker, the exact surviving shim receipt, valid
   retirement/progress/lease evidence and original-source provenance. Missing
   leaves must be justified by the original receipts **and** journal entries;
   a completed phase must not authorize substituted/unrecorded files. Conflicting
   lifecycle holds or unverifiable state refuse. Keep starts excluded and serialize
   recovery against other installer/removal operations.
5. Retain the shim receipt check but **do not execute the broken shim**. Invoke
   cleanup from the verified protected recovery source, using only the exact
   old-layout runtime paths justified by the original installation's provenance.
   Preserve root/group/mode checks, empty proof, hugepage lease safety and foreign
   entries. Do not skip cleanup because source or a service is absent. Any refusal
   retains the retirement latch and progress for retry.
6. Resume the existing receipt/journal-authorized non-purge removal, preserving
   configuration, private credentials, unrelated state and the benchmark folder.
   Do not delete the already-recorded source again or change its receipts to fit
   new source. Retain retirement until a separate explicit reinstall.

This is a new privileged recovery mechanism and a new protected **subdirectory**,
even though it stays under an existing runtime hierarchy. It is a proposal only;
owner approval is required before code or owner-executable instructions are added.

## Required validation after approval

Read `vm-testing.md` first. Use a fresh disposable Bazzite clone first, then
Ubuntu, never the physical installation. Pin the original source and corrected
recovery source/archive hashes separately. Reproduce the failed uninstall with
a real, valid large manifest, real leaf-removal journal and absent source plus
surviving launcher—not merely a mock failure message.

Collect evidence for initial failure and each retry, original receipts and
remaining owned artifacts, actual shim import failure, retirement/progress,
inactive services and empty proof. Test resumed recovery, interrupted retry,
bad/substituted metadata/source/journal/shim, nonempty/unknown miner proof,
hugepage restoration/refusal, foreign preservation, SELinux Enforcing/no relevant
denials, and successful reinstall with `--no-start --no-enable`. No purge/force or
production credentials. Preserve historical failures under the private evidence
tree rather than rewriting them as passes.

**Status at the approval gate (historical):** reader regression coverage only. Recovery implementation,
VM reproduction, recovery acceptance and precise owner-run commands are blocked
on approval of the mechanism above. Do not run physical recovery based on this
document. There is no claim that the workstation is safe to recover yet.

## Approval and implementation scope

The owner subsequently approved this staging hierarchy and a narrowly scoped
one-shot recovery, explicitly prioritising simplicity. Implementation lives only
in `qa/`: no installed CLI dispatch, runtime module, automatic updater or new
persistent management interface is added. The builder consumes exact Git objects,
including the original f348ed1 per-file hashes, not arbitrary working-tree bytes.
The original installer left optional aggregate provenance/layout fields null;
recovery therefore requires the complete exact original 58-leaf receipt inventory
and hashes against independently bundled original Git source. If aggregate fields
are present they must also match. Null aggregate fields are not substituted with
checkout HEAD or treated as proof by themselves.

Read-only preflight may populate verified private staging, but performs no writes
to installed state, journals, service configuration or hugepages. A finite inode/
content deletion plan is written into the existing protected uninstall journal
before the first version-file deletion, permitting retries after interrupted
unlinks without adopting new or replaced files. Original receipts remain intact.
On completion a protected archive-bound completion receipt is fsynced in temporary
staging before clearing completed progress, so a subsequent installation cannot
inherit old completed-removal phases. Repeated apply becomes a no-op behind that
receipt, never a way to manage the new installation. The same verified source can
then run the existing installer explicitly with boot startup/controller startup
disabled. Finite verified staging disposal follows reinstall; foreign entries
refuse rather than being recursively erased. Configuration
and benchmark data are never used as removal targets. VM acceptance is pending;
no physical recovery commands are authorised by this implementation note alone.

### Retained first Bazzite attempt

At `7f83a3ec7b949760ca1fb7abe81304976c2b80ab`, the fresh original installation
succeeded with controller startup and boot startup disabled. Its fresh receipt
history was smaller than the owner's long-lived 81,416-byte document, and the QA
size assertion stopped the driver before uninstall. This is a fixture FAIL, not
recovery acceptance. Evidence was collected and the clone discarded. The revised
fixture grows the valid action history through actual recorded controller-disable
operations using the existing ledger, then marks the ledger ready. It does not
pad JSON, relax the parser or invent removal receipts. Historical evidence remains
under `.omo/evidence/v020/task-2/uninstall-recovery/round1/bazzite/`.

### Retained second Bazzite attempt

At `ec6110032545a256f700ab5e6ef671f4c8ef8ad1`, the original installation had
44,372 bytes of fresh metadata; 136 actual ledger-recorded disable operations
grew it to **81,611 bytes**. The original CLI then produced the real marker/
manifest rejection after deleting **58** source leaves and durably completing
`leaf-receipts`. Its surviving shim failed with missing modules. Verified-source
preflight and altered shim/manifest/journal refusal passed without installed-state
changes. The later nonempty-group QA fixture failed: a drop-in could not sever
the miner's controller dependency, and the attempted controller setup reproduced
old mixed-ownership runtime SELinux setattr denials on staged templates. This is
retained as FAIL, not recovery acceptance, and the clone was discarded.

The corrected fixture never starts either retired product unit: it uses a
separate sandboxed transient sleep unit to exercise actual populated-kernel-cgroup
hugepage refusal. Entry-point active/nonempty guards also have controlled unit
tests; the real manager-unavailable entry-point test runs in its own namespace.
Audit transport is retained in full; unrelated stock `tuned_t` capability denials
are distinguished from recovery/product/python denials rather than suppressed
or counted as evidence about this code. No SELinux policy or protection changes.

## Bazzite source-pinned acceptance — PASS

Fresh Bazzite 44 run `round3/bazzite` used recovery source
`2586b6a940663059b3f8922ffb2a1a2f93e24885` and independently pinned bundle SHA-256
`cf23b20b3c392cb14487290e2c2f4093296f7e4d92fa15c084f39b6438955ecb`.
Two independent local deterministic builds agreed with that bundle hash. The
original source archive was pinned to f348ed1, archive SHA-256
`562dd123448fd8fd64da9fbd133c744765f2037e689817f13bc9f29707e0ffbc`.
The recovery-source Git archive SHA-256 was
`4ad30b8d7622f7f72134ab2a6fc1d9aefacff0abd883463c2e0fe11db05bf45f`.

Guest exit **0**, `complete=true`, **11 recorded checks**, SELinux Enforcing,
`relevant_audit_denials=[]`. Full audit records retain two unrelated stock
`tuned_t` mac_admin denials; they were not generated by recovery or silenced.

- Real old large-manifest uninstall rejection at 81,611 bytes; 58 source removals,
  retired latch, completed leaf phase and surviving unusable launcher.
- Independently verified protected staging and read-only installed-state preflight.
- Changed shim, malformed manifest and malformed progress refused without deletion.
- Actual unavailable-manager namespace refused at the entrypoint.
- Actual populated transient cgroup refused hugepage cleanup using the existing
  kernel proof adapter; the entrypoint active/nonempty guard has separate unit
  coverage. The retired product units are not started for this fixture.
- External page-count change refused; an active owned 32-page lease was preserved
  until valid cleanup, then returned **32 → 0** with the ledger removed.
- Original-layout operation/socket cleanup actually ran from verified source.
- A helper was **SIGKILLed after a real version-file unlink and parent fsync**.
  A durable original-inode/content plan justified the missing file on retry.
  A newly introduced foreign file then caused refusal and was preserved; only
  the QA-owned injected collision was withdrawn before the successful retry.
- Successful resumed and repeated non-purge recovery preserved all configuration
  bytes, private token-bearing files, foreign runtime/state/config sentinels and a
  benchmark-result sentinel. Original retirement remained set until reinstall.
- Existing installer executed from the same verified protected source with
  `--clear-lifecycle-latch --no-start --no-enable` (plus the offline fixture's
  `--use-existing`). Controller/miner remained inactive and boot startup disabled.
- A repeated recovery invocation after reinstall was a no-op; verified finite
  staging disposal succeeded and left no recovery management interface or stale
  removal journal for the new installation.

The clone was collected, forced off and discarded. Ubuntu validation of the same
bundle is BLOCKED, not passed; see the disposition below.
No physical installation/service access or recovery was performed; SSH was limited
to the approved lab controller, its records and artifact storage.

The first Ubuntu clone (`round3/ubuntu`) failed at its first file transfer because
the guest agent was not ready. No product test ran, no guest evidence bundle was
available, and the clone was forced off/discarded. Transport failure records and
source pins are retained. The fresh retry uses bounded harmless guest-agent
readiness probes; neither the recovery bundle nor guest security policy changes.

The fresh Ubuntu retry (`round4/ubuntu`, same exact source and recovery bundle)
passed the agent readiness check and source transfers. The acceptance execution
then returned **139/SIGSEGV**; transport output reported core dumps involving the
Python command and its output-capture pipeline, followed by lost guest-agent access. No acceptance summary or
guest evidence archive could be collected. Transport output/pins remain retained;
the clone was forced off/discarded. The cause is **not diagnosed**, and this is
not counted as a recovery pass or asserted to be an application defect. Further
lab investigation is separate work rather than an expansion of this one-off tool.

After the server restart interrupted the first approval question, the owner
explicitly selected **“Deliver Bazzite-tested procedure”**, waiving the earlier
Ubuntu-acceptance prerequisite for this specific Bazzite recovery. This does not
authorize the assistant to perform physical recovery. The exact, gated owner
procedure is in [the one-off owner guide](v020-recovery-owner.md).

## Final disposition and limits

- **PASS:** Bazzite 44 real-distro one-off recovery, interruption/retry, original
  runtime cleanup, preservation, and explicit disabled-startup reinstall.
- **BLOCKED:** Ubuntu acceptance as described above; owner waived that gate for
  the Bazzite-specific procedure. No Ubuntu recovery-success claim.
- **Retained FAIL evidence:** the first small-history fixture and second retired-
  service failure injector; neither was promoted to acceptance.
- **Local:** 37 focused reader/recovery tests; full **1,252 passed + 619 subtests**;
  production typing on 56 modules, explicit typing on four QA modules, scoped
  Ruff, shell syntax, README inventory, offline build/artifact/dependency checks
  and exported-tree secrets scan passed. No new runtime module or dependency.
- All six owned clones were forced off and discarded. No physical installation,
  service, credential, benchmark result or security setting was accessed/changed.
  SSH access was confined to approved VM-lab administration and artifact transfer.

This utility accepts only the documented original-source/progress state. Physical
state can differ from the fixtures and then must refuse; do not weaken a check to
continue. Kernel page-count/boot ambiguity, damaged journals, altered original
receipts, or an interruption in an untested crash window may need separate
review. In particular, power loss during hugepage restoration can leave ambiguous
lease evidence that intentionally refuses automatic repair. Run no concurrent
installer, recovery or service operations. The test covers a real SIGKILL after
version unlink, not every possible power-loss or hardware-failure boundary.
The offline stand-in is not upstream XMRig or proof of real mining/pool access.
No physical recovery, merge, tag, RC or release-readiness declaration.

### Final owner bundle verification

The final wording correction removed the obsolete instruction to dispose of
staging before reinstall. Final recovery source is
**`21d4960d99813f2da4d1d3c923f9d373e2973158`**, bundle SHA-256
**`6a804efb61a0f10a95421d9badea04487fc6dbc51ecbe092527f1aa7f93aad30`**.
Two independent local builds agreed. A fresh Bazzite clone (`round5/bazzite`)
repeated the complete 11-check acceptance using this exact bundle: guest exit 0,
`complete=true`, Enforcing, zero relevant audit denials. It reproduced the valid
81,611-byte failure, SIGKILL-after-unlink/retry, 32 → 0 pages, preservation,
disabled-startup reinstall and final staging disposal. Its complete driver took
86.84 seconds inside the guest. Source Git archive SHA-256:
`19f933e7ead02dec58f728800b547ed615e475f8de50eea10e9d3aa5a4df2105`.
The original source/archive pin above is unchanged. The two unrelated stock
`tuned_t` audit denials remain retained separately. This final clone was collected,
forced off and discarded. The owner guide uses only these final tested pins;
the later documentation/CI delivery commit is not substituted into the bundle.
