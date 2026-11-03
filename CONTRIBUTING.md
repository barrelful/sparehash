# Contributing to SpareHash

Thank you for considering a contribution. SpareHash is licensed under the
GNU General Public License v3.0 (GPLv3). By contributing, you agree to license
your work under the same license.

## Development setup

SpareHash uses only the Python standard library at runtime and requires
Python 3.11 or newer.

For development, we recommend [uv](https://docs.astral.sh/uv/):

```bash
uv sync --locked
.venv/bin/python -m pytest tests/ -q
```

You can also use the system Python directly:

```bash
python3 -m pytest tests/ -q
```

## Running checks

Unit tests:

```bash
.venv/bin/python -m pytest tests/ -q
```

README inventory checker:

```bash
.venv/bin/python qa/check_readme.py
```

Type checking (required; CI runs mypy):

```bash
.venv/bin/mypy sparehash
```

Build the wheel and source distribution (also required in CI):

```bash
uv build
```

## Architecture invariants

Any change must preserve these safety properties. If your change relaxes one of
them, it must be discussed explicitly in the issue or pull request.

- **Fail-closed.** Unknown or stale safety state must inhibit mining.
- **Controller authority.** Every miner start must be freshly authorized by the
  running controller.
- **Unprivileged miner.** XMRig must never run as root.
- **Controller loss stops mining.** `BindsTo=` and cleanup paths must keep this
  true.
- **No MSR/kernel/firewall changes in v0.2.**
- **Localhost-only XMRig API.**
- **Receipt-authoritative install/uninstall.** Files, units, and identities are
  removed only if the install ledger records them as created by SpareHash.

## Code conventions

- Python 3.11+ syntax, type hints, and `from __future__ import annotations`.
- Prefer dataclasses and frozen value objects for configuration and state.
- Use explicit test seams (protocols, injected functions) for anything that
  touches the filesystem, subprocesses, or network.
- No shell=True subprocess calls.
- No type-error suppression (`as any`, `@ts-ignore`, etc.).
- No untrusted PATH resolution in privileged code; use `FIXED_PATH`.
- File operations that run as root must use descriptor-relative paths
  (`dir_fd`, `O_NOFOLLOW`) where possible.

## Testing

- Add or update unit tests for any code change.
- Destructive integration tests belong in `qa/` and must run only in a
  disposable VM. Do not add destructive host mutation to the unit-test suite.
- A frozen release candidate requires `qa/qa_release_smoke.sh` in an explicitly
  approved disposable systemd VM, with `--yes` and a verified source SHA-256.
  See `qa/README.md`; the fixture never hashes or connects to a pool. Unit tests
  exercise its refusal and digest contracts, not VM success.
- If you add a new CLI command or config key, update the README and run
  `qa/check_readme.py`.

### VM lab workflow

The VM lab (`docs/development/vm-testing.md` when available) provides
disposable-VM acceptance procedures for cross-distribution testing. Unit tests
exercise algorithms only; real systemd/cgroup/SELinux and OS persistence require
VMs or boot media. Before submitting changes that affect installation,
lifecycle, gaming integration, or platform support, verify behavior in a
disposable VM matching the target distribution. See `docs/platforms.md` for the
support matrix and `qa/README.md` for destructive-suite invocation.

## Pull request workflow

1. Discuss architectural/security scope changes; ordinary fixes need no issue ceremony.
2. Create a short-lived feature branch (fork when needed), targeting `main`.
3. Keep commits atomic and write clear commit messages.
4. Ensure unit tests, the README checker, mypy, and the package build pass.
5. Update `CHANGELOG.md` under the unreleased section.
6. Submit a pull request describing what changed and why; squash merge only after CI passes.

## Experimental release preparation

`sparehash/__init__.py` is the sole version source; Hatch metadata derives from it.
From a clean exact release tag, the maintainer uses the existing Nitrokey only:

```sh
python3 qa/build_release.py --tag v0.3.0 --sign 23E9CE0E1C95324190571379766F2AE440E1019A
```

Before tag approval, use `--commit FULL_SOURCE_SHA` instead of `--tag`; it requires
the full exact clean HEAD commit and creates no tag. The builder prepares stamped
source/wheel/sdist before hardware interaction, detached-signs the complete source
archive, then verifies against the bundled installed application trust (not a key
exported from the signing keyring). Only the pinned primary fingerprint is accepted.
Use normal local pinentry/touch for the card; never put a PIN in chat, command
arguments, environment variables or evidence. If signing cannot complete, the
prepared archive/packages remain available for an explicit local signing command.
Publish the source archive plus `.asc` together. Do not add a CI private key,
keyserver lookup, unsigned bypass or candidate-provided trust. Verify production
signatures before publication. Use a new patch version rather
than replacing published assets/tags. See [release preparation](docs/release-0.3.0.md).

Application acceptance uses `qa/update_acceptance.py` in the two approved
networkless disposable baselines, with isolated test-key source copies, the
non-hashing miner, a power-cycle between phases, and captured evidence. No test
trust/fault switch belongs in production code. Read
`docs/development/vm-testing.md` before any lifecycle test. No workstation rollout
or production signing is implied by passing tests.

## Questions?

Open a discussion issue. For security questions, see `SECURITY.md`.
