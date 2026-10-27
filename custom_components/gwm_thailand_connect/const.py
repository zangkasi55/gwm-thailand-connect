"""Public integration identifiers; never application credentials."""

DOMAIN = "gwm_thailand_connect"
NAME = "GWM Thailand Connect"
VERSION = "0.3.0"
APP_STORAGE = DOMAIN + ".app"
DEFAULT_POLL_SECONDS = 60
MIN_POLL_SECONDS = 30
MAX_POLL_SECONDS = 3600
CONTROL_SERVICE = "thailand_control"
FRONTEND_PATH = "/" + DOMAIN + "/dashboard"
