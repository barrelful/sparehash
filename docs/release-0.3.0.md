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

Final frozen runtime source `5b24684d156453e5701386197ea8e74ddaae420f` passed all
seven scenarios on Bazzite, including controlled power-cycle and broken-CLI
recovery, with SELinux Enforcing and no captured AVCs. All test guests were
discarded. Subsequent handover changes are documentation only.

**Final Ubuntu acceptance is blocked, not passed.** Earlier product source and
an intermediate M4 source passed all seven scenarios on Ubuntu; the latter also
passed actual fresh-reinstall/receipt-reset/uninstall. Two final hardened-source
attempts at `5b24684d156453e5701386197ea8e74ddaae420f` lost the QEMU guest-agent
transport, and one controlled power-cycle still had no connected agent. Both
instances were discarded. This is inconclusive infrastructure evidence, not a
diagnosed product failure or permission to use mocks/workstation tests. Repair
and revalidate that lab path (or approve another disposable Ubuntu environment),
then repeat all seven final-source scenarios before release signoff.

Guest networking stays disabled: real read-only GitHub discovery is checked on
the development host, while the shared signed offline pipeline/update/recovery is
exercised in VMs. True online end-to-end guest coverage is not claimed. Existing
published GitHub application archives have no detached signatures, so no real
production-signed successor can currently be verified. Test signatures are not
production release evidence. Gaming/hardware limitations remain unchanged.

## Owner prerequisites and release steps

1. Independently confirm/provide the maintainer public certificate and **full
   primary fingerprint**, separate from XMRig's signer. Pin them in
   `sparehash/app_release.py`; never substitute test trust. Confirm key availability
   before signing. Empty production trust deliberately refuses updates.
2. Resolve the Ubuntu lab blocker above and repeat final-source acceptance.
   Review/merge the key pin and release changes with passing CI. Re-run relevant
   acceptance against the final source and verify the final production-signed
   artifact. Unit tests alone do not satisfy real-distro acceptance.
3. With owner approval, tag the merged clean commit `v0.3.0`; no tag was created
   by implementation. Run `python3 qa/build_release.py --tag v0.3.0 --sign FINGERPRINT`.
   Inspect archive/wheel/sdist identity, signatures, dependencies and legal notices.
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
