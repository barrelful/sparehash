# v0.3.0 experimental release preparation

**Not yet tagged or published.** This is the application updater release, not a
deployment framework. Runtime remains standard-library-only, Python >=3.11.

## Delivered scope

- `sparehash --version` reports one authoritative version/full stamped commit
  without root, network, Git, host detection or private state.
- `update --check` discovers numeric published releases including GitHub-marked
  pre-releases, requiring the complete-source archive/signature asset pair.
- Root `update` and offline `update --from ARCHIVE` authenticate staged bytes with
  isolated GPG and installed signer trust before safe extraction/candidate code.
- One durable `update.json`, one previous-app backup, source/launcher/both-unit
  publication and app receipt refresh. Config, credentials, manual pause, boot
  preferences, XMRig/current, accounts and gaming hooks are not changed.
- Ordinary failures restore automatically. `update --recover` restores incomplete
  publication after interruption/reboot, including via the protected backup CLI.
  Committed outcomes only finalise; no post-success rollback interface exists.
- Superseded migration/completion engine and obsolete installer upgrade flags
  removed. See [updating](updating.md) for commands and broken-CLI recovery.

## Validation and limitations

Current test/PR status and exact next action are maintained in
[`development/v030-progress.md`](development/v030-progress.md). The focused VM
driver exercises seven scenarios on the approved Ubuntu/Bazzite baselines with
non-hashing synthetic XMRig, test-only signer/source fixtures, no guest networking,
and Bazzite SELinux Enforcing. Ubuntu is conventional-family evidence, not Mint
certification. No real wallets, public pools, mining or host storage are used.

Milestone-4 runtime source `5b24684d156453e5701386197ea8e74ddaae420f` passed
all seven scenarios on Bazzite. The final Ubuntu environment was replaced with
`ubuntu-noble-20261103-v3`, built from a newly downloaded, signature-verified
official Ubuntu 24.04 image (`04e8f2461a16a44e78a370284471f29616cdd9baf1893df25807b71f985b6c94`).
Its CPU model is pinned to `EPYC-Rome`, with unchanged 4-vCPU/6-GiB/40-GiB limits,
no NIC, no host share and authenticated serial administration. It passed repeated
cold boots and native daemon-reload/fwupd-refresh stability checks before testing.

All seven final Ubuntu scenarios passed on a fresh clone on 2026-11-03, including
forced power-off publication recovery, broken-live-CLI backup recovery, automatic
restoration after activation failure and receipt-aware uninstall. Test fixtures
used test-only signer trust; the product runtime source is
`095b17d7986702b37bd76193ae4ed83dd9671c45`, which is byte-identical to current
`main` except documentation and the test driver's fixture-marker cleanup. Guest
networking stayed disabled, Bazzite remained Enforcing with no AVCs, and all
disposable guests were discarded. **Ubuntu acceptance is no longer blocked.**

## Owner prerequisites and release steps

1. The owner-confirmed existing Nitrokey primary certificate is now pinned in
   `sparehash/app_release.py`: **`23E9CE0E1C95324190571379766F2AE440E1019A`**,
   separate from XMRig. Its signing subkey was independently matched to the card;
   only public material is bundled. Actual local production-signature verification
   is complete above; missing/incorrect trust remains a hard refusal, never a test bypass.
2. Ubuntu acceptance is complete on the replacement v3 baseline. Review the
   recorded evidence and final runtime/source identity; no native OS blocker
   remains. Unit tests and an authentic production signature alone were not used
   as a substitute for that real-distro acceptance.
3. With owner approval, tag the merged clean commit `v0.3.0`; no tag was created
   by implementation. Run `python3 qa/build_release.py --tag v0.3.0 --sign FINGERPRINT`.
   Inspect archive/wheel/sdist identity, signatures, dependencies and legal notices.
   Pre-tag preparation instead uses `--commit FULL_SOURCE_SHA`; it creates no tag,
   prepares packages before normal local pinentry/touch, and verifies signatures
   against bundled installed trust. Never supply/log a PIN or export private keys.
   The earlier `9de1e00` preparation proves production signing but predates final
   release documentation; rebuild from the approved tag before publication.
4. Create a GitHub **draft** release; attach `sparehash-0.3.0-source.tar.gz` and
   `sparehash-0.3.0-source.tar.gz.asc` together, plus useful optional checksums and
   notes. Publish only after owner approval as an experimental pre-release.
5. Owner-supervised only: receipt-aware removal of v0.2.1 and fresh authenticated
   v0.3.0 installation on each real machine. No migration/automated rollout. From
   v0.3 onward use signed check/update and inspect version/status. Preserve safety
   records on any failure and use documented recovery, never force-clear.

Do not replace published tags/assets to fix a release; use the next patch version.
Separate XMRig updates, benchmark/config-edit workflows, native packaging and
general rollback/version management remain out of scope.
