"""Authenticate with the AI API and request an answer evaluation."""

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
    parser = argparse.ArgumentParser(description="Request answer evaluation from the local AI API.")
    parser.add_argument("--base-url", default="http://localhost:8000", help="AI API base URL.")
    parser.add_argument("--email", default=AI_API_STARTUP_ADMIN_EMAIL, help="Admin login email.")
    parser.add_argument("--password", default=AI_API_STARTUP_ADMIN_PASSWORD, help="Admin login password.")
    parser.add_argument("--question", default="What is a group?", help="Question prompt.")
    parser.add_argument(
        "--expected-answer",
        default="A set with a binary operation that is associative, has identity and inverses.",
        help="Reference answer used for evaluation.",
    )
    parser.add_argument(
        "--user-answer",
        default="A set with an operation, identity, and inverses.",
        help="Learner answer to evaluate.",
    )
    parser.add_argument(
        "--strictness",
        default="medium",
        choices=["low", "medium", "high"],
        help="Evaluation strictness level.",
    )
    parser.add_argument(
        "--allow-paraphrase",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Allow semantically equivalent paraphrases.",
    )
    parser.add_argument(
        "--output-format",
        default="short",
        choices=["short", "full"],
        help="Feedback verbosity format.",
    )
    return parser


def main() -> int:
    """Authenticate and call /v1/active/evaluate-answer with sample data."""
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

    evaluation_payload = {
        "question": args.question,
        "expected_answer": args.expected_answer,
        "user_answer": args.user_answer,
        "grading": {
            "strictness": args.strictness,
            "allow_paraphrase": args.allow_paraphrase,
        },
        "output_format": args.output_format,
    }
    eval_status, eval_body = _post_json(
        f"{base_url}/v1/active/evaluate-answer",
        evaluation_payload,
        headers={"Authorization": f"Bearer {access_token}"},
    )

    print(f"HTTP {eval_status}")
    print(json.dumps(eval_body, indent=2))
    return 0 if eval_status == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
