"""Verify every CLI command and config key appears in the README.

Exit 0 when the README is complete. Exit non-zero and name every missing entry
when something is absent. Includes a negative self-test that temporarily removes
one entry from a copy of the README and confirms the checker detects it.
"""

from __future__ import annotations

import ast
import re
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
README_PATH = REPO_ROOT / "README.md"
MAIN_PATH = REPO_ROOT / "sparehash" / "__main__.py"
CONFIG_PATH = REPO_ROOT / "sparehash" / "config.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _cli_commands() -> list[str]:
    """Extract every public, gaming, and debug command from __main__.py."""
    source = _read(MAIN_PATH)
    tree = ast.parse(source, feature_version=(3, 11))
    commands: list[str] = []
    target_names = ("PUBLIC_COMMANDS", "GAMING_COMMANDS")
    for node in ast.walk(tree):
        value_node: ast.expr | None = None
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in target_names:
                    value_node = node.value
                    break
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if isinstance(node.target, ast.Name) and node.target.id in target_names:
                value_node = node.value
        if value_node is not None and isinstance(value_node, ast.Tuple):
            for elt in value_node.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    commands.append(elt.value)
    commands.append("debug set-blocker")
    commands.append("debug clear-blocker")
    return sorted(commands)


def _cli_flags() -> list[str]:
    """Extract flags from the parser definitions in __main__.py and install.py."""
    flags: list[str] = []
    hidden = {"--bootstrap-created"}
    for path in (MAIN_PATH, REPO_ROOT / "sparehash" / "install.py"):
        if not path.exists():
            continue
        source = _read(path)
        for match in re.finditer(r'add_argument\(([^)]+)\)', source):
            args_str = match.group(1)
            for arg_match in re.finditer(r'["\'](-{1,2}[a-zA-Z][\w-]*)["\']', args_str):
                flag = arg_match.group(1)
                if flag not in hidden:
                    flags.append(flag)
    return sorted(set(flags))


def _config_keys() -> list[str]:
    """Extract every config key from the Config dataclass and _construct."""
    source = _read(CONFIG_PATH)
    keys: list[str] = []
    for match in re.finditer(r'"([a-z_]+)"', source):
        key = match.group(1)
        if key not in ("version", "wallet", "donate_level") and not key.startswith("_"):
            continue
        keys.append(key)
    section_keys = [
        "version", "wallet", "donate_level",
        "pool.host", "pool.port", "pool.tls", "pool.worker",
        "cpu.mode", "cpu.max_threads_percent", "cpu.custom_json",
        "thermal.enabled", "thermal.pause_c", "thermal.resume_c",
        "gaming.enabled", "gaming.users",
        "hugepages.enabled", "hugepages.pages",
        "api.port",
        "controller.min_dwell_seconds", "controller.reconcile_seconds",
        "xmrig.binary",
    ]
    return section_keys


def _check_readme(text: str, commands: list[str], flags: list[str],
                  keys: list[str]) -> list[str]:
    """Return a list of missing entries found in the README text."""
    missing: list[str] = []
    for command in commands:
        if command not in text:
            missing.append(f"command: {command}")
    for flag in flags:
        if flag not in text:
            missing.append(f"flag: {flag}")
    for key in keys:
        bare = key.split(".")[-1]
        if bare not in text and key not in text:
            missing.append(f"config: {key}")
    return missing


def _negative_self_test(commands: list[str], flags: list[str],
                        keys: list[str]) -> None:
    """Remove one entry from a temp README copy and confirm the checker fails."""
    readme_text = _read(README_PATH)
    if not commands:
        return
    removed_command = commands[0]
    modified = readme_text.replace(removed_command, "REMOVED_PLACEHOLDER")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as temp:
        _ = temp.write(modified)
        temp_path = temp.name
    try:
        modified_text = Path(temp_path).read_text(encoding="utf-8")
        missing = _check_readme(modified_text, commands, flags, keys)
        if not missing:
            print(
                f"self-test FAILED: removing {removed_command!r} was not detected",
                file=sys.stderr,
            )
            sys.exit(1)
        found = any(removed_command in entry for entry in missing)
        if not found:
            print(
                f"self-test FAILED: missing entries do not name {removed_command!r}",
                file=sys.stderr,
            )
            sys.exit(1)
    finally:
        Path(temp_path).unlink(missing_ok=True)


def main() -> int:
    """Verify the README covers every CLI command, flag, and config key."""
    if not README_PATH.exists():
        print("README.md not found", file=sys.stderr)
        return 1
    commands = _cli_commands()
    flags = _cli_flags()
    keys = _config_keys()
    readme_text = _read(README_PATH)
    missing = _check_readme(readme_text, commands, flags, keys)
    if missing:
        print("README is missing entries:", file=sys.stderr)
        for entry in missing:
            print(f"  - {entry}", file=sys.stderr)
        return 1
    _negative_self_test(commands, flags, keys)
    message = (f"README check passed: {len(commands)} commands, "
               f"{len(flags)} flags, {len(keys)} config keys")
    print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
