"""Explicit, allowlisted Facebook Messenger send/receive bridge."""

import argparse
import asyncio
import os
import sys
import time

from _fb_runtime import authorized_users, cookie_file, require_thread

def messenger_client(path):
    try:
        from fbchat_muqit import Client
    except ImportError as exc:
        raise RuntimeError("Install the Facebook skill requirements in a virtual environment.") from exc
    return Client(cookies_file_path=str(path), disable_logs=True)


async def send_message_async(text, thread_id, allow_override=False):
    thread_id = require_thread(thread_id, allow_override=allow_override)
    with cookie_file() as path:
        client = messenger_client(path)
        try:
            await client.start()
            await client.send_message(text, thread_id)
        finally:
            await client.close()
    print("Message sent.")


async def receive_message_async(thread_id, wait_for_new, poll_interval=2, timeout=120, allow_override=False):
    thread_id = require_thread(thread_id, allow_override=allow_override)
    users = authorized_users()
    if not users:
        raise ValueError("AUTHORIZED_FB_USERS must contain at least one sender ID.")
    started_ms = int(time.time() * 1000)
    deadline = time.monotonic() + timeout
    with cookie_file() as path:
        client = messenger_client(path)
        try:
            await client.start()
            while True:
                messages = await client.fetch_thread_messages(thread_id, message_limit=10)
                for message in messages:
                    if str(message.sender_id) not in users:
                        continue
                    if wait_for_new and int(message.timestamp or 0) <= started_ms:
                        continue
                    print(message.text or "")
                    return
                if not wait_for_new:
                    print("No recent authorized messages found.", file=sys.stderr)
                    return
                if time.monotonic() >= deadline:
                    raise TimeoutError("No new authorized message arrived before the timeout.")
                await asyncio.sleep(poll_interval)
        finally:
            await client.close()


def main():
    parser = argparse.ArgumentParser(description="Allowlisted Facebook Messenger bridge.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    send = subparsers.add_parser("send")
    send.add_argument("text", help="Message text; use '-' to read standard input.")
    send.add_argument("--thread-id", default=os.environ.get("DEFAULT_FB_THREAD_ID"))
    send.add_argument("--allow-send", action="store_true", help="Explicitly permit this outbound message.")
    send.add_argument("--allow-thread-override", action="store_true", help="Explicitly permit a Commander-authorized nondefault thread.")
    receive = subparsers.add_parser("receive")
    receive.add_argument("--thread-id", default=os.environ.get("DEFAULT_FB_THREAD_ID"))
    receive.add_argument("--allow-thread-override", action="store_true", help="Explicitly permit a Commander-authorized nondefault thread.")
    receive.add_argument("--wait", action="store_true")
    receive.add_argument("--timeout", type=int, default=120, help="Maximum seconds to wait (default: 120).")
    args = parser.parse_args()

    try:
        if args.command == "send":
            if not args.allow_send:
                parser.error("Sending requires --allow-send and user authorization.")
            text = sys.stdin.read() if args.text == "-" else args.text
            asyncio.run(send_message_async(text, args.thread_id, allow_override=args.allow_thread_override))
        else:
            if args.timeout <= 0:
                parser.error("--timeout must be positive.")
            asyncio.run(receive_message_async(args.thread_id, args.wait, timeout=args.timeout, allow_override=args.allow_thread_override))
    except (ValueError, TimeoutError, RuntimeError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
