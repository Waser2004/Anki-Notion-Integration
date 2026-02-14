"""Authenticate with the AI API and request example question variants."""

from __future__ import annotations

import argparse
import json
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
        # Return the API error body so callers can print useful diagnostics.
        response_body = exc.read().decode("utf-8")
        parsed = json.loads(response_body) if response_body else {}
        return exc.code, parsed


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(description="Request 5 question variants from the local AI API.")
    parser.add_argument("--base-url", default="http://localhost:8000", help="AI API base URL.")
    parser.add_argument("--email", default=AI_API_STARTUP_ADMIN_EMAIL, help="Admin login email.")
    parser.add_argument(
        "--password",
        default=AI_API_STARTUP_ADMIN_PASSWORD,
        help="Admin login password.",
    )
    return parser


def main() -> int:
    """Authenticate and call /v1/static/generate-question-variants with sample data."""
    args = _build_parser().parse_args()
    if not args.email or not args.password:
        print("Missing admin credentials. Set AI_API_STARTUP_ADMIN_EMAIL and AI_API_STARTUP_ADMIN_PASSWORD.", file=sys.stderr)
        return 1

    base_url = args.base_url.rstrip("/")
    login_payload = {"email": args.email, "password": args.password}
    token_status, token_body = _post_json(f"{base_url}/v1/auth/token", login_payload)
    if token_status != 200:
        print("Auth failed:")
        print(json.dumps(token_body, indent=2))
        return 1

    access_token = token_body.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        print("Auth succeeded but no access token was returned.", file=sys.stderr)
        return 1

    variants_payload = {
        "question": "What is photosynthesis?",
        "answer": "Photosynthesis converts light energy into chemical energy in plants.",
        "number_variations": 5,
        "language": "en",
        "style": "exam",
        "difficulty": "medium",
        "constraints": {"no_trick_questions": True, "keep_length_similar": True},
    }
    variants_status, variants_body = _post_json(
        f"{base_url}/v1/static/generate-question-variants",
        variants_payload,
        headers={"Authorization": f"Bearer {access_token}"},
    )

    print(f"HTTP {variants_status}")
    print(json.dumps(variants_body, indent=2))
    return 0 if variants_status == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
