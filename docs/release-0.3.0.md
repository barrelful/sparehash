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

Milestone-4 runtime source `5b24684d156453e5701386197ea8e74ddaae420f` passed all
seven scenarios on Bazzite, including controlled power-cycle and broken-CLI
recovery, with SELinux Enforcing and no captured AVCs. All test guests were
discarded. The 2026-11-03 follow-up adds the owner-confirmed public signer pin and
exact-commit release preparation; its runtime source is
`095b17d7986702b37bd76193ae4ed83dd9671c45` (2026-11-03 Ubuntu attempt, not a pass).

**Prior final Ubuntu acceptance was blocked, not passed.** Earlier product source and
an intermediate M4 source passed all seven scenarios on Ubuntu; the latter also
passed actual fresh-reinstall/receipt-reset/uninstall. Two final hardened-source
attempts at `5b24684d156453e5701386197ea8e74ddaae420f` lost the QEMU guest-agent
transport, and one controlled power-cycle still had no connected agent. Both
instances were discarded. This is inconclusive infrastructure evidence, not a
diagnosed product failure or permission to use mocks/workstation tests. The owner
authorized a lab repair: separate authenticated-serial Ubuntu v2 administration
now works independently of QGA. An initial follow-up run exposed an actual PID 1
systemd segfault/kernel panic during scenario 6; diagnostic evidence was preserved.
Reboot recovery and a repeated signed activation-failure restoration succeeded,
but that retry does not explain the original OS crash. The fresh attempt also
panicked, before its first signed update. A native fwupd-refresh/daemon-reload
reproducer panicked with SpareHash stopped; a separate **no-SpareHash** overlay
also panicked after authenticated offline updates to systemd `255.4-1ubuntu8.18`
and kernel `6.8.0-147-generic`. The bounded repair attempt therefore did not resolve
acceptance. No new product defect is identified, and no full Ubuntu pass is
claimed. All owned guests are discarded; original/v2 baseline hashes were
revalidated. Isolation and policy were not weakened. A pristine official-image
OS reproduction/debugging step is proposed before another product acceptance run.

Guest networking stays disabled: real read-only GitHub discovery is checked on
the development host, while the shared signed offline pipeline/update/recovery is
exercised in VMs. True online end-to-end guest coverage is not claimed. Existing
published GitHub application archives have no detached signatures, so no real
published production-signed successor can currently be downloaded. A local real
Nitrokey-signed preparation archive **has** now been verified as recorded below;
this is separate from VM test signatures. Gaming/hardware limitations remain unchanged.

## Verified local production preparation (2026-11-03)

PR [#8](https://github.com/barrelful/sparehash/pull/8) is squash-merged at
`9de1e004594ff07dd856cfdaa837981e157f8dab`; all five PR and main CI checks passed.
That exact clean commit was prepared with `qa/build_release.py --commit` (no tag)
and signed by the existing Nitrokey's signing subkey. The complete source archive
signature verifies with the **bundled application defaults**, independently from
both the repository and the extracted archive. Source/wheel/sdist stamps, source
contents, GPL/attribution and zero runtime dependencies were checked. No PIN or
private key was exported/logged and no hardware interaction remains outstanding.

Artifacts are local/gitignored under
`.omo/evidence/v030/milestone-4/nitrokey-followup/production-prepared-9de1e00/`.
The source archive SHA-256 is
`7e78dcb76ef4f660a0b44a877c78b50ee0d12516c7e0f755390ed4166f926c4d`;
its `.asc` SHA-256 is
`e7de399e5965b0046e9be4b6c071abd1b54fa9b8f1b77e9d0c1c8ce2496c1662`.
Full wheel/sdist hashes and a read-only verification command are in
[`v030-progress.md`](development/v030-progress.md#production-signing-outcome-2026-11-03).
Only the source archive is detached-signed; wheel/sdist are stamped packages.
Later handover changes are documentation only. **Ubuntu acceptance remains blocked;
this authentic artifact is not an approved release.**

## Owner prerequisites and release steps

1. The owner-confirmed existing Nitrokey primary certificate is now pinned in
   `sparehash/app_release.py`: **`23E9CE0E1C95324190571379766F2AE440E1019A`**,
   separate from XMRig. Its signing subkey was independently matched to the card;
   only public material is bundled. Actual local production-signature verification
   is complete above; missing/incorrect trust remains a hard refusal, never a test bypass.
2. Ubuntu acceptance remains blocked as above; resolve the native OS failure
   before release signoff. The focused public-key/build-preparation PR may merge
   after passing CI because the native failure reproduces without the product;
   that merge does not approve a release. Unit tests and an authentic production
   signature alone do not satisfy real-distro acceptance.
3. With owner approval, tag the merged clean commit `v0.3.0`; no tag was created
   by implementation. Run `python3 qa/build_release.py --tag v0.3.0 --sign FINGERPRINT`.
   Inspect archive/wheel/sdist identity, signatures, dependencies and legal notices.
   Pre-tag preparation instead uses `--commit FULL_SOURCE_SHA`; it creates no tag,
   prepares packages before normal local pinentry/touch, and verifies signatures
   against bundled installed trust. Never supply/log a PIN or export private keys.
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
