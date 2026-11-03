# Upgrade status — deferred in experimental v0.2

> **Historical page.** For the supported fresh v0.3+ application updater and
> interrupted recovery, use [updating](updating.md). There is no v0.2.1 migration;
> preserve historical safety records and seek maintainer help.

There is **no supported in-place upgrade, rollback, reconfiguration or migration
completion workflow** in this release. Old instructions from experimental branches
must not be used as installation guidance.

Public `update`, `complete-upgrade`, `config edit`, `benchmark` and installer
`--upgrade`, `--recover`, `--complete-upgrade`, `--reconfigure` return exit 2 before
host inspection or workflow effects. No automatic updater or native packaging
channel is introduced. Internal transaction code and its safety regressions are
retained as research, not an alternate supported route.
Ordinary installation also refuses an existing protected CLI through a read-only
check before retirement clearing or capability detection; omitting the deferred
flags does not authorize in-place installation.

If an old experiment left a migration journal, private staging or update hold,
preserve it and seek maintainer assistance. Do not delete records, clear a lease,
or manufacture receipts to resume mining or force installation.

Fresh installs and explicitly consented reinstalls after completed removal are
the experimental path. See [platforms/removal](platforms.md), the [README](../README.md)
and [S3 lifecycle evidence](development/s3-lifecycle-reliability.md). This is not
a blanket promise that arbitrary old installations can be safely migrated.
