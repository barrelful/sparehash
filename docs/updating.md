# Application updates (experimental)

Updates support fresh v0.3+ installations, not migration from v0.2.1. They never
update XMRig, rewrite configuration/credentials/manual pause, or change boot
startup preferences. A running controller returns to active; a stopped controller
remains stopped. Mining re-evaluates current blockers rather than resuming blindly.

```sh
sparehash --version
sparehash update --check
sudo sparehash update
sudo sparehash update --from ./release.tar.gz
sudo sparehash update --recover
```

Check/from/recover are mutually exclusive. Check is rootless and read-only; an
available update still exits zero. Mutation requires root (exit 2 otherwise).
Network, authentication and update failures exit 1, including when the previous
application was automatically restored successfully. No downgrade, force,
unsigned-input or post-success rollback option exists.

Copy both `release.tar.gz` and `release.tar.gz.asc` for fully offline updates.
Renaming requires renaming both. Originals are never deleted. Online discovery
includes numeric GitHub pre-releases and requires the source/signature pair;
availability does not authenticate an archive. Both update paths use the same
staged detached-GPG verification before extraction or candidate execution.

**Production prerequisite:** the owner must independently confirm/provide the
maintainer's full primary fingerprint and public certificate. Until pinned in
the installed application, updates deliberately refuse all archives. A candidate
key, test fixture or signed Git tag alone does not establish archive trust.

## Failure and interruption

Ordinary incomplete-publication/activation failures restore the protected previous
app, launcher, both units and app receipts automatically. An interrupted process
retains `/var/lib/sparehash/update.json` and its matching lifecycle hold. Any
journal, including a corrupt one or one without a hold, inhibits mining across
restart while status IPC remains available. Do not delete journals or holds.

Run `sudo sparehash update --recover`. With a broken/missing installed CLI, use
the known-good protected source copy printed before publication:

```sh
sudo /usr/bin/python3 -I -B /opt/sparehash/.update-backup/src/sparehash/__main__.py update --recover
```

Recovery restores an incomplete installation, or finishes finalisation of an
already committed outcome. Without a pending journal it does not restore an old
version. A retained backup is recovery material, not a version selector. If empty
proof, integrity or restoration cannot be established, inhibition/evidence remain
and the concrete failure is reported; there is no force-clear option. Preserve
foreign contents and ask the maintainer rather than removing safety records.
