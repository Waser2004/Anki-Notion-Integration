"""Authenticate with the AI API, request speech audio, save it, and play it."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any
from urllib import error, request


AI_API_STARTUP_ADMIN_EMAIL = "admin@admin.com"
AI_API_STARTUP_ADMIN_PASSWORD = "IhNub10JaudimAP!"


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
    """Send a JSON POST request and return status code plus parsed JSON body."""
    encoded_payload = json.dumps(payload).encode("utf-8")
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)

    http_request = request.Request(url, data=encoded_payload, headers=request_headers, method="POST")
    try:
        with request.urlopen(http_request) as response:
            response_body = response.read().decode("utf-8")
            return response.status, json.loads(response_body) if response_body else {}
    except error.HTTPError as exc:
        response_body = exc.read().decode("utf-8")
        parsed = json.loads(response_body) if response_body else {}
        return exc.code, parsed


def _post_binary(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> tuple[int, bytes, dict[str, str]]:
    """Send a JSON POST request and return status code plus raw bytes response."""
    encoded_payload = json.dumps(payload).encode("utf-8")
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)

    http_request = request.Request(url, data=encoded_payload, headers=request_headers, method="POST")
    try:
        with request.urlopen(http_request) as response:
            return response.status, response.read(), dict(response.headers.items())
    except error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers.items())


def _try_play_audio(path: Path) -> bool:
    """Try to play the generated audio using a platform-specific command."""
    if sys.platform == "darwin":
        if shutil.which("afplay"):
            return subprocess.run(["afplay", str(path)], check=False).returncode == 0
        return False

    if sys.platform.startswith("linux"):
        if shutil.which("paplay"):
            return subprocess.run(["paplay", str(path)], check=False).returncode == 0
        if shutil.which("aplay"):
            return subprocess.run(["aplay", str(path)], check=False).returncode == 0
        if shutil.which("ffplay"):
            return (
                subprocess.run(
                    ["ffplay", "-nodisp", "-autoexit", str(path)],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).returncode
                == 0
            )
        return False

    if sys.platform.startswith("win"):
        if shutil.which("powershell.exe"):
            command = f'Start-Process -FilePath "{path}"'
            return (
                subprocess.run(
                    ["powershell.exe", "-NoProfile", "-Command", command],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).returncode
                == 0
            )
        return False

    return False


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(description="Request text-to-speech audio from the local AI API.")
    parser.add_argument("--base-url", default="http://localhost:8000", help="AI API base URL.")
    parser.add_argument("--email", default=AI_API_STARTUP_ADMIN_EMAIL, help="Admin login email.")
    parser.add_argument("--password", default=AI_API_STARTUP_ADMIN_PASSWORD, help="Admin login password.")
    parser.add_argument(
        "--text",
        default="What is the capital of {{c1::France}}?",
        help="Text to synthesize into speech.",
    )
    parser.add_argument("--voice", default="alloy", help="OpenAI voice name.")
    parser.add_argument("--format", default="mp3", choices=["mp3", "wav"], help="Audio output format.")
    parser.add_argument("--speed", type=float, default=1.0, help="Speech speed from 0.5 to 2.0.")
    parser.add_argument(
        "--parse-cloze",
        action="store_true",
        help="Enable cloze-marker parsing before synthesis.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output path. Defaults to ./tmp/tts_sample.<format>.",
    )
    return parser


def main() -> int:
    """Authenticate and call /v1/static/text-to-speech with sample data."""
    args = _build_parser().parse_args()
    if not args.email or not args.password:
        print("Missing admin credentials. Set AI_API_STARTUP_ADMIN_EMAIL and AI_API_STARTUP_ADMIN_PASSWORD.", file=sys.stderr)
        return 1

    base_url = args.base_url.rstrip("/")
    token_status, token_body = _post_json(
        f"{base_url}/v1/auth/token",
        {"email": args.email, "password": args.password},
    )
    if token_status != 200:
        print("Auth failed:")
        print(json.dumps(token_body, indent=2))
        return 1

    access_token = token_body.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        print("Auth succeeded but no access token was returned.", file=sys.stderr)
        return 1

    payload = {
        "text": args.text,
        "voice": args.voice,
        "format": args.format,
        "speed": args.speed,
        "parse_cloze": bool(args.parse_cloze),
    }
    tts_status, tts_bytes, _ = _post_binary(
        f"{base_url}/v1/static/text-to-speech",
        payload,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if tts_status != 200:
        print(f"TTS failed with HTTP {tts_status}")
        try:
            print(json.dumps(json.loads(tts_bytes.decode("utf-8")), indent=2))
        except (UnicodeDecodeError, json.JSONDecodeError):
            print(tts_bytes.decode("utf-8", errors="replace"))
        return 1

    output_path = Path(args.output) if args.output else Path("tmp") / f"tts_sample.{args.format}"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(tts_bytes)
    print(f"Saved audio to: {output_path.resolve()}")

    if _try_play_audio(output_path):
        print("Audio playback started.")
    else:
        print("Auto-play unavailable. Open the file manually to listen.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
