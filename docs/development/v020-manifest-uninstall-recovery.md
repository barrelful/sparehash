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

**Current status:** reader regression coverage only. Recovery implementation,
VM reproduction, recovery acceptance and precise owner-run commands are blocked
on approval of the mechanism above. Do not run physical recovery based on this
document. There is no claim that the workstation is safe to recover yet.
