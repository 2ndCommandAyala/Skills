"""Control Tapo devices by their live, unique nickname on an explicit local subnet."""

import argparse
import asyncio
import ipaddress
import os
import sys

SUPPORTED_MODELS = ("p110", "l530", "l510", "p100", "p105", "p115", "p300")
MAX_CONCURRENT_HOSTS = 32
PRIVATE_IPV4_RANGES = tuple(
    ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


def make_client(username, password):
    try:
        from tapo import ApiClient
    except ImportError as exc:
        raise RuntimeError("Install the Tapo skill requirements in a virtual environment.") from exc
    return ApiClient(username, password)


def scan_hosts():
    """Return only hosts in the explicitly configured private subnet."""
    raw = os.environ.get("TAPO_SCAN_CIDR", "").strip()
    if not raw:
        raise ValueError("Set TAPO_SCAN_CIDR to the local Tapo subnet (for example, 192.168.1.0/24).")
    network = ipaddress.ip_network(raw, strict=True)
    if (network.version != 4
            or not any(network.subnet_of(private) for private in PRIVATE_IPV4_RANGES)
            or network.num_addresses > 256):
        raise ValueError("TAPO_SCAN_CIDR must be an RFC 1918 IPv4 subnet of at most 256 addresses.")
    return [str(address) for address in network.hosts()]


async def check_port(ip, port=80, timeout=0.8):
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=timeout)
        writer.close()
        await writer.wait_closed()
        return ip
    except (OSError, asyncio.TimeoutError):
        return None


async def get_device_object(client, ip, preferred_model=None):
    models = ([preferred_model] if preferred_model in SUPPORTED_MODELS else [])
    models.extend(model for model in SUPPORTED_MODELS if model != preferred_model)
    for model in models:
        try:
            device = await getattr(client, model)(ip)
            await device.get_device_info()
            return device, model
        except Exception as exc:
            if "Unauthorized" in str(exc) or "Forbidden" in str(exc):
                raise PermissionError("Tapo rejected local device authentication.") from exc
    raise ConnectionError("No supported Tapo device responded at the discovered address.")


async def discover_all_devices(username, password, silent=False):
    """Discover devices on an explicit private subnet; do not persist LAN inventory."""
    hosts = scan_hosts()
    if not silent:
        print(f"Scanning {os.environ['TAPO_SCAN_CIDR']} for Tapo devices...", file=sys.stderr)
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_HOSTS)

    async def probe(ip):
        async with semaphore:
            return await check_port(ip)

    open_ports = [ip for ip in await asyncio.gather(*(probe(ip) for ip in hosts)) if ip]
    client = make_client(username, password)

    async def inspect(ip):
        async with semaphore:
            try:
                device, model = await get_device_object(client, ip)
                info = (await device.get_device_info()).to_dict()
                alias = str(info.get("nickname") or "").strip()
                if not alias:
                    return None
                return {
                    "ip": ip,
                    "alias": alias,
                    "model": str(info.get("model") or model),
                    "is_on": bool(info.get("device_on", False)),
                    "type": model,
                    "unauthorized": False,
                }
            except (PermissionError, ConnectionError, OSError):
                return None
            except Exception:
                return None

    return [device for device in await asyncio.gather(*(inspect(ip) for ip in open_ports)) if device]


async def get_device_by_alias(alias, username, password):
    """Resolve a unique nickname afresh; never control by IP address or stale cache."""
    alias = alias.strip()
    if not alias:
        raise ValueError("A Tapo device nickname is required.")
    try:
        ipaddress.ip_address(alias)
    except ValueError:
        pass
    else:
        raise ValueError("Raw IP control is disabled; use the device's Tapo nickname.")

    devices = await discover_all_devices(username, password, silent=True)
    matches = [device for device in devices if device["alias"].casefold() == alias.casefold()]
    if not matches:
        raise LookupError(f"No Tapo device with nickname '{alias}' was discovered.")
    if len(matches) != 1:
        raise LookupError(f"Nickname '{alias}' is ambiguous; give devices unique Tapo nicknames.")

    selected = matches[0]
    client = make_client(username, password)
    device, _model = await get_device_object(client, selected["ip"], selected["type"])
    live_alias = str((await device.get_device_info()).to_dict().get("nickname") or "").strip()
    if live_alias.casefold() != alias.casefold():
        raise LookupError("Device nickname changed during discovery; command cancelled.")
    return device, selected["ip"]


async def main():
    parser = argparse.ArgumentParser(description="Discover and control Tapo devices by nickname.")
    parser.add_argument("device_name", nargs="?", help="Exact Tapo nickname; omit to list devices.")
    parser.add_argument("action", nargs="?", choices=("on", "off", "status", "toggle"))
    args = parser.parse_args()

    username = os.environ.get("TAPO_USERNAME")
    password = os.environ.get("TAPO_PASSWORD")
    if not username or not password:
        parser.error("Set TAPO_USERNAME and TAPO_PASSWORD in the process environment.")

    try:
        if not args.device_name:
            devices = await discover_all_devices(username, password)
            if not devices:
                print("No Tapo devices found.")
            else:
                for item in devices:
                    print(f"{item['alias']} ({item['model']}): {'ON' if item['is_on'] else 'OFF'}")
            return

        device, _ip = await get_device_by_alias(args.device_name, username, password)
        info = (await device.get_device_info()).to_dict()
        alias = str(info.get("nickname") or args.device_name)
        state = bool(info.get("device_on", False))
        action = args.action or "status"
        if action == "on":
            await device.on()
            print(f"Turned ON '{alias}'.")
        elif action == "off":
            await device.off()
            print(f"Turned OFF '{alias}'.")
        elif action == "toggle":
            if state:
                await device.off()
            else:
                await device.on()
            print(f"Toggled '{alias}' {'OFF' if state else 'ON'}.")
        else:
            print(f"{alias}: {'ON' if state else 'OFF'}")
            if hasattr(device, "get_current_power"):
                try:
                    power = (await device.get_current_power()).to_dict()
                    print(f"Power draw: {power.get('current_power', 0) / 1000.0:.2f} W")
                except Exception:
                    pass
    except (ValueError, LookupError, PermissionError, ConnectionError, RuntimeError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    asyncio.run(main())
