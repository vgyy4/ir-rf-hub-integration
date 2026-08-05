"""Real Home Assistant core test harness -- deliberately never run on a
developer's own machine (see the top-level README's "Testing" section and
CLAUDE-facing history: HA core is intentionally not installed locally for
this project). This suite only runs inside GitHub Actions' cloud runners,
where installing Home Assistant core is someone else's ephemeral VM, not
the user's computer.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# custom_components/ lives at the integration repo root, one level up from
# this tests_full/ directory -- add it explicitly rather than relying on
# pytest's rootdir-insertion heuristics, which are sensitive to exactly
# how/where pytest is invoked from and easy to get subtly wrong across two
# nested pytest.ini files in the same repo (see tests_full/pytest.ini).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """pytest-homeassistant-custom-component only loads custom_components/
    when this fixture is active -- without it, hass silently behaves as
    if our integration doesn't exist at all, which fails in a confusing
    way (config flow "domain not found") rather than a clear error.
    """
    yield


@pytest.fixture(autouse=True)
def auto_enable_sockets(socket_enabled):
    """pytest-homeassistant-custom-component's own hass fixture calls
    pytest_socket.disable_socket() directly, which a plain --allow-hosts
    CLI option doesn't override -- the socket_enabled fixture is the
    documented counter for tests (like ours) that intentionally need real
    local network I/O against a real aiohttp server standing in for the
    App.
    """
    yield


@pytest.fixture(autouse=True)
def _mock_zeroconf(mock_async_zeroconf):
    """manifest.json now declares zeroconf as a dependency (hassfest
    requires it, since esphome_discovery.py imports
    homeassistant.components.zeroconf), so Home Assistant Core's own
    zeroconf.async_setup() runs automatically for every test that sets up
    this integration's config entry -- not just the ones that go through
    our own background discovery task. async_setup() constructs a *real*
    Zeroconf instance via its own internal _async_get_instance() call
    (not the public async_get_async_instance() wrapper our code uses, so
    patching only that wrapper doesn't cover this path). A real instance
    here corrupts hass's own EVENT_HOMEASSISTANT_CLOSE-triggered zeroconf
    teardown badly enough to crash *later*, unrelated tests with
    "RuntimeError: Event loop is closed" -- confirmed by CI across
    several iterations chasing the actual construction site.

    mock_async_zeroconf is pytest-homeassistant-custom-component's own
    official fixture for exactly this: it patches the HaZeroconf/
    HaAsyncZeroconf *classes* Home Assistant Core constructs from, so it
    covers every call path uniformly (async_setup, our own
    async_get_async_instance call, whatever else) rather than chasing
    individual functions that happen to call into them.
    """
    yield
