# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Typed TOML configuration for Sparehash."""

from __future__ import annotations

import os
import tempfile
import tomllib
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Final, TypedDict, TypeVar, cast

from sparehash.config_checks import custom_json_error as _custom_json_error
from sparehash.config_checks import valid_pool_host as _is_valid_pool_host
from sparehash.paths import CONFIG_TOML, XMRIG_CURRENT

T = TypeVar("T")


class ConfigError(ValueError):
    """A configuration file could not be parsed into the supported schema."""


class RawConfig(TypedDict, total=False):
    version: int
    wallet: str
    donate_level: int
    pool: dict[str, str | int | bool]
    cpu: dict[str, str | int]
    thermal: dict[str, bool | int]
    power: dict[str, bool]
    gaming: dict[str, bool | list[str]]
    hugepages: dict[str, bool | int]
    api: dict[str, int]
    controller: dict[str, int]
    xmrig: dict[str, str]
    msr: dict[str, bool | int]


def _field(data: RawConfig | dict[str, str | int | bool | list[str] | dict[str, str | int | float | bool]], key: str, default: T, expected: type[T]) -> T:
    value = data.get(key, default)
    if type(value) is not expected:
        raise ConfigError(f"{key} must be {expected.__name__}")
    return cast(T, value)


@dataclass(frozen=True, slots=True)
class Pool:
    host: str = ""
    port: int = 3333
    tls: bool = False
    worker: str = "sparehash"


@dataclass(frozen=True, slots=True)
class Cpu:
    mode: str = "spare"
    max_threads_percent: int = 75
    custom_json: str = ""


@dataclass(frozen=True, slots=True)
class Thermal:
    enabled: bool = False
    pause_c: int = 85
    resume_c: int = 75


@dataclass(frozen=True, slots=True)
class Power:
    maintenance_mode: bool = False


@dataclass(frozen=True, slots=True)
class Gaming:
    enabled: bool = True
    users: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Hugepages:
    enabled: bool = False
    pages: int = 1280


@dataclass(frozen=True, slots=True)
class Api:
    port: int = 38087


@dataclass(frozen=True, slots=True)
class Controller:
    min_dwell_seconds: int = 60
    reconcile_seconds: int = 30


@dataclass(frozen=True, slots=True)
class Xmrig:
    binary: str = str(XMRIG_CURRENT)


@dataclass(frozen=True, slots=True)
class Config:
    version: int = 1
    wallet: str = ""
    donate_level: int = 1
    pool: Pool = field(default_factory=Pool)
    cpu: Cpu = field(default_factory=Cpu)
    thermal: Thermal = field(default_factory=Thermal)
    power: Power = field(default_factory=Power)
    gaming: Gaming = field(default_factory=Gaming)
    hugepages: Hugepages = field(default_factory=Hugepages)
    api: Api = field(default_factory=Api)
    controller: Controller = field(default_factory=Controller)
    xmrig: Xmrig = field(default_factory=Xmrig)

    @classmethod
    def from_dict(cls, data: RawConfig) -> Config:
        return _construct(data)

    def to_dict(self) -> RawConfig:
        return cast(RawConfig, cast(object, {**asdict(self), "gaming": {**asdict(self.gaming), "users": list(self.gaming.users)}}))


DEFAULTS: Final = Config()


def validate(cfg: Config) -> list[str]:
    """Return human-readable validation errors for a typed configuration."""
    errors: list[str] = []
    if cfg.version != 1:
        errors.append("version must be 1")
    if not 1 <= cfg.donate_level <= 99:
        errors.append("donate_level must be between 1 and 99")
    if not 1 <= cfg.pool.port <= 65535:
        errors.append("pool.port must be between 1 and 65535")
    if cfg.pool.host and not _is_valid_pool_host(cfg.pool.host):
        errors.append("pool.host must be a hostname or IPv4 address without scheme, port or path")
    if cfg.cpu.mode not in ("spare", "spare-capped", "custom"):
        errors.append("cpu.mode must be spare, spare-capped, or custom")
    if cfg.cpu.mode == "custom":
        custom_json_error = _custom_json_error(cfg.cpu.custom_json)
        if custom_json_error:
            errors.append(custom_json_error)
    if not 1 <= cfg.cpu.max_threads_percent <= 100:
        errors.append("cpu.max_threads_percent must be between 1 and 100")
    if cfg.thermal.resume_c >= cfg.thermal.pause_c:
        errors.append("thermal.resume_c must be lower than pause_c (hysteresis)")
    if not 1 <= cfg.hugepages.pages:
        errors.append("hugepages.pages must be positive")
    if not 1 <= cfg.api.port <= 65535:
        errors.append("api.port must be between 1 and 65535")
    if cfg.controller.min_dwell_seconds < 0 or cfg.controller.reconcile_seconds < 1:
        errors.append("controller timing values must be non-negative and reconcile_seconds positive")
    return errors


def _table(data: RawConfig, name: str) -> dict[str, str | int | bool | list[str] | dict[str, str | int | float | bool]]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}] must be a table")
    return cast(dict[str, str | int | bool | list[str] | dict[str, str | int | float | bool]], value)


def _unknown(data: dict[str, str | int | bool | list[str] | dict[str, str | int | float | bool]], allowed: tuple[str, ...], prefix: str) -> None:
    for key in data.keys() - allowed:
        warnings.warn(f"unknown configuration key {prefix}{key}", UserWarning, stacklevel=3)


def _construct(data: RawConfig) -> Config:
    top = ("version", "wallet", "donate_level", "pool", "cpu", "thermal", "power", "gaming", "hugepages", "api", "controller", "xmrig", "msr")
    for key in data.keys() - set(top):
        warnings.warn(f"unknown configuration key {key}", UserWarning, stacklevel=3)
    msr = _table(data, "msr")
    if msr and (set(msr) != {"enabled"} or msr["enabled"] is not False):
        raise ConfigError("legacy [msr] settings are unsupported; only enabled=false is accepted")
    if msr:
        warnings.warn("disabled legacy [msr] settings are ignored", UserWarning, stacklevel=3)
    pool, cpu = _table(data, "pool"), _table(data, "cpu")
    thermal, gaming = _table(data, "thermal"), _table(data, "gaming")
    power = _table(data, "power")
    _unknown(power, ("maintenance_mode",), "power.")
    hugepages, api = _table(data, "hugepages"), _table(data, "api")
    controller, xmrig = _table(data, "controller"), _table(data, "xmrig")
    sections = ((pool, ("host", "port", "tls", "worker"), "pool."), (cpu, ("mode", "max_threads_percent", "custom_json"), "cpu."), (thermal, ("enabled", "pause_c", "resume_c"), "thermal."), (gaming, ("enabled", "users"), "gaming."), (hugepages, ("enabled", "pages"), "hugepages."), (api, ("port",), "api."), (controller, ("min_dwell_seconds", "reconcile_seconds"), "controller."), (xmrig, ("binary",), "xmrig."))
    for section, allowed, prefix in sections:
        _unknown(section, allowed, prefix)
    try:
        users = _field(gaming, "users", cast(list[str | int | bool], []), list)
        for user in users:
            if type(user) is not str:
                raise ConfigError("gaming.users must contain only strings")
        custom_json = _field(cpu, "custom_json", "", str)
        mode = _field(cpu, "mode", "spare", str)
        if mode == "custom" or custom_json:
            custom_json_error = _custom_json_error(custom_json)
            if custom_json_error:
                raise ConfigError(custom_json_error)
        return Config(
            version=_field(data, "version", 1, int), wallet=_field(data, "wallet", "", str), donate_level=_field(data, "donate_level", 1, int),
            pool=Pool(host=_field(pool, "host", "", str), port=_field(pool, "port", 3333, int), tls=_field(pool, "tls", False, bool), worker=_field(pool, "worker", "sparehash", str)),
            cpu=Cpu(mode=mode, max_threads_percent=_field(cpu, "max_threads_percent", 75, int), custom_json=custom_json),
            thermal=Thermal(enabled=_field(thermal, "enabled", False, bool), pause_c=_field(thermal, "pause_c", 85, int), resume_c=_field(thermal, "resume_c", 75, int)),
            power=Power(maintenance_mode=_field(power, "maintenance_mode", False, bool)),
            gaming=Gaming(enabled=_field(gaming, "enabled", True, bool), users=tuple(cast(str, user) for user in users)),
            hugepages=Hugepages(enabled=_field(hugepages, "enabled", False, bool), pages=_field(hugepages, "pages", 1280, int)),
            api=Api(port=_field(api, "port", 38087, int)),
            controller=Controller(min_dwell_seconds=_field(controller, "min_dwell_seconds", 60, int), reconcile_seconds=_field(controller, "reconcile_seconds", 30, int)),
            xmrig=Xmrig(binary=_field(xmrig, "binary", str(XMRIG_CURRENT), str)),
        )
    except TypeError as exc:
        raise ConfigError(f"invalid configuration value: {exc}") from exc


def load(path: str | Path = CONFIG_TOML) -> Config:
    """Read TOML, apply partial-file defaults, and parse into Config."""
    try:
        with Path(path).open("rb") as source:
            content = cast(RawConfig, cast(object, tomllib.load(source)))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"malformed TOML: {exc}") from exc
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from exc
    return Config.from_dict(content)


def _string(value: str) -> str:
    escaped: list[str] = []
    for character in value:
        codepoint = ord(character)
        if character == "\\":
            escaped.append("\\\\")
        elif character == '"':
            escaped.append('\\"')
        elif character in "\b\t\n\f\r":
            escaped.append({"\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}[character])
        elif codepoint < 0x20 or codepoint == 0x7f:
            escaped.append(f"\\u{codepoint:04X}")
        else:
            escaped.append(character)
    return '"' + "".join(escaped) + '"'


def _value(value: str | int | float | bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _string(value)
    return repr(value)


def _serialize(cfg: Config) -> str:
    lines = [f"version = {cfg.version}", f"wallet = {_string(cfg.wallet)}", f"donate_level = {cfg.donate_level}"]
    sections: tuple[tuple[str, dict[str, str | int | float | bool | tuple[str, ...] | dict[str, str | int | float | bool]]], ...] = (
        ("pool", {"host": cfg.pool.host, "port": cfg.pool.port, "tls": cfg.pool.tls, "worker": cfg.pool.worker}),
        ("cpu", {"mode": cfg.cpu.mode, "max_threads_percent": cfg.cpu.max_threads_percent, "custom_json": cfg.cpu.custom_json}),
        ("thermal", {"enabled": cfg.thermal.enabled, "pause_c": cfg.thermal.pause_c, "resume_c": cfg.thermal.resume_c}),
        ("power", {"maintenance_mode": cfg.power.maintenance_mode}),
        ("gaming", {"enabled": cfg.gaming.enabled, "users": cfg.gaming.users}),
        ("hugepages", {"enabled": cfg.hugepages.enabled, "pages": cfg.hugepages.pages}),
        ("api", {"port": cfg.api.port}),
        ("controller", {"min_dwell_seconds": cfg.controller.min_dwell_seconds, "reconcile_seconds": cfg.controller.reconcile_seconds}),
        ("xmrig", {"binary": cfg.xmrig.binary}),
    )
    for title, fields in sections:
        lines.extend(("", f"[{title}]"))
        for key, value in fields.items():
            if key == "custom_json":
                custom_json = cast(str, value)
                rendered = _string(custom_json)
            elif key == "users":
                users = cast(tuple[str, ...], value)
                rendered = "[" + ", ".join(_string(user) for user in users) + "]"
            else:
                rendered = _value(cast(str | int | float | bool, value))
            lines.append(f"{key} = {rendered}")
    return "\n".join(lines) + "\n"


def save(cfg: Config, path: str | Path = CONFIG_TOML, warn_overwrite: bool = True) -> None:
    """Atomically write the supported schema with restrictive predictable permissions."""
    errors = validate(cfg)
    if errors:
        raise ConfigError("; ".join(errors))
    serialized = _serialize(cfg)
    destination = Path(path)
    if warn_overwrite and destination.exists():
        warnings.warn(f"overwriting configuration {destination}", UserWarning, stacklevel=2)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destination.parent, delete=False) as temporary:
            temporary_name = temporary.name
            _ = temporary.write(serialized)
            temporary.flush()
            os.fchmod(temporary.fileno(), 0o644)
        os.replace(temporary_name, destination)
    except OSError as exc:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
        raise ConfigError(f"cannot save config {destination}: {exc}") from exc
