"""Control a Tapo device only after credential-free, identity-pinned discovery."""

import argparse
import asyncio
import ipaddress
import os
import re

import aiohttp
from yarl import URL

SUPPORTED_MODELS = ("p110", "l530", "l510", "p100", "p105", "p115", "p300")
PRIVATE_IPV4_RANGES = tuple(
    ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)


def scan_network():
    """Require an explicitly configured, small, owned private subnet."""
    raw = os.environ.get("TAPO_SCAN_CIDR", "").strip()
    if not raw:
        raise ValueError("Set TAPO_SCAN_CIDR to the current owned Tapo subnet.")
    network = ipaddress.ip_network(raw, strict=True)
    if (network.version != 4
            or not any(network.subnet_of(private) for private in PRIVATE_IPV4_RANGES)
            or network.num_addresses > 256):
        raise ValueError("TAPO_SCAN_CIDR must be an RFC 1918 IPv4 subnet of at most 256 addresses.")
    return network


def normalize_mac(value):
    compact = re.sub(r"[:-]", "", str(value).strip())
    if not re.fullmatch(r"[0-9a-fA-F]{12}", compact):
        raise ValueError("A valid device MAC address is required.")
    return ":".join(compact[index:index + 2] for index in range(0, 12, 2)).upper()


def model_matches(actual, expected):
    return not expected or str(actual).casefold().startswith(str(expected).casefold())


def candidate_from_device(host, device, network):
    """Read only fields in a UDP discovery reply; never call update()."""
    try:
        ip = ipaddress.ip_address(host)
        if ip.version != 4 or ip not in network or ip in (network.network_address, network.broadcast_address):
            return None
        return {
            "ip": str(ip),
            "mac": normalize_mac(device.mac),
            "model": str(device.model).strip(),
            "device_id": str(device.device_id).strip(),
        }
    except (AttributeError, ValueError):
        return None


async def close_discovered(device):
    protocol = getattr(device, "protocol", None)
    if protocol is not None:
        await protocol.close()


async def discover_candidates(network):
    """Use python-kasa's UDP discovery with no credentials or HTTP update."""
    try:
        from kasa import Discover
    except ImportError as exc:
        raise RuntimeError("Install the Tapo skill requirements in a virtual environment.") from exc
    devices = await Discover.discover(
        target=str(network.broadcast_address),
        discovery_timeout=5,
        discovery_packets=2,
        credentials=None,
    )
    try:
        return [candidate for host, device in devices.items()
                if (candidate := candidate_from_device(host, device, network)) is not None]
    finally:
        for device in devices.values():
            await close_discovered(device)


async def confirm_candidate(host, network):
    """Repeat credential-free UDP discovery immediately before authentication."""
    try:
        from kasa import Discover
    except ImportError as exc:
        raise RuntimeError("Install the Tapo skill requirements in a virtual environment.") from exc
    device = await Discover.discover_single(host, discovery_timeout=3, credentials=None)
    if device is None:
        raise ConnectionError("Selected device did not answer credential-free discovery.")
    try:
        candidate = candidate_from_device(host, device, network)
        if candidate is None:
            raise ConnectionError("Selected device returned invalid identity metadata.")
        return candidate, device.config
    finally:
        await close_discovered(device)


def select_candidate(candidates, expected_mac, expected_model=None, candidate_host=None):
    """Never select a host by alias, IP, or model alone before authentication."""
    mac = normalize_mac(expected_mac)
    if candidate_host is not None:
        try:
            candidate_host = str(ipaddress.IPv4Address(candidate_host))
        except ipaddress.AddressValueError as exc:
            raise ValueError("Candidate host must be an IPv4 address from the Tapo app.") from exc
    matches = [candidate for candidate in candidates if candidate["mac"] == mac]
    if len(matches) != 1:
        raise LookupError("Expected MAC was not uniquely discovered; no credentials sent.")
    selected = matches[0]
    if candidate_host is not None and selected["ip"] != candidate_host:
        raise LookupError("Current Tapo-app IP differs from discovery; no credentials sent.")
    if not model_matches(selected["model"], expected_model):
        raise LookupError("Discovered model differs from expected model; no credentials sent.")
    return selected


class PinnedClientSession(aiohttp.ClientSession):
    """Ignore ambient proxies; allow HTTP only to one literal IP and port."""

    def __init__(self, host, port, scheme):
        self._pinned_host = str(ipaddress.IPv4Address(host))
        self._pinned_port = int(port)
        self._pinned_scheme = scheme
        super().__init__(trust_env=False, cookie_jar=aiohttp.CookieJar(unsafe=True))

    async def _request(self, method, str_or_url, **kwargs):
        url = URL(str_or_url)
        if (url.host != self._pinned_host or url.port != self._pinned_port
                or url.scheme != self._pinned_scheme or kwargs.get("proxy") is not None):
            raise ConnectionError("Tapo HTTP destination differed from pinned local device.")
        # A device-supplied Location must never redirect a credential-bearing request.
        kwargs["allow_redirects"] = False
        return await super()._request(method, url, **kwargs)


async def connect_selected_device(config, username, password):
    """Connect with python-kasa through a destination-pinned aiohttp session."""
    from kasa import Credentials, Device

    connection = config.connection_type
    scheme = "https" if connection.https else "http"
    port = connection.http_port or (443 if connection.https else 80)
    if not config.uses_http or (scheme, port) not in (("http", 80), ("https", 443)):
        raise ValueError("Discovered Tapo transport is not a supported pinned HTTP endpoint.")
    session = PinnedClientSession(config.host, port, scheme)
    config.credentials = Credentials(username, password)
    config.http_client = session
    try:
        device = await Device.connect(config=config)
    except Exception:
        await session.close()
        # The SDK error may include a URL or request data; never echo it.
        raise ConnectionError("Selected Tapo device connection failed; no retry attempted.") from None
    return device, session


async def close_device_and_session(device, session):
    """Close the pinned HTTP session even if device disconnect fails."""
    try:
        await device.disconnect()
    finally:
        await session.close()


def verify_live_device(device, alias, expected_mac, expected_model):
    live_alias = str(device.alias or "").strip()
    if live_alias.casefold() != alias.strip().casefold():
        raise LookupError("Live Tapo nickname differs from requested nickname; command cancelled.")
    if normalize_mac(device.mac) != normalize_mac(expected_mac):
        raise LookupError("Authenticated device MAC differs from expected MAC; command cancelled.")
    if not model_matches(device.model, expected_model):
        raise LookupError("Authenticated device model differs from expected model; command cancelled.")
    # L530E UDP discovery and authenticated get_device_info expose different
    # device_id namespaces (observed 32 vs 40 characters). Keep the strict
    # UDP-to-UDP ID recheck above; never require cross-protocol string equality.
    if str(device.device_id).strip() in ("", "None"):
        raise LookupError("Authenticated device did not report a device ID; command cancelled.")
    return live_alias


async def get_device_by_alias(alias, username, password, *, expected_mac,
                              expected_model=None, candidate_host=None):
    """Resolve unique identity without secrets, then authenticate one host."""
    alias = alias.strip()
    if not alias:
        raise ValueError("A Tapo device nickname is required.")
    try:
        ipaddress.ip_address(alias)
    except ValueError:
        pass
    else:
        raise ValueError("Raw IP control is disabled; use the device's Tapo nickname.")

    network = scan_network()
    candidates = await discover_candidates(network)
    selected = select_candidate(candidates, expected_mac, expected_model, candidate_host)
    second, config = await confirm_candidate(selected["ip"], network)
    if (second["mac"] != selected["mac"]
            or second["device_id"] != selected["device_id"]
            or second["model"] != selected["model"]
            or config.host != selected["ip"]):
        raise LookupError("Device identity changed before authentication; no credentials sent.")
    if selected["model"].casefold().startswith("l530") and (
            config.connection_type.device_family.value != "SMART.TAPOBULB"):
        raise LookupError("Discovered L530 protocol family is unexpected; no credentials sent.")

    if not any(selected["model"].casefold().startswith(m) for m in SUPPORTED_MODELS):
        raise ValueError("Discovered Tapo model is not supported for control.")
    # This is the first credential-bearing operation, limited to one UDP-verified host.
    device, session = await connect_selected_device(config, username, password)
    try:
        verify_live_device(device, alias, expected_mac, expected_model)
    except Exception:
        await close_device_and_session(device, session)
        raise
    return device, session


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-mac", default=os.environ.get("TAPO_EXPECTED_MAC"),
                        help="MAC independently confirmed in the Tapo app; never a cached IP")
    parser.add_argument("--expected-model", help="Expected model prefix, e.g. L530")
    parser.add_argument("--candidate-host", help="Optional current IP shown in the Tapo app")
    parser.add_argument("device_name", nargs="?", help="Exact live Tapo nickname")
    parser.add_argument("action", nargs="?", choices=("on", "off", "status", "toggle"))
    args = parser.parse_args()

    try:
        network = scan_network()
        if not args.device_name:
            candidates = await discover_candidates(network)
            if not candidates:
                print("No Tapo devices answered credential-free discovery.")
            for item in candidates:
                print(f"{item['model']} {item['mac']} (current IP {item['ip']})")
            return

        if not args.expected_mac:
            raise ValueError("Provide --expected-mac from the Tapo app; nickname cannot be read safely before authentication.")
        username = os.environ.get("TAPO_USERNAME")
        password = os.environ.get("TAPO_PASSWORD")
        if not username or not password:
            raise ValueError("Set dedicated TAPO_USERNAME and TAPO_PASSWORD in the process environment.")

        device, session = await get_device_by_alias(
            args.device_name, username, password,
            expected_mac=args.expected_mac,
            expected_model=args.expected_model,
            candidate_host=args.candidate_host,
        )
        try:
            alias = str(device.alias).strip()
            state = bool(device.is_on)
            action = args.action or "status"
            if action in ("on", "off", "toggle"):
                target_on = (action == "on") if action != "toggle" else not state
                if target_on:
                    await device.turn_on()
                else:
                    await device.turn_off()
                await device.update()
                verify_live_device(device, alias, args.expected_mac, args.expected_model)
                if bool(device.is_on) != target_on:
                    raise ConnectionError("State readback did not confirm the action; outcome may be partial.")
                print(f"{alias}: {'ON' if target_on else 'OFF'} (read back)")
            else:
                print(f"{alias}: {'ON' if state else 'OFF'}")
        finally:
            await close_device_and_session(device, session)
    except (ValueError, LookupError, PermissionError, ConnectionError, RuntimeError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    except Exception:
        parser.exit(1, "Error: Tapo request failed; outcome may be partial. Do not blindly retry.\n")


if __name__ == "__main__":
    asyncio.run(main())
