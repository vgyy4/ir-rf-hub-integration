DOMAIN = "ir_rf_hub"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_TOKEN = "token"

DEFAULT_SWITCH_RESET_DELAY_S = 1.0

CONF_DEVICE_GROUPING = "device_grouping"

# One HA Device per Command (today's behavior) -- users reason about "TV
# Power," not about which physical device happens to route it.
MODE_SEPARATE = "separate"
# Every command's entities collapse onto the single hub device -- fewer
# devices to scroll through for setups with many recorded commands.
MODE_UNIFIED = "unified"
# Two virtual devices, "Buttons" and "Switches" -- a middle ground that
# still separates the two entity kinds (selects group with buttons) but
# stops giving every command its own device.
MODE_SPLIT_BY_TYPE = "split_by_type"

DEFAULT_DEVICE_GROUPING = MODE_SEPARATE
