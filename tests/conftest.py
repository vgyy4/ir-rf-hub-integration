"""Puts custom_components/ir_rf_hub/ directly on sys.path so its
zero-HA-dependency modules (pairing.py, sync.py, api.py) can be imported
as plain top-level modules -- `import pairing`, not
`from custom_components.ir_rf_hub import pairing`.

This is deliberate, not a shortcut: importing via the package path would
execute custom_components/ir_rf_hub/__init__.py first (Python always runs
a package's __init__ before any submodule), which imports real
`homeassistant` modules that are NOT installed in this test environment on
purpose -- this project's HA integration is tested without installing HA
core locally. entity.py/button.py/switch.py/coordinator.py/config_flow.py
subclass real HA base classes and genuinely can't be unit-tested this way;
they're intentionally left for verification on a real HA instance instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

_INTEGRATION_DIR = Path(__file__).resolve().parent.parent / "custom_components" / "ir_rf_hub"
sys.path.insert(0, str(_INTEGRATION_DIR))
