import os
import urllib.parse

from . import __version__


__all__ = [
    "BASE_PATH",
    "CHORD_DEBUG",
    "BENTO_FEDERATION_MODE",
    "CHORD_URL",
    "CHORD_HOST",
    "CHORD_REGISTRY_URL",

    "OIDC_DISCOVERY_URI",

    "DB_PATH",

    "MAX_BUFFER_SIZE",

    "SERVICE_ORGANIZATION",
    "SERVICE_ARTIFACT",
    "SERVICE_TYPE",
    "SERVICE_ID",
    "SERVICE_NAME",

    "SERVICE_SOCKET",

    "INITIALIZE_IMMEDIATELY",

    "CHORD_URLS_SET",

    "TIMEOUT",
    "WORKERS",
    "LAST_ERRORED_CACHE_TIME",
]


def _env_to_bool(var: str, default: bool = False) -> bool:
    return os.environ.get(var, str(default)).strip().lower() == "true"


BASE_PATH = os.environ.get("SERVICE_URL_BASE_PATH", "")
CHORD_DEBUG = _env_to_bool("CHORD_DEBUG")
BENTO_FEDERATION_MODE = _env_to_bool("BENTO_FEDERATION_MODE", default=True)
CHORD_URL = os.environ.get("CHORD_URL", "").strip()
CHORD_HOST = urllib.parse.urlparse(CHORD_URL or "").netloc or ""
CHORD_REGISTRY_URL = os.environ.get("CHORD_REGISTRY_URL", "")  # "http://1.chord.dlougheed.com/"
OIDC_DISCOVERY_URI = os.environ.get("OIDC_DISCOVERY_URI")

DB_PATH = os.path.join(os.getcwd(), os.environ.get("DATABASE", "data/federation.db"))

SERVICE_ORGANIZATION = "ca.c3g.bento"
SERVICE_ARTIFACT = "federation"
SERVICE_TYPE = f"{SERVICE_ORGANIZATION}:{SERVICE_ARTIFACT}:{__version__}"
SERVICE_ID = os.environ.get("SERVICE_ID", SERVICE_TYPE)
SERVICE_NAME = "Bento Federation Service"

SERVICE_SOCKET = os.environ.get("SERVICE_SOCKET", "/tmp/federation.sock")

INITIALIZE_IMMEDIATELY = _env_to_bool("INITIALIZE_IMMEDIATELY", default=True)

CHORD_URLS_SET = CHORD_URL.strip() != "" and CHORD_REGISTRY_URL.strip() != ""

TIMEOUT = 180  # seconds
LAST_ERRORED_CACHE_TIME = 30
MAX_BUFFER_SIZE = 1024 ** 3  # 1 gigabyte; maximum size a response can be

try:
    # noinspection PyUnresolvedReferences
    WORKERS = len(os.sched_getaffinity(0))
except AttributeError:  # Some operating systems don't provide sched_getaffinity
    WORKERS = 4  # Default to 4 workers
