"""Private runtime credential handling for the Facebook skill scripts."""

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path


def authorized_users():
    return {value.strip() for value in os.environ.get("AUTHORIZED_FB_USERS", "").split(",") if value.strip()}


def default_thread():
    value = os.environ.get("DEFAULT_FB_THREAD_ID", "").strip()
    if not value:
        raise ValueError("DEFAULT_FB_THREAD_ID must be configured.")
    return value


def require_thread(thread_id, allow_override=False):
    configured = default_thread()
    selected = str(thread_id or "")
    if selected != configured and not allow_override:
        raise ValueError("A different thread requires an explicitly authorized override.")
    if not selected:
        raise ValueError("A thread ID is required.")
    return selected


@contextmanager
def cookie_file():
    """Provide a cookie path outside published skills, never retaining env cookies."""
    raw = os.environ.get("FB_COOKIES")
    if not raw:
        raise ValueError("Set FB_COOKIES in the process environment.")
    cookies = json.loads(raw)
    if isinstance(cookies, dict):
        cookies = [{"name": name, "value": value} for name, value in cookies.items()]
    if (not isinstance(cookies, list) or not cookies
            or any(not isinstance(item, dict)
                   or not isinstance(item.get("name") or item.get("key"), str)
                   or not (item.get("name") or item.get("key"))
                   or not isinstance(item.get("value"), str) for item in cookies)):
        raise ValueError("FB_COOKIES must be a nonempty list of cookie records with name/key and value.")
    with tempfile.TemporaryDirectory(prefix="fb-session-") as directory:
        path = Path(directory) / "cookies.json"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(cookies, handle)
        yield path
