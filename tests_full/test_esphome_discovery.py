"""Exercises async_report_esphome_devices_forever's wiring against real
Home Assistant core -- specifically that
homeassistant.components.zeroconf.async_get_async_instance(hass) is a
valid call and the loop correctly forwards whatever a browse cycle
finds to the client.

The actual mDNS browse (_browse_once) is monkeypatched out here --
real multicast discovery doesn't belong in a unit test (see the App-
side tests_full's own equivalent: it never tests its own local mDNS
browse either, only the merge logic around it). The shared-Zeroconf
lookup itself (async_get_async_instance) is neutralized globally by
conftest.py's autouse _no_real_zeroconf_instance fixture -- constructing
a *real* HaAsyncZeroconf turned out to corrupt hass's own teardown
badly enough to crash *other*, unrelated tests, confirmed by CI.
"""

from __future__ import annotations

import asyncio

import pytest
from zeroconf import ServiceStateChange, Signal

import custom_components.ir_rf_hub.esphome_discovery as esphome_discovery
from custom_components.ir_rf_hub.esphome_discovery import async_report_esphome_devices_forever


class _RecordingClient:
    def __init__(self) -> None:
        self.reports: list[list[dict]] = []

    async def async_report_discovered_devices(self, devices: list[dict]) -> None:
        self.reports.append(devices)


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


async def test_on_change_callback_is_compatible_with_real_signal_fire(monkeypatch: pytest.MonkeyPatch):
    """Regression test for the exact bug _browse_once's own comment
    documents: zeroconf's Signal.fire(**kwargs) invokes every registered
    handler by keyword (zeroconf=..., service_type=..., name=...,
    state_change=...) -- see zeroconf/_services/__init__.py's Signal.fire
    and browser.py's _fire_service_state_changed_event, which is exactly
    how a real AsyncServiceBrowser dispatches to the handlers it's given.
    _on_change's first parameter must therefore be named exactly
    `zeroconf`, or the call itself raises TypeError before its body ever
    runs. Every other test in this file monkeypatches _browse_once as a
    whole, so a parameter-name regression here went uncaught until a real
    install hit it.

    This fakes out AsyncServiceBrowser -- the same external-library
    boundary the rest of this project fakes at (see the App-side
    fake_esphome_server.py) -- with a stand-in that registers the real
    handler onto a genuine zeroconf.Signal and fires it exactly like the
    real browser does, so this exercises the real library's calling
    convention rather than a mock's. Firing with `Removed` (not `Added`)
    deliberately skips _on_change's _resolve() branch, so no real mDNS
    resolution I/O is needed -- this test is only about the call
    succeeding at all.
    """
    real_signal = Signal()

    class _FakeAsyncServiceBrowser:
        def __init__(self, zeroconf, service_type, handlers) -> None:
            for h in handlers:
                real_signal.registration_interface.register_handler(h)
            real_signal.fire(
                zeroconf=zeroconf,
                service_type=service_type,
                name="test-device._esphomelib._tcp.local.",
                state_change=ServiceStateChange.Removed,
            )

        async def async_cancel(self) -> None:
            pass

    monkeypatch.setattr(esphome_discovery, "AsyncServiceBrowser", _FakeAsyncServiceBrowser)
    monkeypatch.setattr(esphome_discovery, "_BROWSE_WINDOW_S", 0.01)

    devices = await esphome_discovery._browse_once(object())
    assert devices == []


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
