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
    """Extract every config key from the Config dataclass schema."""
    source = _read(CONFIG_PATH)
    tree = ast.parse(source, feature_version=(3, 11))
    
    all_dataclasses: dict[str, list[str]] = {}
    config_fields: list[tuple[str, str]] = []
    
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            fields: list[str] = []
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    fields.append(item.target.id)
            all_dataclasses[node.name] = fields
            
            if node.name == "Config":
                for item in node.body:
                    if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                        field_name = item.target.id
                        if isinstance(item.annotation, ast.Name):
                            type_name = item.annotation.id
                            config_fields.append((field_name, type_name))
    
    keys: list[str] = []
    for field_name, type_name in config_fields:
        if type_name in all_dataclasses:
            for subfield in all_dataclasses[type_name]:
                keys.append(f"{field_name}.{subfield}")
        else:
            keys.append(field_name)
    
    return sorted(keys)


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


def _negative_fixture(readme_text: str, pattern: str) -> str:
    count = readme_text.count(pattern)
    if count != 1:
        raise ValueError(
            f"negative self-test pattern {pattern!r} appears {count} times; expected 1"
        )
    return readme_text.replace(pattern, "REMOVED_PLACEHOLDER", 1)


def _test_negative_fixture() -> None:
    # Given one exact occurrence, when fixture creation runs, then it is removed.
    modified = _negative_fixture("donate_level = 1", "donate_level = 1")
    if "donate_level = 1" in modified:
        raise AssertionError("unique pattern was not removed")

    # Given no occurrence, when fixture creation runs, then it raises explicitly.
    try:
        _ = _negative_fixture("other text", "donate_level = 1")
    except ValueError:
        pass
    else:
        raise AssertionError("missing pattern did not fail")

    # Given duplicate occurrences, when fixture creation runs, then it raises explicitly.
    try:
        _ = _negative_fixture(
            "donate_level = 1\ndonate_level = 1", "donate_level = 1"
        )
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate pattern did not fail")


def _negative_self_test(commands: list[str], flags: list[str],
                        keys: list[str]) -> None:
    """Remove a unique inventory entry from a temp README copy and confirm detection."""
    _test_negative_fixture()
    readme_text = _read(README_PATH)
    test_key = "donate_level"
    test_pattern = f"{test_key} = 1"
    modified = _negative_fixture(readme_text, test_pattern)
    
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
                f"self-test FAILED: removing {test_pattern!r} was not detected",
                file=sys.stderr,
            )
            sys.exit(1)
        
        found = any(test_key in entry for entry in missing)
        if not found:
            print(
                f"self-test FAILED: missing entries do not name {test_key!r}",
                file=sys.stderr,
            )
            print(f"Missing entries: {missing}", file=sys.stderr)
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
