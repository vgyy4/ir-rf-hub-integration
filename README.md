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
3. The App announces itself to Supervisor as soon as it's running, so a "IR/RF Command Hub" **Discovered** card should appear under Settings → Devices & services within a minute or so -- click it, then Submit. No fields, nothing to copy.
4. If it doesn't show up (App not running under Supervisor, or this integration installed after the App gave up re-announcing): Settings → Devices & services → Add integration → "IR/RF Command Hub", then open the App -- the first time it starts, it shows a pairing code on a screen you can't get past until you pair -- and paste that code into the single field in the setup form. The code encodes the App's internal host, port, and an auth token together, so no separate host/port entry is needed.

## Testing

This package is tested in three tiers, deliberately:

- **`tests/`** (runs locally with plain `pytest` + `aiohttp` — no Home Assistant core installed) covers everything that doesn't subclass HA base classes: `pairing.py` (pairing-code decode), `sync.py` (the coordinator's add/remove diffing), and `api.py` (the HTTP/WS client, tested against a real local `aiohttp.web` server standing in for the App). Run with:
  ```
  python -m venv .venv-test
  .venv-test/Scripts/pip install aiohttp voluptuous pytest pytest-asyncio
  .venv-test/Scripts/pytest
  ```
- **`tests_full/`** covers everything `tests/` structurally can't: the real `ConfigFlow`, the `DataUpdateCoordinator` subclass, and the `button`/`switch` entity platforms, using [`pytest-homeassistant-custom-component`](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component) against real Home Assistant core. This only ever runs in GitHub Actions' cloud runners (see `.github/workflows/ci.yaml`'s `test-full` job) — **Home Assistant core is intentionally never installed on a contributor's own machine** for this project. It's not runnable locally by design; if you're making changes to `__init__.py`, `config_flow.py`, `coordinator.py`, `entity.py`, `button.py`, or `switch.py`, push to a branch/PR and let CI validate them, or install the integration on a real HA instance directly.
- CI also runs [`hassfest`](https://developers.home-assistant.io/docs/creating_integration_manifest/#hassfest) and the [HACS validator](https://hacs.xyz/docs/publish/action/) against the manifest/structure on every push and PR.

All four CI jobs are required status checks before anything can merge into `main`.

## Contributing

Pull requests are welcome for review, but merges into `main` are
restricted to the repository owner while this project is experimental —
see the branch protection settings. Please open an issue to discuss
larger changes before submitting a PR.
