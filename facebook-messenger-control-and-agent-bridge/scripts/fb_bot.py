"""Allowlisted Messenger commands for local Tapo devices.

No incoming chat or home inventory is forwarded to an AI service. Device control
requires a live, unique Tapo nickname; the bot never accepts room-to-IP mappings.
"""

import importlib.util
import json
import os
from pathlib import Path
import re
import sys

from _fb_runtime import authorized_users, cookie_file, default_thread


STRICT_COMMAND = re.compile(r"^!(ping|help|list|on|off|toggle|status)(?:\s+(.+))?$", re.IGNORECASE)
NATURAL_PREFIXES = (
    ("turn on ", "on"),
    ("switch on ", "on"),
    ("turn off ", "off"),
    ("switch off ", "off"),
    ("toggle ", "toggle"),
    ("check status of ", "status"),
    ("status of ", "status"),
)


def load_tapo():
    """Find the sibling Tapo skill in flattened ~/Skills or the source tree."""
    script = Path(__file__).resolve()
    candidates = (
        script.parents[2] / "tapo-device-control" / "scripts" / "tapo_control.py",
        script.parents[2] / "tapo" / "scripts" / "tapo_control.py",
        script.parents[3] / "home" / "tapo" / "scripts" / "tapo_control.py",
    )
    for candidate in candidates:
        if not candidate.is_file():
            continue
        spec = importlib.util.spec_from_file_location("facebook_skill_tapo_control", candidate)
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except ImportError:
            return None
        return module
    return None


def approved_devices():
    """Require an explicit alias-to-app-confirmed-MAC binding for each device."""
    raw = os.environ.get("TAPO_APPROVED_DEVICES", "")
    if not raw:
        return {}
    devices = json.loads(raw)
    if not isinstance(devices, dict):
        raise ValueError("TAPO_APPROVED_DEVICES must be a JSON object.")
    result = {}
    for alias, identity in devices.items():
        if not isinstance(alias, str) or not alias.strip() or not isinstance(identity, dict):
            raise ValueError("Each approved device needs an alias and identity object.")
        mac = identity.get("mac")
        if not isinstance(mac, str) or not re.fullmatch(r"(?:[0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}|[0-9a-fA-F]{12}", mac):
            raise ValueError("Each approved device needs an app-confirmed MAC.")
        if identity.get("model") is not None and not isinstance(identity["model"], str):
            raise ValueError("Approved device model must be a string.")
        key = alias.strip().casefold()
        if key in result:
            raise ValueError("Approved aliases must be unique ignoring case.")
        result[key] = {"mac": mac, "model": identity.get("model")}
    return result


def parse_command(text):
    text = text.strip()
    strict = STRICT_COMMAND.fullmatch(text)
    if strict:
        return strict.group(1).lower(), (strict.group(2) or "").strip()
    lowered = text.casefold()
    for prefix, action in NATURAL_PREFIXES:
        if lowered.startswith(prefix):
            return action, text[len(prefix):].strip()
    return None


def create_handler(client, tapo_module):
    users = authorized_users()
    thread = default_thread()
    tapo_user = os.environ.get("TAPO_USERNAME")
    tapo_password = os.environ.get("TAPO_PASSWORD")
    devices = approved_devices()
    actions = {item.strip().lower() for item in os.environ.get("TAPO_APPROVED_ACTIONS", "").split(",") if item.strip()}
    if not actions.issubset({"status", "on", "off", "toggle"}):
        raise ValueError("TAPO_APPROVED_ACTIONS contains an unsupported action.")

    async def on_message(message):
        if str(message.sender_id) == str(client.uid):
            return
        if str(message.sender_id) not in users or str(message.thread_id) != thread:
            return
        parsed = parse_command(message.text or "")
        if not parsed:
            return
        command, alias = parsed

        async def reply(text):
            await client.send_message(text, message.thread_id)

        if command == "ping":
            await reply("Pong. Bot is online.")
            return
        if command == "help":
            await reply("Commands: !ping, !list, !on <nickname>, !off <nickname>, !toggle <nickname>, !status <nickname>.")
            return
        if tapo_module is None or not tapo_user or not tapo_password:
            await reply("Tapo integration is not configured.")
            return
        if command == "list":
            await reply("Approved nicknames: " + (", ".join(sorted(devices)) if devices else "none"))
            return

        if not alias:
            await reply(f"Specify the exact Tapo nickname: !{command} <nickname>")
            return
        identity = devices.get(alias.casefold())
        if identity is None or command not in actions:
            await reply("Device or action is not approved for this bridge.")
            return
        try:
            device, session = await tapo_module.get_device_by_alias(
                alias, tapo_user, tapo_password,
                expected_mac=identity["mac"], expected_model=identity["model"],
            )
            try:
                live_name = tapo_module.verify_live_device(device, alias, identity["mac"], identity["model"])
                state = bool(device.is_on)
                if command != "status":
                    target_on = command == "on" or (command == "toggle" and not state)
                    if target_on:
                        await device.turn_on()
                    else:
                        await device.turn_off()
                    await device.update()
                    tapo_module.verify_live_device(device, alias, identity["mac"], identity["model"])
                    if bool(device.is_on) != target_on:
                        raise ConnectionError("State readback did not confirm the action.")
                    state = target_on
                await reply(f"'{live_name}' is {'ON' if state else 'OFF'}.")
            finally:
                await tapo_module.close_device_and_session(device, session)
        except Exception as exc:
            # Do not echo raw chat text, local IPs, credentials, or library errors.
            print(f"Tapo command failed ({type(exc).__name__}).", file=sys.stderr)
            await reply("Device not found uniquely or command failed. Use its exact Tapo nickname.")

    return on_message


def start_bot():
    if not authorized_users():
        raise SystemExit("AUTHORIZED_FB_USERS must contain at least one sender ID.")
    try:
        default_thread()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    try:
        from fbchat_muqit import Client
    except ImportError as exc:
        raise SystemExit("Install the Facebook skill requirements in a virtual environment.") from exc
    with cookie_file() as path:
        client = Client(cookies_file_path=str(path), disable_logs=True)
        client.event(create_handler(client, load_tapo()))
        client.run()


if __name__ == "__main__":
    start_bot()
