"""Authenticate once and test all AI API endpoints in one run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
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


def _post_binary(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    """Send a JSON POST request and return status code plus raw bytes body."""
    encoded_payload = json.dumps(payload).encode("utf-8")
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)

    http_request = request.Request(url, data=encoded_payload, headers=request_headers, method="POST")
    try:
        with request.urlopen(http_request) as response:
            return response.status, response.read()
    except error.HTTPError as exc:
        return exc.code, exc.read()


def _build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(description="Run all AI API endpoint checks in one script.")
    parser.add_argument("--base-url", default="http://localhost:8000", help="AI API base URL.")
    parser.add_argument("--email", default=AI_API_STARTUP_ADMIN_EMAIL, help="Admin login email.")
    parser.add_argument("--password", default=AI_API_STARTUP_ADMIN_PASSWORD, help="Admin login password.")
    parser.add_argument("--tts-output", default=None, help="Optional output path for generated TTS audio.")
    return parser


def main() -> int:
    """Authenticate once and call all three endpoints with sample payloads."""
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
    auth_headers = {"Authorization": f"Bearer {access_token}"}

    # Endpoint 1: evaluate-answer
    evaluation_payload = {
        "question": "What is a group?",
        "expected_answer": "A set with a binary operation that is associative, has identity and inverses.",
        "user_answer": "A set with an operation, identity, and inverses.",
        "grading": {"strictness": "medium", "allow_paraphrase": True},
        "output_format": "short",
    }
    eval_status, eval_body = _post_json(f"{base_url}/v1/active/evaluate-answer", evaluation_payload, headers=auth_headers)
    print(f"[evaluate-answer] HTTP {eval_status}")
    print(json.dumps(eval_body, indent=2))

    # Endpoint 2: generate-question-variants
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
        headers=auth_headers,
    )
    print(f"[generate-question-variants] HTTP {variants_status}")
    print(json.dumps(variants_body, indent=2))

    # Endpoint 3: generate-cloze-variants
    cloze_variants_payload = {
        "cloze_text": "Paris is the capital of {{c1::France}}.",
        "number_variations": 3,
        "language": "en",
        "style": "exam",
        "difficulty": "medium",
        "constraints": {"no_trick_questions": True, "keep_length_similar": True},
    }
    cloze_variants_status, cloze_variants_body = _post_json(
        f"{base_url}/v1/static/generate-cloze-variants",
        cloze_variants_payload,
        headers=auth_headers,
    )
    print(f"[generate-cloze-variants] HTTP {cloze_variants_status}")
    print(json.dumps(cloze_variants_body, indent=2))

    # Endpoint 4: text-to-speech
    tts_payload = {
        "text": "What is the capital of {{c1::France}}?",
        "voice": "alloy",
        "format": "mp3",
        "speed": 1.0,
        "parse_cloze": True,
    }
    tts_status, tts_bytes = _post_binary(f"{base_url}/v1/static/text-to-speech", tts_payload, headers=auth_headers)
    print(f"[text-to-speech] HTTP {tts_status}")
    if tts_status == 200:
        output_path = Path(args.tts_output) if args.tts_output else Path("tmp") / "tts_sample_all_endpoints.mp3"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(tts_bytes)
        print(f"Saved audio to: {output_path.resolve()}")
    else:
        try:
            print(json.dumps(json.loads(tts_bytes.decode("utf-8")), indent=2))
        except (UnicodeDecodeError, json.JSONDecodeError):
            print(tts_bytes.decode("utf-8", errors="replace"))

    statuses = [eval_status, variants_status, cloze_variants_status, tts_status]
    return 0 if all(status == 200 for status in statuses) else 1


if __name__ == "__main__":
    raise SystemExit(main())
