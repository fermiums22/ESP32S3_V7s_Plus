"""Start an ESPHome Device Builder compile through its HA ingress WebSocket."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

import websockets


async def compile_configuration(url: str, configuration: str) -> int:
    async with websockets.connect(
        url,
        additional_headers={"X-HA-Ingress": "YES"},
        origin=None,
        open_timeout=15,
        # ESP-IDF extraction can block the Device Builder event loop for longer
        # than a WebSocket ping timeout on its first build.
        ping_interval=None,
        max_size=None,
    ) as connection:
        await connection.send(
            json.dumps({"type": "spawn", "configuration": configuration})
        )
        async for raw_message in connection:
            message = json.loads(raw_message)
            event = message.get("event")
            if event == "line":
                print(str(message.get("data", "")), end="", flush=True)
            elif event == "exit":
                return int(message.get("code", 1))
    return 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("configuration")
    parser.add_argument(
        "--url", default="ws://172.30.32.1:63605/compile"
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(compile_configuration(args.url, args.configuration)))


if __name__ == "__main__":
    main()
