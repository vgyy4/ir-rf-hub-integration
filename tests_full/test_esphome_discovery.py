"""Exercises async_report_esphome_devices_forever's wiring against real
Home Assistant core -- specifically that
homeassistant.components.zeroconf.async_get_async_instance(hass) is a
valid call and the loop correctly forwards whatever a browse cycle
finds to the client.

Both the actual mDNS browse (_browse_once) and the shared-Zeroconf
lookup itself are monkeypatched out. Real multicast discovery doesn't
belong in a unit test (see the App-side tests_full's own equivalent --
it never tests its own local mDNS browse either, only the merge logic
around it). Constructing a *real* HaAsyncZeroconf here (even one whose
result goes unused, since _browse_once is mocked) trips HA's
"multiple Zeroconf instance" detector and leaves a dangling instance
that crashes *other* tests' teardown with "Event loop is closed" --
confirmed by an earlier version of this file failing CI. Monkeypatching
async_get_async_instance itself avoids ever creating one, while still
exercising the real call site/import path.
"""

from __future__ import annotations

import asyncio

import pytest

import custom_components.ir_rf_hub.esphome_discovery as esphome_discovery
from custom_components.ir_rf_hub.esphome_discovery import async_report_esphome_devices_forever


class _RecordingClient:
    def __init__(self) -> None:
        self.reports: list[list[dict]] = []

    async def async_report_discovered_devices(self, devices: list[dict]) -> None:
        self.reports.append(devices)


class _FakeAsyncZeroconf:
    zeroconf = None


@pytest.fixture(autouse=True)
def _no_real_zeroconf_instance(monkeypatch: pytest.MonkeyPatch):
    async def fake_get_async_instance(hass):
        return _FakeAsyncZeroconf()

    monkeypatch.setattr(esphome_discovery.ha_zeroconf, "async_get_async_instance", fake_get_async_instance)


async def test_reports_whatever_a_browse_cycle_finds(hass, monkeypatch: pytest.MonkeyPatch):
    async def fake_browse_once(zeroconf) -> list[dict]:
        return [{"name": "living-room-esp", "host": "10.0.0.9", "port": 6053}]

    monkeypatch.setattr(esphome_discovery, "_browse_once", fake_browse_once)
    monkeypatch.setattr(esphome_discovery, "_BROWSE_INTERVAL_S", 0)

    client = _RecordingClient()
    task = asyncio.ensure_future(async_report_esphome_devices_forever(hass, client))
    try:
        for _ in range(50):
            if client.reports:
                break
            await asyncio.sleep(0.05)
        assert client.reports[0] == [{"name": "living-room-esp", "host": "10.0.0.9", "port": 6053}]
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_empty_browse_cycle_does_not_report(hass, monkeypatch: pytest.MonkeyPatch):
    async def fake_browse_once(zeroconf) -> list[dict]:
        return []

    monkeypatch.setattr(esphome_discovery, "_browse_once", fake_browse_once)
    monkeypatch.setattr(esphome_discovery, "_BROWSE_INTERVAL_S", 0)

    client = _RecordingClient()
    task = asyncio.ensure_future(async_report_esphome_devices_forever(hass, client))
    try:
        await asyncio.sleep(0.3)
        assert client.reports == []
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
