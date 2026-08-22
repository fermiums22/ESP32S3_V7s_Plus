"""Safely merge Home Assistant app options through the Supervisor API."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def request_json(method: str, path: str, payload: dict | None = None) -> dict:
    token = os.environ["SUPERVISOR_TOKEN"]
    data = None if payload is None else json.dumps(payload).encode()
    request = Request(
        f"http://supervisor{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urlopen(request, timeout=30) as response:
        result = json.load(response)
    if result.get("result") != "ok":
        raise RuntimeError(result)
    return result.get("data") or {}


def endpoint_prefix(slug: str) -> str:
    for prefix in ("/apps", "/addons"):
        try:
            request_json("GET", f"{prefix}/{slug}/info")
            return prefix
        except HTTPError as error:
            if error.code != 404:
                raise
    raise RuntimeError(f"App not found: {slug}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("slug")
    parser.add_argument("--openai-key-file")
    parser.add_argument("--pcm-source")
    parser.add_argument("--external-port", type=int)
    parser.add_argument("--close-external-port", action="store_true")
    args = parser.parse_args()

    prefix = endpoint_prefix(args.slug)
    info = request_json("GET", f"{prefix}/{args.slug}/info")
    options = dict(info.get("options") or {})
    if args.openai_key_file:
        key = Path(args.openai_key_file).read_text(encoding="utf-8").strip()
        if not key.startswith("sk-"):
            raise ValueError("OpenAI key file has an unexpected format")
        options["openai_api_key"] = key
    if args.pcm_source:
        options.update(
            {
                "rtsp_url": args.pcm_source,
                "stt_only_mode": True,
                "cloud_stt_enabled": True,
            }
        )
    if args.external_port:
        options["leave_front_door_open"] = True
    if args.close_external_port:
        options.pop("leave_front_door_open", None)

    payload: dict = {"options": options}
    if args.external_port:
        payload["network"] = {"6052/tcp": args.external_port}
    elif args.close_external_port:
        payload["network"] = {"6052/tcp": None}
    request_json("POST", f"{prefix}/{args.slug}/options", payload)
    print("ok")


if __name__ == "__main__":
    main()
