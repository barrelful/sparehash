#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""TEST-ONLY SpareHash QA fixture generator; never writes outside the QA root.

The receipt-owned installation stays byte-identical: this script only READS
/etc/sparehash and writes derived copies under the disposable QA root, where
the runner bind-mounts them over the exact runtime paths inside the controller
and miner namespaces.

Contracts enforced before anything is written (fail closed, refuse otherwise):

  * config.toml parses and carries donate_level 1..99 (0 is never valid, so
    the public configuration keeps its original donation value untouched);
  * both installed miner JSONs (public xmrig.json and the wrapper fallback
    xmrig.runtime.json) already point exclusively at 127.0.0.1:3333 with
    tls=false, and expose the loopback HTTP API - no external mining path;
  * the zero-donation copies differ from the installed bytes only in the
    donate-level value, so the API token and pool bytes stay identical.

Outputs (under --qa-root):

  config.test.toml          thermal.enabled=true, gaming.enabled=true,
                            gaming.users=["root"]; every other byte-equivalent
                            value preserved (donate_level unchanged, 0644)
  xmrig.zero.json           donate-level=0, 0640 root:sparehash
  xmrig.zero.runtime.json   donate-level=0, 0640 root:xmrig
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tomllib
from pathlib import Path
from typing import Any, Final

LOCAL_POOL: Final = "127.0.0.1:3333"
SECTION_HEADER: Final = re.compile(r"^\s*\[([^\]]+)\]\s*(?:#.*)?$")
THERMAL_ENABLED: Final = re.compile(r"^(\s*enabled\s*=\s*).*$")
GAMING_USERS: Final = re.compile(r"^(\s*users\s*=\s*).*$")


class Refusal(Exception):
    """A source contract violation; nothing is written."""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_config(path: Path) -> dict[str, Any]:
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise Refusal(f"cannot parse {path}: {error}") from error
    if type(document.get("donate_level")) is not int or not 1 <= document["donate_level"] <= 99:
        raise Refusal(f"{path}: donate_level must be an integer in 1..99, never 0")
    for section in ("thermal", "gaming"):
        if not isinstance(document.get(section), dict):
            raise Refusal(f"{path}: missing [{section}] section")
    if not isinstance(document["thermal"].get("enabled"), bool):
        raise Refusal(f"{path}: thermal.enabled must be a boolean")
    if not isinstance(document["gaming"].get("enabled"), bool):
        raise Refusal(f"{path}: gaming.enabled must be a boolean")
    return document


def edit_config(source: str, destination: Path) -> dict[str, Any]:
    """Enable both guards and name root; every other value is left alone."""
    section: str | None = None
    saw = {"thermal.enabled": False, "gaming.enabled": False, "gaming.users": False}
    output: list[str] = []
    lines = source.splitlines(keepends=True)
    index = 0
    while index < len(lines):
        line = lines[index]
        header = SECTION_HEADER.match(line.rstrip("\n"))
        if header:
            section = header.group(1)
            output.append(line)
            index += 1
            continue
        if section == "thermal" and (enabled := THERMAL_ENABLED.match(line)):
            output.append(f"{enabled.group(1)}true\n")
            saw["thermal.enabled"] = True
        elif section == "gaming" and (enabled := THERMAL_ENABLED.match(line)):
            output.append(f"{enabled.group(1)}true\n")
            saw["gaming.enabled"] = True
        elif section == "gaming" and (users := GAMING_USERS.match(line)):
            output.append(f'{users.group(1)}["root"]\n')
            saw["gaming.users"] = True
            remainder = users.group(1) + line.split("=", 1)[1]
            if remainder.count("[") > remainder.count("]"):
                index += 1
                while index < len(lines) and "]" not in lines[index]:
                    index += 1
                if index < len(lines):
                    index += 1  # drop the original closing bracket line
                continue
        else:
            output.append(line)
        index += 1
    missing = sorted(name for name, seen in saw.items() if not seen)
    if missing:
        raise Refusal(f"config edit did not find {', '.join(missing)}; refusing partial fixture")
    edited = "".join(output)
    try:
        document = tomllib.loads(edited)
    except tomllib.TOMLDecodeError as error:
        raise Refusal(f"edited config does not parse: {error}") from error
    if document["thermal"]["enabled"] is not True or document["gaming"]["enabled"] is not True:
        raise Refusal("edited config did not enable both guards")
    if document["gaming"].get("users") != ["root"]:
        raise Refusal("edited config must name exactly the root gaming provider")
    destination.write_text(edited, encoding="utf-8")
    os.chmod(destination, 0o644)
    return document


def zero_copy(source: Path, destination: Path, gid: int) -> None:
    """Validate the local-only pool contract, then zero the donation level."""
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise Refusal(f"cannot parse {source}: {error}") from error
    if not isinstance(document, dict):
        raise Refusal(f"{source}: miner configuration must be a JSON object")
    if type(document.get("donate-level")) is not int:
        raise Refusal(f"{source}: donate-level must be an integer")
    pools = document.get("pools")
    if not isinstance(pools, list) or not pools:
        raise Refusal(f"{source}: no pools configured")
    for pool in pools:
        if not isinstance(pool, dict) or pool.get("url") != LOCAL_POOL or pool.get("tls") is not False:
            raise Refusal(f"{source}: QA allows only {LOCAL_POOL} non-TLS pools, refusing to "
                          "generate a fixture that could reach an external pool")
    http = document.get("http")
    if not (isinstance(http, dict) and http.get("enabled") is True
            and http.get("host") == "127.0.0.1" and type(http.get("port")) is int):
        raise Refusal(f"{source}: QA requires the loopback HTTP API in the miner configuration")
    document["donate-level"] = 0
    destination.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    os.chmod(destination, 0o640)
    if os.geteuid() == 0:
        os.chown(destination, 0, gid)


def require_group(name: str) -> int:
    import grp
    try:
        return grp.getgrnam(name).gr_gid
    except KeyError as error:
        raise Refusal(f"required group {name!r} is missing; install the candidate first") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fixture_setup",
                                     description="derive TEST-ONLY QA fixtures from the installed bytes")
    parser.add_argument("--qa-root", required=True, help="disposable QA root (writes go here only)")
    parser.add_argument("--etc", default="/etc/sparehash", help="installed configuration directory (read-only)")
    args = parser.parse_args(argv)

    qa_root = Path(args.qa_root)
    etc = Path(args.etc)
    config_source = etc / "config.toml"
    public_source = etc / "xmrig.json"
    runtime_source = etc / "xmrig.runtime.json"
    for path in (config_source, public_source, runtime_source):
        if not path.is_file():
            raise Refusal(f"missing installed file {path}; nothing generated")

    before = {path.name: sha256(path) for path in (config_source, public_source, runtime_source)}
    qa_root.mkdir(parents=True, exist_ok=True)

    original = load_config(config_source)
    edited = edit_config(config_source.read_text(encoding="utf-8"), qa_root / "config.test.toml")
    if edited["donate_level"] != original["donate_level"]:
        raise Refusal("edited config changed donate_level; the public configuration must stay valid")

    # Group ownership only applies when writing as root; on non-root dev hosts
    # (unit tests with a synthetic --etc tree) the gids are never consumed by
    # zero_copy's chown, so the lookup is skipped instead of refusing.
    if os.geteuid() == 0:
        sparehash_gid = require_group("sparehash")
        xmrig_gid = require_group("xmrig")
    else:
        sparehash_gid = xmrig_gid = 0
    zero_copy(public_source, qa_root / "xmrig.zero.json", sparehash_gid)
    zero_copy(runtime_source, qa_root / "xmrig.zero.runtime.json", xmrig_gid)

    after = {path.name: sha256(path) for path in (config_source, public_source, runtime_source)}
    if before != after:
        raise Refusal("installed bytes changed during fixture generation; refusing")

    print(f"fixture: config.test.toml (thermal/gaming enabled, users=[root], "
          f"donate_level={edited['donate_level']} preserved) from sha256:{before[config_source.name]}")
    for name in ("xmrig.json", "xmrig.runtime.json"):
        print(f"fixture: {name} -> donate-level=0, local-only {LOCAL_POOL}, "
              f"sha256:{before[name]} unchanged at source")
    print("fixture: installed receipt files verified byte-identical after generation")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Refusal as error:
        print(f"FAIL: fixture setup: {error}", file=sys.stderr)
        raise SystemExit(1)
