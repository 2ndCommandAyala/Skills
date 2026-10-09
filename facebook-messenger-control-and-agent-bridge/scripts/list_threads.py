"""List Messenger threads locally for explicit route configuration."""

import asyncio
import sys

from _fb_runtime import cookie_file

async def list_threads():
    try:
        from fbchat_muqit import Client
    except ImportError as exc:
        raise RuntimeError("Install the Facebook skill requirements in a virtual environment.") from exc
    with cookie_file() as path:
        client = Client(cookies_file_path=str(path), disable_logs=True)
        try:
            await client.start()
            threads = await client.fetch_thread_list(limit=15)
            for thread in threads:
                name = thread.name or "Unnamed Thread"
                print(f"{name}: {thread.thread_id} ({thread.thread_type.name})")
        finally:
            await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(list_threads())
    except (ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
