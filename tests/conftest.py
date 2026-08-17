"""Suite-wide guarantee that the tests make no outbound network connection.

The gateway's own fixtures already forbid client instantiation at the seam. This
closes the remaining gap: if any code path ever reached a provider (or any other
host) during a test, the connection itself fails loudly instead of silently
spending money or depending on the network.
"""
from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def no_outbound_network(monkeypatch):
    def _blocked(self, address, *args, **kwargs):
        raise AssertionError(f"tests must not open a network connection (attempted {address})")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked)
