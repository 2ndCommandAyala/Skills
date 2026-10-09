"""Offline checks that credentials cannot reach a host before identity selection."""

import asyncio
import ipaddress
import pathlib
import sys
import types
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import tapo_control as control

NETWORK = ipaddress.ip_network("192.168.50.0/24")
MAC = "02:00:00:00:00:01"
CANDIDATE = {
    "ip": "192.168.50.10",
    "mac": MAC,
    "model": "L530E",
    "device_id": "device-1",
}


def fake_config():
    return types.SimpleNamespace(
        host=CANDIDATE["ip"], uses_http=True,
        connection_type=types.SimpleNamespace(
            device_family=types.SimpleNamespace(value="SMART.TAPOBULB"),
            https=False, http_port=80,
        ),
    )


class FakeProtocol:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


class FakeDiscoveryDevice:
    def __init__(self, mac=MAC, device_id="device-1"):
        self.mac = mac
        self.device_id = device_id
        self.model = "L530E"
        self.protocol = FakeProtocol()
        self.config = fake_config()

    async def update(self):
        raise AssertionError("Credential-free discovery must not call update()")


class DiscoveryTests(unittest.TestCase):
    def test_private_subnet_limit(self):
        with patch.dict(control.os.environ, {"TAPO_SCAN_CIDR": "192.168.50.0/24"}):
            self.assertEqual(control.scan_network(), NETWORK)
        for cidr in ("8.8.8.0/24", "192.168.0.0/16", "192.168.50.1/24"):
            with self.subTest(cidr=cidr), patch.dict(control.os.environ, {"TAPO_SCAN_CIDR": cidr}):
                with self.assertRaises(ValueError):
                    control.scan_network()

    def test_discovery_is_udp_only_and_has_no_credentials(self):
        device = FakeDiscoveryDevice()

        class FakeDiscover:
            @staticmethod
            async def discover(**kwargs):
                self.assertEqual(kwargs["target"], "192.168.50.255")
                self.assertIsNone(kwargs["credentials"])
                return {"192.168.50.10": device, "10.0.0.1": FakeDiscoveryDevice()}

        with patch.dict(sys.modules, {"kasa": types.SimpleNamespace(Discover=FakeDiscover)}):
            result = asyncio.run(control.discover_candidates(NETWORK))
        self.assertEqual(result, [CANDIDATE])
        self.assertTrue(device.protocol.closed)

    def test_directed_recheck_has_no_credentials(self):
        device = FakeDiscoveryDevice()

        class FakeDiscover:
            @staticmethod
            async def discover_single(host, **kwargs):
                self.assertEqual(host, CANDIDATE["ip"])
                self.assertIsNone(kwargs["credentials"])
                return device

        with patch.dict(sys.modules, {"kasa": types.SimpleNamespace(Discover=FakeDiscover)}):
            result = asyncio.run(control.confirm_candidate(CANDIDATE["ip"], NETWORK))
        self.assertEqual(result[0], CANDIDATE)
        self.assertTrue(device.protocol.closed)

    def test_unique_mac_and_model_required(self):
        self.assertEqual(control.select_candidate([CANDIDATE], MAC, "L530"), CANDIDATE)
        with self.assertRaises(LookupError):
            control.select_candidate([CANDIDATE], "AA:BB:CC:DD:EE:FF", "L530")
        with self.assertRaises(LookupError):
            control.select_candidate([CANDIDATE, CANDIDATE], MAC, "L530")
        with self.assertRaises(LookupError):
            control.select_candidate([CANDIDATE], MAC, "P110")
        with self.assertRaises(LookupError):
            control.select_candidate([CANDIDATE], MAC, "L530", "192.168.50.11")

    def test_no_authentication_when_selection_or_recheck_fails(self):
        async def check(candidates, confirmed, error):
            with (
                patch.object(control, "scan_network", return_value=NETWORK),
                patch.object(control, "discover_candidates", new=AsyncMock(return_value=candidates)),
                patch.object(control, "confirm_candidate", new=AsyncMock(
                    return_value=(confirmed, fake_config()))) as recheck,
                patch.object(control, "connect_selected_device") as connect,
            ):
                with self.assertRaises(error):
                    await control.get_device_by_alias(
                        "Living Room Lamp", "user", "password",
                        expected_mac=MAC, expected_model="L530",
                    )
                connect.assert_not_called()
                if not candidates:
                    recheck.assert_not_called()

        asyncio.run(check([], CANDIDATE, LookupError))
        asyncio.run(check([CANDIDATE], {**CANDIDATE, "device_id": "different"}, LookupError))

    def test_authentication_targets_only_selected_host_and_alias_is_checked(self):
        device = types.SimpleNamespace(
            alias="Living Room Lamp", mac=MAC, model="L530E",
            device_id="different-authenticated-id-namespace")
        config = fake_config()
        session = object()

        async def check():
            with (
                patch.object(control, "scan_network", return_value=NETWORK),
                patch.object(control, "discover_candidates", new=AsyncMock(return_value=[CANDIDATE])),
                patch.object(control, "confirm_candidate", new=AsyncMock(return_value=(CANDIDATE, config))),
                patch.object(control, "connect_selected_device", new=AsyncMock(return_value=(device, session))) as connect,
            ):
                actual, actual_session = await control.get_device_by_alias(
                    "Living Room Lamp", "user", "password",
                    expected_mac=MAC, expected_model="L530",
                )
                self.assertIs(actual, device)
                self.assertIs(actual_session, session)
                connect.assert_awaited_once_with(config, "user", "password")

        asyncio.run(check())

    def test_authenticated_alias_and_mac_must_match(self):
        for device in (
            types.SimpleNamespace(alias="Other Room", mac=MAC, model="L530E", device_id="device-1"),
            types.SimpleNamespace(alias="Living Room Lamp", mac="AA:BB:CC:DD:EE:FF", model="L530E", device_id="device-1"),
        ):
            with self.subTest(device=device), self.assertRaises(LookupError):
                control.verify_live_device(device, "Living Room Lamp", MAC, "L530")

    def test_authenticated_id_may_differ_from_udp_id_but_must_be_nonempty(self):
        authenticated = types.SimpleNamespace(
            alias="Living Room Lamp", mac=MAC, model="L530E",
            device_id="a" * 40,
        )
        self.assertNotEqual(authenticated.device_id, CANDIDATE["device_id"])
        self.assertEqual(
            control.verify_live_device(authenticated, "Living Room Lamp", MAC, "L530"),
            "Living Room Lamp",
        )
        authenticated.device_id = None
        with self.assertRaises(LookupError):
            control.verify_live_device(authenticated, "Living Room Lamp", MAC, "L530")

    def test_pinned_http_ignores_hostile_proxy_and_refuses_redirect(self):
        async def check():
            hostile_requests = []
            app = web.Application()
            async def redirect(_request):
                return web.Response(
                    status=302, headers={"Location": "http://example.invalid/steal"})

            app.router.add_post("/", redirect)
            hostile = web.Application()

            async def hostile_handler(request):
                hostile_requests.append(request.path)
                return web.Response(text="proxied")

            hostile.router.add_route("*", "/{tail:.*}", hostile_handler)
            good_runner = web.AppRunner(app)
            hostile_runner = web.AppRunner(hostile)
            await good_runner.setup()
            await hostile_runner.setup()
            good_site = web.TCPSite(good_runner, "127.0.0.1", 0)
            hostile_site = web.TCPSite(hostile_runner, "127.0.0.1", 0)
            await good_site.start()
            await hostile_site.start()
            good_port = good_site._server.sockets[0].getsockname()[1]
            hostile_port = hostile_site._server.sockets[0].getsockname()[1]
            try:
                proxy_url = f"http://127.0.0.1:{hostile_port}"
                with patch.dict(control.os.environ, {
                    "HTTP_PROXY": proxy_url, "HTTPS_PROXY": proxy_url,
                    "ALL_PROXY": proxy_url, "NO_PROXY": "",
                }):
                    async with control.PinnedClientSession("127.0.0.1", good_port, "http") as session:
                        from kasa.deviceconfig import DeviceConfig
                        from kasa.httpclient import HttpClient

                        self.assertIs(HttpClient(DeviceConfig(
                            host="127.0.0.1", http_client=session)).client, session)
                        self.assertFalse(session.trust_env)
                        async with session.post(f"http://127.0.0.1:{good_port}/") as response:
                            self.assertEqual(response.status, 302)
                        self.assertEqual(hostile_requests, [])
                        with self.assertRaises(ConnectionError):
                            await session.post("http://example.invalid/steal")
                        with self.assertRaises(ConnectionError):
                            await session.post(
                                f"http://127.0.0.1:{good_port}/", proxy=proxy_url)
            finally:
                await good_runner.cleanup()
                await hostile_runner.cleanup()

        asyncio.run(check())

    def test_sdk_exception_is_hidden_and_pinned_session_is_closed(self):
        async def check():
            class FakeDevice:
                @staticmethod
                async def connect(*, config):
                    self.assertFalse(config.http_client.trust_env)
                    raise RuntimeError("password in https://elsewhere.invalid/")

            config = fake_config()
            fake_kasa = types.SimpleNamespace(
                Credentials=lambda username, password: (username, password),
                Device=FakeDevice,
            )
            with patch.dict(sys.modules, {"kasa": fake_kasa}):
                with self.assertRaises(ConnectionError) as caught:
                    await control.connect_selected_device(config, "user", "password")
            self.assertNotIn("password in", str(caught.exception))
            self.assertTrue(config.http_client.closed)

        asyncio.run(check())

    def test_pinned_session_closes_even_if_disconnect_raises(self):
        async def fail_disconnect():
            raise RuntimeError("disconnect failed")

        device = types.SimpleNamespace(disconnect=fail_disconnect)
        session = types.SimpleNamespace(close=AsyncMock())
        with self.assertRaises(RuntimeError):
            asyncio.run(control.close_device_and_session(device, session))
        session.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
