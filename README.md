# IR/RF Hub: Home Assistant integration

> [!WARNING]
> **This project is experimental.** It was built quickly and has not been
> verified against a wide range of setups; the pairing mechanism and data
> it exposes may change without notice between versions. Please [open an
> issue](https://github.com/vgyy4/ir-rf-hub-integration/issues) if
> something breaks.

Companion integration for the **IR/RF Hub** [App](https://github.com/vgyy4/ir-rf-hub). Connects to the
App over your Home Assistant Supervisor's internal network and creates a
`button` + `switch` + `select` entity trio for every command recorded in
the App: a button that fires it, a switch that fires-then-auto-resets
(handy for automations that expect a toggle), and a select for choosing
which ESPHome device to fire it from when you don't want the command's
default. All three are kept live-synced as commands are added, renamed, or deleted.

There's also a single `remote` entity per integration entry (not one per
command) that addresses any command by name: `remote.send_command` with
`command: ["TV Power", "AC On"]` fires them in order, which doubles as a
free way to build a short sequence/macro out of existing commands without
a dedicated feature for it.

By default each command's three entities are grouped under their own
Device. An Options flow (Settings → Devices & services → IR/RF Hub
→ Configure) lets you switch to grouping everything under one shared
hub Device instead, or splitting by button/select-vs-switch across two
devices; pick whichever matches how you want commands to show up in
dashboards and areas. Changing it reloads the integration so every
entity's device assignment updates immediately.

The integration also runs its own background mDNS scan for ESPHome
devices on your network and reports what it finds back to the App, so
the App's own "Scan for devices" can surface devices even when the App's
container can't see them directly.

## Installing

1. Copy `custom_components/ir_rf_hub` into your Home Assistant `config/custom_components/` directory (or install via HACS once published there).
2. Restart Home Assistant.
3. The App announces itself to Supervisor as soon as it's running, so a "IR/RF Hub" **Discovered** card should appear under Settings → Devices & services within a minute or so. Click it, then Submit. No fields, nothing to copy.
4. If it doesn't show up (App not running under Supervisor, or this integration installed after the App gave up re-announcing): Settings → Devices & services → Add integration → "IR/RF Hub", then open the App. The first time it starts, it shows a pairing code on a screen you can't get past until you pair, then paste that code into the single field in the setup form. The code encodes the App's internal host, port, and an auth token together, so no separate host/port entry is needed.

## Pairing & re-pair behavior

Both entry paths above converge on the same connectivity+auth check
before creating a config entry. If the App is reinstalled (Supervisor
keeps the add-on's internal hostname stable across an uninstall+reinstall,
but the App issues a fresh pairing token every time it starts unpaired),
re-pairing arrives with the same `host:port` unique ID but a new token;
the config flow detects this and updates the existing entry's data plus
schedules a reload, rather than silently rejecting it as "already
configured." Leaving the stale token in place would otherwise strand the
entry in a permanent auth-failure retry loop with no obvious fix short of
deleting and re-adding the integration. See
`custom_components/ir_rf_hub/config_flow.py`'s
`_async_update_existing_entry` for the implementation.

## Troubleshooting

If the App gets reinstalled while this integration is running, its saved
pairing token stops working. Previously that failed silently in the
background forever. Now it raises a **Repair** (Settings → System →
Repairs) telling you to reconfigure with a fresh code, which clears itself
once a resync succeeds again. There's also **Diagnostics** support
(the entry's ⋮ menu → Download diagnostics) for troubleshooting without
digging through logs; the pairing token is redacted, everything else
isn't sensitive (host/port are Supervisor-internal).

## Testing

This package is tested in three tiers, deliberately:

- **`tests/`** (runs locally with plain `pytest` + `aiohttp`, no Home Assistant core installed) covers everything that doesn't subclass HA base classes: `pairing.py` (pairing-code decode), `sync.py` (the coordinator's add/remove diffing), and `api.py` (the HTTP/WS client, tested against a real local `aiohttp.web` server standing in for the App). Run with:
  ```
  python -m venv .venv-test
  .venv-test/Scripts/pip install aiohttp voluptuous pytest pytest-asyncio
  .venv-test/Scripts/pytest
  ```
- **`tests_full/`** covers everything `tests/` structurally can't: the real `ConfigFlow`, the `DataUpdateCoordinator` subclass, the `button`/`switch`/`select`/`remote` entity platforms, Repairs, and Diagnostics, using [`pytest-homeassistant-custom-component`](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component) against real Home Assistant core. This only ever runs in GitHub Actions' cloud runners (see `.github/workflows/ci.yaml`'s `test-full` job). **Home Assistant core is intentionally never installed on a contributor's own machine** for this project. It's not runnable locally by design; if you're making changes to `__init__.py`, `config_flow.py`, `coordinator.py`, `entity.py`, `button.py`, `switch.py`, `select.py`, `remote.py`, `diagnostics.py`, or `esphome_discovery.py`, push to a branch/PR and let CI validate them, or install the integration on a real HA instance directly.
- CI also runs [`hassfest`](https://developers.home-assistant.io/docs/creating_integration_manifest/#hassfest) and the [HACS validator](https://hacs.xyz/docs/publish/action/) against the manifest/structure on every push and PR.

All four CI jobs are required status checks before anything can merge into `main`.

## Contributing

Pull requests are welcome for review, but merges into `main` are
restricted to the repository owner while this project is experimental;
see the branch protection settings. Please open an issue to discuss
larger changes before submitting a PR.
