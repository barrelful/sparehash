# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (c) 2026 SpareHash contributors

"""Producer construction and configuration-switch tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sparehash.config import Gaming
from sparehash.gaming_producer import LOG_RELATIVE, build_producer, steam_client_pid
from sparehash.gaming_steam import SteamLogProducer


def test_steam_log_switch_defaults_off() -> None:
    # Given / When
    default = Gaming()
    # Then
    assert default.steam_log is False


def test_steam_log_switch_round_trips() -> None:
    # Given: the schema and dataclass carry the switch.
    source = (Path(__file__).resolve().parents[1] / "sparehash" / "config.py").read_text()
    # When / Then
    assert source.count("steam_log") >= 4
    assert Gaming(steam_log=True).steam_log is True


def test_build_producer_returns_none_without_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given: no Steam client for this UID.
    monkeypatch.setattr("sparehash.gaming_producer.steam_client_pid", lambda _uid: None)
    # When / Then
    assert build_producer(tmp_path, os.getuid()) is None


def test_build_producer_returns_none_for_invalid_uid(tmp_path: Path) -> None:
    # When / Then
    assert build_producer(tmp_path, -1) is None
    assert steam_client_pid(-1) is None


def test_build_producer_targets_the_owner_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Given
    monkeypatch.setattr("sparehash.gaming_producer.steam_client_pid", lambda _uid: 4242)
    (tmp_path / LOG_RELATIVE).parent.mkdir(parents=True)
    (tmp_path / LOG_RELATIVE).write_bytes(b"")
    # When
    producer = build_producer(tmp_path, os.getuid())
    # Then
    assert isinstance(producer, SteamLogProducer)
    assert producer.path == (tmp_path / LOG_RELATIVE).resolve()
