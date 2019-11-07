import os


__all__ = [
    "BASE_PATH",
    "CHORD_URL",
    "CHORD_REGISTRY_URL",
    "DB_PATH",
    "SERVICE_SOCKET",

    "CHORD_URLS_SET",

    "TIMEOUT",
    "WORKERS",
    "LAST_ERRORED_CACHE_TIME",
]


BASE_PATH = os.environ.get("SERVICE_URL_BASE_PATH", "")
CHORD_URL = os.environ.get("CHORD_URL", "")
CHORD_REGISTRY_URL = os.environ.get("CHORD_REGISTRY_URL", "")  # "http://1.chord.dlougheed.com/"
DB_PATH = os.path.join(os.getcwd(), os.environ.get("DATABASE", "data/federation.db"))
SERVICE_SOCKET = os.environ.get("SERVICE_SOCKET", "/tmp/federation.sock")

CHORD_URLS_SET = CHORD_URL.strip() != "" and CHORD_REGISTRY_URL.strip() != ""

TIMEOUT = 45
WORKERS = 10
LAST_ERRORED_CACHE_TIME = 30