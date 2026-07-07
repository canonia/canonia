# Copyright 2026 André Lopes
# SPDX-License-Identifier: Apache-2.0
"""Tests for index backend detection + resolution.

Pure-Python: no NumPy/ONNX needed, so this file has no importorskip guard. The
sqlite-vec store isn't implemented (and can't load on this machine's Python), so
capability is simulated by monkeypatching the two probes.
"""

import sqlite3

import pytest

from canonia import index

# --- capability probes ------------------------------------------------------

def test_loadable_extensions_probe_returns_bool():
    # Value is platform-dependent (False on macOS system Python); only assert it
    # answers cleanly without raising.
    assert isinstance(index.sqlite_loadable_extensions(), bool)


def test_loadable_extensions_probe_leaves_no_open_connection(monkeypatch):
    # The probe must close its throwaway connection regardless of outcome —
    # a closed connection raises ProgrammingError on use.
    holder = {}
    real_connect = sqlite3.connect

    def spy_connect(*a, **k):
        holder["conn"] = real_connect(*a, **k)
        return holder["conn"]

    monkeypatch.setattr(index.sqlite3, "connect", spy_connect)
    index.sqlite_loadable_extensions()
    with pytest.raises(sqlite3.ProgrammingError):
        holder["conn"].execute("SELECT 1")


def test_sqlite_vec_available_returns_bool():
    assert isinstance(index.sqlite_vec_available(), bool)


# --- resolution -------------------------------------------------------------

def test_resolve_sqlite_is_brute_force():
    c = index.resolve_backend("sqlite")
    assert c.name == "sqlite" and not c.fell_back
    assert "brute-force" in c.reason


def test_resolve_none_defaults_to_sqlite():
    assert index.resolve_backend(None).name == "sqlite"


def test_resolve_sqlite_vec_falls_back_when_no_extension(monkeypatch):
    monkeypatch.setattr(index, "sqlite_loadable_extensions", lambda: False)
    monkeypatch.setattr(index, "sqlite_vec_available", lambda: True)
    c = index.resolve_backend("sqlite-vec")
    assert c.name == "sqlite" and c.fell_back
    assert "can't load extensions" in c.reason


def test_resolve_sqlite_vec_falls_back_when_not_installed(monkeypatch):
    monkeypatch.setattr(index, "sqlite_loadable_extensions", lambda: True)
    monkeypatch.setattr(index, "sqlite_vec_available", lambda: False)
    c = index.resolve_backend("sqlite-vec")
    assert c.name == "sqlite" and c.fell_back
    assert "not installed" in c.reason


def test_resolve_sqlite_vec_capable_but_store_unbuilt(monkeypatch):
    # Even with both capabilities, the store isn't written yet → brute force.
    monkeypatch.setattr(index, "sqlite_loadable_extensions", lambda: True)
    monkeypatch.setattr(index, "sqlite_vec_available", lambda: True)
    c = index.resolve_backend("sqlite-vec")
    assert c.name == "sqlite" and c.fell_back
    assert "not yet implemented" in c.reason


def test_resolve_auto_falls_back_silently(monkeypatch):
    # `auto` asked us to choose, so an unavailable vec store is not a warning.
    monkeypatch.setattr(index, "sqlite_loadable_extensions", lambda: False)
    monkeypatch.setattr(index, "sqlite_vec_available", lambda: False)
    c = index.resolve_backend("auto")
    assert c.name == "sqlite" and not c.fell_back


def test_resolve_unknown_backend_falls_back_flagged():
    c = index.resolve_backend("faiss")
    assert c.name == "sqlite" and c.fell_back
    assert "unknown backend" in c.reason
