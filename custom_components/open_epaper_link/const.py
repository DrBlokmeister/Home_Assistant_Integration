DOMAIN = "open_epaper_link"
SIGNAL_TAG_UPDATE = f"{DOMAIN}_tag_update"
SIGNAL_TAG_IMAGE_UPDATE = f"{DOMAIN}_tag_image_update"
SIGNAL_AP_UPDATE = f"{DOMAIN}_ap_update"

# Timeout for accepting external tag updates when multiple APs report
DEFAULT_EXTERNAL_TIMEOUT = 1.0

# Log level options
LOG_LEVELS = [
    "critical",
    "fatal",
    "error",
    "warning",
    "warn",
    "info",
    "debug",
    "notset",
]
DEFAULT_LOG_LEVEL = "info"
