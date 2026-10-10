# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Read-only setup boundary: injected prompts or TOML answers produce Config."""

from __future__ import annotations

import re
import socket
import tomllib
import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Final, TypeGuard, TypeVar

from .config import Config, ConfigError, Cpu, Gaming, Hugepages, Pool, RawConfig, validate
from .gaming import User, UserError
from .sysdetect import Capabilities
from .thermal import SensorPath, find_sensor
from .wallet import validate_address

T = TypeVar("T")
TomlValue = str | int | float | bool | date | datetime | Sequence["TomlValue"] | Mapping[str, "TomlValue"]
MSR_NOTICE: Final = "MSR optimisation is not supported in v1; SpareHash never writes CPU MSRs."
NUMA_NOTICE: Final = (
    "Automatic hugepages unavailable: require exactly one credibly detected online "
    "memory-bearing NUMA node; multiple or unknown nodes are unsupported. Mining still works."
)


class WizardError(ConfigError):
    """Actionable setup failure; field is usable by a future CLI adapter."""

    def __init__(self, field: str, reason: str) -> None:
        self.field: str = field
        self.reason: str = reason
        super().__init__(f"{field}: {reason}")


def _hostname(value: str) -> str:
    if len(value) > 253 or not value or any(
        re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) is None
        for label in value.split(".")
    ):
        raise WizardError("pool.host", "enter a hostname without scheme, port or path")
    return value


def _wallet(value: str) -> str:
    valid, reason = validate_address(value)
    if not valid:
        raise WizardError("wallet", reason)
    return value


def _users(value: str) -> tuple[str, ...]:
    users = tuple(value.split())
    if not users:
        raise WizardError("gaming.users", "specify at least one existing account or disable gaming")
    if any(re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_.-]*\$?", name) is None for name in users):
        raise WizardError("gaming.users", "enter valid local account names")
    try:
        for name in users:
            _ = User.from_name(name)
    except UserError as error:
        raise WizardError("gaming.users", "enter valid, resolvable local account names") from error
    return tuple(dict.fromkeys(users))


def _checked(cfg: Config, caps: Capabilities, sensor: SensorPath | None) -> Config:
    errors = validate(cfg)
    if errors:
        raise WizardError("config", "; ".join(errors))
    _ = _wallet(cfg.wallet)
    _ = _hostname(cfg.pool.host)
    if not cfg.pool.worker or any(ord(c) < 32 or ord(c) == 127 for c in cfg.pool.worker):
        raise WizardError("pool.worker", "enter a nonempty worker without control characters")
    if cfg.gaming.users or cfg.gaming.enabled:
        _ = _users(" ".join(cfg.gaming.users))
        if any(len(name.split()) != 1 or name.split()[0] != name for name in cfg.gaming.users):
            raise WizardError("gaming.users", "each entry must be one account name")
    if cfg.hugepages.enabled and (caps.numa_nodes is None or len(caps.numa_nodes) != 1):
        raise WizardError("hugepages.enabled", NUMA_NOTICE)
    if cfg.thermal.enabled and sensor is None:
        raise WizardError("thermal.enabled", "no credible CPU sensor; disable thermal guard")
    return cfg


def _config_shape(data: Mapping[str, TomlValue]) -> TypeGuard[RawConfig]:
    defaults = Config()
    raw = defaults.to_dict()
    schema: dict[str, TomlValue] = {
        "version": defaults.version, "wallet": defaults.wallet, "donate_level": defaults.donate_level,
        "pool": raw.get("pool", {}), "cpu": raw.get("cpu", {}), "thermal": raw.get("thermal", {}),
        "gaming": raw.get("gaming", {}), "hugepages": raw.get("hugepages", {}), "api": raw.get("api", {}),
        "controller": raw.get("controller", {}), "xmrig": raw.get("xmrig", {}),
    }
    for key, value in data.items():
        if key not in schema:
            raise WizardError(key, "unsupported configuration field")
        expected = schema[key]
        if type(value) is not type(expected):
            raise WizardError(key, f"must be {type(expected).__name__}")
        if isinstance(value, Mapping) and isinstance(expected, Mapping):
            for field, item in value.items():
                if field not in expected:
                    raise WizardError(f"{key}.{field}", "unsupported configuration field")
                if type(item) is not type(expected[field]):
                    raise WizardError(f"{key}.{field}", "wrong value type; use the Config TOML schema")
                if isinstance(item, Sequence) and not isinstance(item, str) and any(type(user) is not str for user in item):
                    raise WizardError(f"{key}.{field}", "must contain only account strings")
    return True


def load_answers(path: str | Path, caps: Capabilities, *,
                 sensor_probe: Callable[[], SensorPath | None] = find_sensor) -> Config:
    """Parse partial Config TOML with explicit required wallet/pool and safety gates."""
    try:
        with Path(path).open("rb") as source:
            parsed: dict[str, TomlValue] = tomllib.load(source)
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise WizardError("answers", "cannot read valid UTF-8 TOML; check file and syntax") from error
    if "msr" in parsed:
        msr = parsed["msr"]
        if not isinstance(msr, Mapping) or set(msr) != {"enabled"} or msr["enabled"] is not False:
            raise WizardError("msr", "only disabled legacy MSR settings are accepted; MSR unavailable in v1")
        del parsed["msr"]
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", UserWarning)
            if not _config_shape(parsed):
                raise WizardError("answers", "invalid configuration shape")
            cfg = Config.from_dict(parsed)
    except (ConfigError, UserWarning) as error:
        raise WizardError("answers", str(error)) from error
    return _checked(cfg, caps, sensor_probe())


@dataclass(frozen=True, slots=True)
class _Prompts:
    input_fn: Callable[[str], str]
    output_fn: Callable[[str], None]

    def ask(self, prompt: str, parse: Callable[[str], T]) -> T:
        """Retry only input errors, never read uncontrolled stdin."""
        while True:
            try:
                return parse(self.input_fn(prompt).strip())
            except WizardError as error:
                self.output_fn(str(error))

    def integer(self, prompt: str, bounds: tuple[int, int, int]) -> int:
        low, high, default = bounds

        def parse(value: str) -> int:
            try:
                number = int(value) if value else default
            except ValueError as error:
                raise WizardError("integer", f"enter an integer between {low} and {high}") from error
            if not low <= number <= high:
                raise WizardError("integer", f"enter an integer between {low} and {high}")
            return number

        return self.ask(prompt, parse)

    def boolean(self, prompt: str, default: bool = False) -> bool:
        def parse(value: str) -> bool:
            if not value:
                return default
            choices = {"y": True, "yes": True, "n": False, "no": False}
            if value.lower() not in choices:
                raise WizardError("choice", "answer yes or no")
            return choices[value.lower()]

        return self.ask(f"{prompt} {'[Y/n]' if default else '[y/N]'}: ", parse)


def run_interactive(caps: Capabilities, input_fn: Callable[[str], str],
                    output_fn: Callable[[str], None], username: str | None = None, *,
                    sensor_probe: Callable[[], SensorPath | None] = find_sensor,
                    current: Config | None = None) -> Config:
    """Build only a value. Four inputs preserve the requested I/O/account seam.

    username is the caller's SUDO_USER candidate, never an unchecked default;
    sensor_probe is a narrow read-only discovery seam, not a capability guess.
    """
    prompts = _Prompts(input_fn, output_fn)
    seed = current or Config()
    output_fn(MSR_NOTICE)
    wallet = prompts.ask(f"Monero mainnet wallet [{seed.wallet}]: ", lambda v: _wallet(v or seed.wallet))
    host = prompts.ask(f"Custom pool hostname [{seed.pool.host}]: ", lambda v: _hostname(v or seed.pool.host))
    port = prompts.integer(f"Pool port [{seed.pool.port}]: ", (1, 65535, seed.pool.port))
    tls = prompts.boolean("Pool TLS", seed.pool.tls)
    worker_default = seed.pool.worker if current else socket.gethostname()

    def worker(value: str) -> str:
        candidate = value or worker_default
        if not candidate or any(ord(c) < 32 or ord(c) == 127 for c in candidate):
            raise WizardError("pool.worker", "enter a nonempty worker without control characters")
        return candidate

    pool = Pool(host, port, tls, prompts.ask(f"Worker [{worker_default}]: ", worker))
    donate = prompts.integer(f"Donation level [{seed.donate_level}]: ", (1, 99, seed.donate_level))

    def cpu_mode(value: str) -> str:
        mode = value or seed.cpu.mode
        if mode not in ("spare", "spare-capped", "custom"):
            raise WizardError("cpu.mode", "choose spare, spare-capped or custom")
        return mode

    mode = prompts.ask(f"CPU mode spare/spare-capped/custom [{seed.cpu.mode}]: ", cpu_mode)
    percent = seed.cpu.max_threads_percent
    custom = seed.cpu.custom_json
    if mode == "spare-capped":
        percent = prompts.integer(f"Maximum threads percent [{percent}]: ", (1, 100, percent))
    if mode == "custom":
        def custom_fragment(value: str) -> str:
            value = value or seed.cpu.custom_json
            try:
                _ = Config.from_dict({"cpu": {"mode": "custom", "custom_json": value}})
            except ConfigError as error:
                raise WizardError("cpu.custom_json", str(error)) from error
            return value

        custom = prompts.ask("Custom CPU JSON object: ", custom_fragment)
    sensor = sensor_probe()
    thermal = seed.thermal
    if sensor is None:
        output_fn("Thermal guard UNAVAILABLE/disabled: no credible CPU sensor detected.")
    else:
        thermal = replace(thermal, enabled=prompts.boolean("Enable CPU thermal guard", thermal.enabled))
    default_user = " ".join(seed.gaming.users)
    if username and current is None:
        try:
            if len(username.split()) != 1 or username.split()[0] != username:
                raise WizardError("gaming.users", "SUDO_USER must be one account name")
            default_user = " ".join(_users(username))
        except WizardError:
            output_fn("SUDO_USER is not a valid existing account; no gaming user default.")
    gaming = replace(seed.gaming, enabled=False)
    if dict(caps.helper_paths).get("gamemoded") is None:
        output_fn("GameMode was not detected; gaming pause requires GameMode installation and user-session hooks.")
    if prompts.boolean("Enable gaming guard", seed.gaming.enabled):
        users = prompts.ask(f"Gaming account names [{default_user}]: ", lambda v: _users(v or default_user))
        gaming = Gaming(users=users)
    hugepages = seed.hugepages
    output_fn("Regular 2 MiB hugepages: 1280 pages reserve 2560 MiB (~2.5 GiB) temporarily while mining; " +
              "released best-effort after stop. Usually 20–30% improvement, possibly greater for RandomX " +
              "(up to 50%), hardware-dependent and not guaranteed. 1 GiB pages are disabled; " +
              "their incremental benefit is only 1–3% beyond regular pages.")
    if caps.numa_nodes is None or len(caps.numa_nodes) != 1:
        output_fn(NUMA_NOTICE)
    else:
        enabled = prompts.boolean("Enable dynamic 2 MiB hugepages", hugepages.enabled)
        pages = prompts.integer(f"2 MiB page count [{hugepages.pages}]: ", (1, 2**31 - 1, hugepages.pages)) if enabled else hugepages.pages
        hugepages = Hugepages(enabled, pages)
    return _checked(replace(seed, wallet=wallet, pool=pool, donate_level=donate,
                            cpu=Cpu(mode, percent, custom), thermal=thermal,
                            gaming=gaming, hugepages=hugepages), caps, sensor)
