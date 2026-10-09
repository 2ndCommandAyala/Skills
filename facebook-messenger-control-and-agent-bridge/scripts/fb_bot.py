"""Allowlisted Messenger commands for local Tapo devices.

No incoming chat or home inventory is forwarded to an AI service. Device control
requires a live, unique Tapo nickname; the bot never accepts room-to-IP mappings.
"""

import importlib.util
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
            try:
                devices = await tapo_module.discover_all_devices(tapo_user, tapo_password, silent=True)
                if not devices:
                    await reply("No Tapo devices found.")
                    return
                rows = [
                    f"{device['alias']} ({device['model']}): {'ON' if device['is_on'] else 'OFF'}"
                    for device in devices
                ]
                await reply("Discovered Tapo devices:\n" + "\n".join(rows))
            except Exception as exc:
                print(f"Tapo discovery failed ({type(exc).__name__}).", file=sys.stderr)
                await reply("Tapo discovery failed.")
            return

        if not alias:
            await reply(f"Specify the exact Tapo nickname: !{command} <nickname>")
            return
        try:
            device, _ip = await tapo_module.get_device_by_alias(alias, tapo_user, tapo_password)
            info = (await device.get_device_info()).to_dict()
            live_name = str(info.get("nickname") or alias)
            state = bool(info.get("device_on", False))
            if command == "on":
                await device.on()
                await reply(f"Turned ON '{live_name}'.")
            elif command == "off":
                await device.off()
                await reply(f"Turned OFF '{live_name}'.")
            elif command == "toggle":
                if state:
                    await device.off()
                else:
                    await device.on()
                await reply(f"Toggled '{live_name}' {'OFF' if state else 'ON'}.")
            elif command == "status":
                await reply(f"'{live_name}' is {'ON' if state else 'OFF'}.")
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
