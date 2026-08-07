"""Computes the DeviceInfo a command entity should report, based on the
options-flow-selected grouping mode (see const.py's MODE_* constants).
Kept separate from entity.py so the three modes' identifier logic lives
in one place shared by every entity kind (button/switch/select), and so
__init__.py's orphan-pruning can import the same identifiers instead of
re-deriving them.
"""

from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo

from .api import CommandRecord
from .const import DOMAIN, MODE_SEPARATE, MODE_SPLIT_BY_TYPE, MODE_UNIFIED

# entity_kind "select" groups with "button" in split-by-type mode -- both
# read as "trigger an action," unlike the switch's toggle-style state.
_SPLIT_GROUP_FOR_KIND = {"button": "buttons", "select": "buttons", "switch": "switches"}


def buttons_device_id(entry_id: str) -> str:
    return f"{entry_id}_buttons"


def switches_device_id(entry_id: str) -> str:
    return f"{entry_id}_switches"


def split_device_ids(entry_id: str) -> set[str]:
    return {buttons_device_id(entry_id), switches_device_id(entry_id)}


def device_info_for(
    entry_id: str, command_id: str, command: CommandRecord | None, entity_kind: str, mode: str
) -> DeviceInfo:
    name = command.name if command else command_id
    model = "IR Command" if (command is None or command.type == "ir") else "RF Command"

    if mode == MODE_UNIFIED:
        # Reuse the hub device itself (always pre-created in
        # __init__.py) rather than a new identifier -- "unified" means
        # everything nests under the one device the user already sees.
        return DeviceInfo(identifiers={(DOMAIN, entry_id)})

    if mode == MODE_SPLIT_BY_TYPE:
        group = _SPLIT_GROUP_FOR_KIND[entity_kind]
        device_id = buttons_device_id(entry_id) if group == "buttons" else switches_device_id(entry_id)
        group_name = "Buttons" if group == "buttons" else "Switches"
        return DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=group_name,
            manufacturer="IR/RF Hub",
            via_device=(DOMAIN, entry_id),
        )

    assert mode == MODE_SEPARATE
    return DeviceInfo(
        identifiers={(DOMAIN, command_id)},
        name=name,
        manufacturer="IR/RF Hub",
        model=model,
        via_device=(DOMAIN, entry_id),
    )
