# IR/RF Command Hub — Home Assistant integration

> [!WARNING]
> **This project is experimental.** It was built quickly and has not been
> verified against a wide range of setups; the pairing mechanism and data
> it exposes may change without notice between versions. Please [open an
> issue](https://github.com/vgyy4/ir-rf-hub-integration/issues) if
> something breaks.

Companion integration for the **IR/RF Command Hub** [App](https://github.com/vgyy4/ir-rf-hub). Connects to the
App over your Home Assistant Supervisor's internal network and creates a
`button` + `switch` entity pair (grouped under one Device) for every
command recorded in the App, kept live-synced as commands are added,
renamed, or deleted.

## Installing

1. Copy `custom_components/ir_rf_hub` into your Home Assistant `config/custom_components/` directory (or install via HACS once published there).
2. Restart Home Assistant.
3. Settings → Devices & services → Add integration → "IR/RF Command Hub".
4. Open the App's own Settings page, copy the pairing code shown there, and paste it into the single field in the setup form. That's it — the code encodes the App's internal host, port, and an auth token together, so no separate host/port entry is needed.

## Testing

This package is tested in two tiers, deliberately:

- **`tests/`** (this repo, runs with plain `pytest` + `aiohttp` — no Home Assistant core installed) covers everything that doesn't subclass HA base classes: `pairing.py` (pairing-code decode), `sync.py` (the coordinator's add/remove diffing), and `api.py` (the HTTP/WS client, tested against a real local `aiohttp.web` server standing in for the App). Run with:
  ```
  python -m venv .venv-test
  .venv-test/Scripts/pip install aiohttp voluptuous pytest pytest-asyncio
  .venv-test/Scripts/pytest
  ```
- **`__init__.py`, `config_flow.py`'s `ConfigFlow` class, `coordinator.py`'s `DataUpdateCoordinator` subclass, `entity.py`, `button.py`, `switch.py`** genuinely require Home Assistant core (they subclass its entity/coordinator/config-flow base classes) and are **not** unit-tested in this repo by design — this project deliberately does not install `homeassistant` locally. Verify these by installing the integration on a real Home Assistant instance (see above) and checking: the config flow accepts a valid pairing code and rejects an invalid one, entities appear grouped correctly per command, a dashboard button card fires a real transmit, the native "Button pressed" automation trigger fires, and the switch visibly toggles on then resets.

CI also runs [`hassfest`](https://developers.home-assistant.io/docs/creating_integration_manifest/#hassfest) and the [HACS validator](https://hacs.xyz/docs/publish/action/) against the manifest/structure on every push and PR, which catches real structural mistakes without needing HA core installed locally either.

## Contributing

Pull requests are welcome for review, but merges into `main` are
restricted to the repository owner while this project is experimental —
see the branch protection settings. Please open an issue to discuss
larger changes before submitting a PR.
