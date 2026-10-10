"""Offline checks for the Messenger-to-Tapo authorization boundary."""

import asyncio
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fb_bot


class FakeClient:
    uid = "bot"

    def __init__(self):
        self.sent = []

    async def send_message(self, text, thread):
        self.sent.append((text, thread))


class FakeDevice:
    alias = "Lamp"
    is_on = False

    async def turn_on(self):
        self.is_on = True

    async def turn_off(self):
        self.is_on = False

    async def update(self):
        pass


class FakeTapo:
    def __init__(self):
        self.device = FakeDevice()
        self.calls = []
        self.closed = False

    async def get_device_by_alias(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.device, object()

    def verify_live_device(self, *args):
        return self.device.alias

    async def close_device_and_session(self, *args):
        self.closed = True


class BotBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "AUTHORIZED_FB_USERS": "owner", "DEFAULT_FB_THREAD_ID": "group",
            "TAPO_USERNAME": "user", "TAPO_PASSWORD": "password",
            "TAPO_APPROVED_DEVICES": '{"Lamp":{"mac":"02:00:00:00:00:01","model":"L530"}}',
            "TAPO_APPROVED_ACTIONS": "status,on",
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def dispatch(self, text, sender="owner", thread="group"):
        client, tapo = FakeClient(), FakeTapo()
        message = type("Message", (), {"sender_id": sender, "thread_id": thread, "text": text})()
        asyncio.run(fb_bot.create_handler(client, tapo)(message))
        return client, tapo

    def test_unapproved_sender_thread_alias_and_action_never_connect(self):
        for text, sender, thread in (
            ("!on Lamp", "stranger", "group"),
            ("!on Lamp", "owner", "other"),
            ("!on Other", "owner", "group"),
            ("!off Lamp", "owner", "group"),
        ):
            with self.subTest(text=text, sender=sender, thread=thread):
                _, tapo = self.dispatch(text, sender, thread)
                self.assertEqual(tapo.calls, [])

    def test_approved_action_pins_identity_and_closes_session(self):
        client, tapo = self.dispatch("!on Lamp")
        self.assertEqual(tapo.calls[0][1], {"expected_mac": "02:00:00:00:00:01", "expected_model": "L530"})
        self.assertTrue(tapo.closed)
        self.assertTrue(tapo.device.is_on)
        self.assertEqual(client.sent, [("'Lamp' is ON.", "group")])

    def test_invalid_mac_rejected_at_startup(self):
        with patch.dict(os.environ, {"TAPO_APPROVED_DEVICES": '{"Lamp":{"mac":"bogus"}}'}):
            with self.assertRaises(ValueError):
                fb_bot.approved_devices()


if __name__ == "__main__":
    unittest.main()
