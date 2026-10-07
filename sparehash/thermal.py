"""Read-only credible CPU thermal policy; polling cadence belongs to the caller."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, assert_never, final

CPU_SENSOR_DRIVERS: Final = frozenset({"coretemp", "k10temp", "zenpower"})
PREFERRED_LABELS: Final = ("Package id 0", "Tctl", "Tdie", "Package")
LOGGER: Final = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SensorPath:
    path: Path
    driver: str
    label: str | None = None


@final
class SensorReadError(ValueError):
    path: Path
    reason: str

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        self.reason = reason
        super().__init__(f"CPU sensor {path}: {reason}")


def find_sensor(hwmon_root: Path | None = None) -> SensorPath | None:
    """Select by credible driver and label priority, never by temperature.

    Missing/unreadable driver names cannot identify a CPU. Existing input files
    need not be readable during discovery: an armed read failure must block.
    """
    root = hwmon_root if hwmon_root is not None else Path(
        os.environ.get("SPAREHASH_HWMON_ROOT", "/sys/class/hwmon")
    )
    for name_path in sorted(root.glob("hwmon*/name")):
        try:
            driver = name_path.read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            continue
        if driver not in CPU_SENSOR_DRIVERS:
            continue
        labels: dict[str, Path] = {}
        for label_path in sorted(name_path.parent.glob("temp*_label")):
            if re.fullmatch(r"temp[0-9]+_label", label_path.name) is None:
                continue
            try:
                label = label_path.read_text(encoding="ascii").strip()
            except (OSError, UnicodeError):
                continue
            input_path = label_path.with_name(label_path.name.removesuffix("_label") + "_input")
            if input_path.is_file():
                _ = labels.setdefault(label, input_path)
        for preferred in PREFERRED_LABELS:
            if preferred in labels:
                return SensorPath(labels[preferred], driver, preferred)
        fallback = name_path.parent / "temp1_input"
        if fallback.is_file():
            return SensorPath(fallback, driver)
    return None


def read_celsius(sensor: SensorPath) -> float:
    """Parse an ASCII integer millidegree reading, with at most one newline.

    Reject below absolute zero or above 200 C as implausible CPU input; these
    failures are not temperatures and must take the guard's fail-safe path.
    """
    try:
        with sensor.path.open("r", encoding="ascii", newline="") as stream:
            raw = stream.read(33)
    except (OSError, UnicodeError) as error:
        raise SensorReadError(sensor.path, str(error)) from error
    if len(raw) > 32 or re.fullmatch(r"-?[0-9]+\n?", raw) is None:
        raise SensorReadError(sensor.path, "expected integer millidegrees")
    millidegrees = int(raw)
    if not -273150 <= millidegrees <= 200000:
        raise SensorReadError(sensor.path, "outside plausible CPU range [-273150, 200000]")
    return millidegrees / 1000


class ThermalStatus(StrEnum):
    UNAVAILABLE = "UNAVAILABLE"
    CLEAR = "CLEAR"
    TRIPPED = "TRIPPED"


@dataclass(frozen=True, slots=True)
class ThermalResult:
    status: ThermalStatus
    celsius: float | None
    message: str

    @property
    def available(self) -> bool:
        return self.status is not ThermalStatus.UNAVAILABLE

    @property
    def active(self) -> bool:
        """Controller Observation.thermal value; unavailable is explicitly False."""
        match self.status:
            case ThermalStatus.UNAVAILABLE | ThermalStatus.CLEAR:
                return False
            case ThermalStatus.TRIPPED:
                return True
        assert_never(self.status)


@final
class ThermalGuard:
    """Mutable hysteresis latch; failure warnings are emitted once per guard.

    Four constructor inputs preserve Todo15's specified public/config interface.
    Config validation owns threshold ordering; this class consumes typed values.
    """

    def __init__(self, enabled: bool = False, pause_c: int = 85,
                 resume_c: int = 75, sensor: SensorPath | None = None) -> None:
        self.enabled = enabled
        self.pause_c = pause_c
        self.resume_c = resume_c
        self.sensor = sensor
        self._tripped = False
        self._warned = False
        self._unavailable_logged = False

    @property
    def available(self) -> bool:
        return self.enabled and self.sensor is not None

    def poll(self) -> ThermalResult:
        """Produce one fresh policy result, without touching the blocker registry."""
        if not self.enabled or self.sensor is None:
            self._tripped = False
            if self.enabled and not self._unavailable_logged:
                LOGGER.info("thermal guard UNAVAILABLE: no credible CPU sensor")
                self._unavailable_logged = True
            return ThermalResult(ThermalStatus.UNAVAILABLE, None, "Thermal guard unavailable")
        try:
            celsius = read_celsius(self.sensor)
        except SensorReadError as error:
            self._tripped = True
            if not self._warned:
                LOGGER.warning("thermal sensor read failed; fail-safe blocker active",
                               extra={"sensor_path": str(error.path), "reason": error.reason})
                self._warned = True
            return ThermalResult(ThermalStatus.TRIPPED, None, str(error))
        if celsius >= self.pause_c:
            self._tripped = True
        elif celsius <= self.resume_c:
            self._tripped = False
        if self._tripped:
            return ThermalResult(ThermalStatus.TRIPPED, celsius, "Thermal limit active")
        return ThermalResult(ThermalStatus.CLEAR, celsius, "CPU temperature below thermal limit")
