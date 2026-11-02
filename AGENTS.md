# AGENTS.md

This file is read by development agents and automation before they touch
the SpareHash repository. It is intentionally short.

## Required reading before running integration tests

**Before running any integration, installation, upgrade, uninstallation
or lifecycle test, read `docs/development/vm-testing.md`.** It describes
the disposable-virtual-machine lab that is the standard integration
testing environment, including the launcher location, the two
available baselines, the operational constraints, and the host's
authoritative records.

Production behavior on the user's real workstation is never validated
by running these tests against the workstation directly. All destructive
tests must be run inside a disposable VM from the lab.

## General rules

- Do not commit private SSH keys, credentials, personal network
  addresses, production wallets, or other sensitive host details. The
  SpareHash repository is public.
- Do not commit the VM launcher or any host-internal lab controller
  into the SpareHash source tree. The launcher lives outside the
  repository, in a gitignored local directory.
- Do not add a runtime dependency on the lab. The lab is a developer
  convenience, not a runtime dependency.
- Follow the approved plan in `docs/development/v030-plan.md`; do not invent new
  distribution channels, new policy modules or new privileged paths
  without explicit owner approval.
- When a test cannot be satisfied with the available lab, surface that
  as a blocker to the owner with a proposed next step — do not silently
  relax safety or substitute mocks for real-distro evidence.

## Project conventions

- The runtime is standard-library-only; do not add a runtime dependency.
- The project targets Python >=3.11; CI covers 3.11–3.14. The test
  runner uses `uv sync --locked` once and then `.venv/bin/python -m
  pytest`.
- All destructive v0.3 tests use `.omo/evidence/v030/milestone-N/` for evidence.
  Evidence is gitignored; do not commit it.
- Use sequential short-lived `atlas/v030-mN-*` branches and PRs into `main`.
  The owner has authorized pushes and squash merges after passing CI for this
  plan. No final tag, published release or production signing without confirmed
  signing-key trust; never install/update/uninstall on the real workstation.
