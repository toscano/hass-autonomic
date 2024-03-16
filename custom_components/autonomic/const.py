"""Constants for the AVPro matrix switch integration."""
from typing import Final
from datetime import timedelta

# This is the internal name of the integration, it should also match the directory
# name for the integration.
DOMAIN: Final           = "autonomic"
MANUFACTURER: Final     = "Autonomic"

MODE_UNKNOWN: Final     = "mode_unknown"
MODE_MRAD: Final        = "mode_mrad"
MODE_STANDALONE: Final  = "mode_standalone"

MIN_VERSION_REQUIRED: Final = "6.1.20180215.0"

RETRY_CONNECT_SECONDS: Final= 30
PING_INTERVAL:Final         = timedelta(seconds=10)

TICK_THRESHOLD_SECONDS: Final =  5
TICK_UPDATE_SECONDS: Final    =  4
