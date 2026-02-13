# Noteck AI API (FastAPI) — Concrete v1 Plan

This document specifies a concrete **v1** API contract for the external AI service used by the Noteck Anki add-on.

---

## Goals

- Provide AI features to the Anki add-on via a separate, authenticated REST API.
- Support quotas/rate limits and future paid plans without breaking the client.
- Keep responses **schema-stable** and **prompt-versioned**.

---

## High-level architecture

- **Client:** Noteck Anki add-on (desktop)
- **Server:** FastAPI service (`/v1/...`)
- **AI provider:** OpenAI APIs (LLM + TTS + optional STT)
- **Auth:** Bearer access token (JWT) + refresh token
- **Billing/Quotas:** per-user monthly token budget + per-endpoint rate limits
- **Storage:** minimal (users, tokens, usage ledger, refresh tokens). Avoid storing user content by default.

---

## Core conventions

### Versioning
- All endpoints are versioned: `https://api.example.com/v1/...`
- Breaking changes require `/v2`.

### Auth
- All non-auth endpoints require:
  - `Authorization: Bearer <access_token>`

### Request IDs
- Server returns header: `X-Request-Id: <uuid>`
- Server logs the same ID for debugging.

### Idempotency
- Endpoints that may be retried accept:
  - `Idempotency-Key: <random-uuid>`

### Error format (consistent)
All errors return:
```json
{
  "error": {
    "code": "RATE_LIMITED",
    "message": "Too many requests",
    "details": { "retry_after_seconds": 12 }
  }
}
```

### Response metadata
All AI endpoints return:
- `meta.prompt_version`
- `meta.model`
- `meta.usage.{input_tokens, output_tokens}` (or equivalent)

---

## Endpoints (v1)

### Auth

#### `POST /v1/auth/register`
Create an account.

**Request**
```json
{ "email": "a@b.com", "password": "..." }
```

**Response**
```json
{ "user_id": "usr_123" }
```

---

#### `POST /v1/auth/token`
Login and obtain tokens.

**Request**
```json
{ "email": "a@b.com", "password": "..." }
```

**Response**
```json
{
  "access_token": "...",
  "refresh_token": "...",
  "token_type": "bearer",
  "expires_in": 3600
}
```

---

#### `POST /v1/auth/refresh`
Rotate refresh token and get a new access token.

**Request**
```json
{ "refresh_token": "..." }
```

**Response**
```json
{
  "access_token": "...",
  "refresh_token": "...",
  "token_type": "bearer",
  "expires_in": 3600
}
```

---

#### `GET /v1/auth/me`
Return user info + plan + quota.

**Response**
```json
{
  "user_id": "usr_123",
  "email": "a@b.com",
  "plan": "free",
  "quota": {
    "month_tokens_left": 120000,
    "reset_at": "2026-03-01T00:00:00Z"
  }
}
```

---

### Static features

#### `POST /v1/static/generate-question-variants`
Generate N unique variants of a given card question.

**Request**
```json
{
  "question": "What is a group?",
  "answer": "A set with a binary operation that is associative, has identity and inverses.",
  "number_variations": 5,
  "language": "en",
  "style": "exam",
  "difficulty": "medium",
  "constraints": {
    "no_trick_questions": true,
    "keep_length_similar": true
  }
}
```

**Response**
```json
{
  "items": [
    { "question": "Define a group in abstract algebra.", "type": "open" },
    { "question": "State the axioms that make (G, *) a group.", "type": "open" }
  ],
  "meta": {
    "prompt_version": "varq_v3",
    "model": "gpt-...",
    "usage": { "input_tokens": 123, "output_tokens": 456 }
  }
}
```

**Notes**
- Server enforces deduplication (e.g., similarity threshold) so “unique” is actually true.
- Prompt is stored in a versioned file, e.g. `prompts/varq_v3.md`.

---

#### `POST /v1/static/text-to-speech`
Convert text to audio.

**Request**
```json
{
  "text": "Define a group.",
  "voice": "alloy",
  "format": "mp3",
  "speed": 1.0
}
```

**Response**
- Return audio bytes:
  - `Content-Type: audio/mpeg` (for mp3)  
  - or `Content-Type: audio/wav`

**Notes**
- This is not “system prompt” driven; it’s voice/style parameter driven.

---

### Active features

#### `POST /v1/active/evaluate-answer`
Evaluate a user answer against an expected answer.

**Request**
```json
{
  "question": "What is a group?",
  "expected_answer": "A set with a binary operation that is associative, has identity and inverses.",
  "user_answer": "A set with an operation, identity, and inverses.",
  "grading": {
    "strictness": "medium",
    "allow_paraphrase": true
  },
  "output_format": "short"
}
```

**Response**
```json
{
  "verdict": "partial",
  "score": 0.7,
  "feedback": "Good, but you missed associativity.",
  "missing_points": ["associativity"],
  "meta": {
    "prompt_version": "eval_v2",
    "model": "gpt-...",
    "usage": { "input_tokens": 98, "output_tokens": 120 }
  }
}
```

**Notes**
- Prefer structured outputs over free-form strings for UI rendering.
- Prompt is stored in a versioned file, e.g. `prompts/eval_v2.md`.

---

## Experimental (optional later)

### `POST /v1/active/transcribe`
Upload an audio blob and receive text (non-streaming STT).

**Request**
- `multipart/form-data` with:
  - `file`: audio
  - `language`: optional (e.g., `de`, `en`)

**Response**
```json
{ "text": "..." }
```

---

### `WS /v1/active/transcribe-stream`
Streaming STT over WebSocket.

**Client → server**
```json
{ "type": "start", "format": "pcm16", "sample_rate": 16000, "language": "de" }
{ "type": "audio", "data_base64": "..." }
{ "type": "end" }
```

**Server → client**
```json
{ "type": "partial", "text": "I think a group is ..." }
{ "type": "final", "text": "I think a group is a set ..." }
{ "type": "error", "code": "...", "message": "..." }
```

**Recommendation**
- Prefer `POST /transcribe` first (simpler ops).
- Keep streaming behind a feature flag + rate limits.

---

## Quotas, rate limits, and usage tracking

### Required from day 1
- **Per-user monthly quota:** tokens/month
- **Per-user rate limits:** requests/minute per endpoint class (auth vs AI)
- **Per-IP rate limits:** especially for auth endpoints

### Suggested usage ledger fields
- `user_id`
- `timestamp`
- `endpoint`
- `input_tokens`
- `output_tokens`
- `latency_ms`
- `status_code`
- `request_id`

Expose (optional but recommended):
- `GET /v1/billing/usage` (for UI transparency)
- `GET /v1/billing/plans`

---

## Privacy & logging policy

Decide and document:
- Default: **do not store** card content or user answers.
- Logs contain **metadata only** (endpoint, usage, latency, status).
- If storing prompts/responses for debugging:
  - make it opt-in
  - redact aggressively
  - keep short retention (e.g., 7–14 days)

---

## Prompt/version management

- Prompts live in versioned files:
  - `prompts/varq_v3.md`
  - `prompts/eval_v2.md`
- Each endpoint response includes `meta.prompt_version`.
- Changing schema → new API version (or new endpoint).

---

## Naming fixes (standardized)

- `variable_questions` → `generate-question-variants`
- `read_questions_aloud` → `text-to-speech`
- `evaluate_input` → `evaluate-answer`
- Fix typos: `answer`, `whether`

---

## Minimal v1 endpoint list (recommended)

**Auth**
- `POST /v1/auth/register`
- `POST /v1/auth/token`
- `POST /v1/auth/refresh`
- `GET  /v1/auth/me`

**Static**
- `POST /v1/static/generate-question-variants`
- `POST /v1/static/text-to-speech`

**Active**
- `POST /v1/active/evaluate-answer`

**Optional later**
- `POST /v1/active/transcribe`
- `WS   /v1/active/transcribe-stream`

---

## Dev milestone update (implemented)

This repository milestone currently targets **dev-first API foundation**.

### Implemented now
- FastAPI service scaffold under `src/ai_api`
- Full auth flow:
  - `POST /v1/auth/register`
  - `POST /v1/auth/token`
  - `POST /v1/auth/refresh` (rotation)
  - `GET /v1/auth/me`
- Shared conventions:
  - `X-Request-Id` response header
  - consistent JSON error envelope
  - bearer auth guard on protected routes
  - OpenAPI route exposure for all v1 endpoints

### Shell-only for now
The following routes are exposed with final input schemas but intentionally return:
- HTTP `501`
- `error.code = "NOT_IMPLEMENTED"`

Routes:
- `POST /v1/static/generate-question-variants`
- `POST /v1/static/text-to-speech`
- `POST /v1/active/evaluate-answer`

### Dev constraints for this milestone
- SQLite is the default persistence backend.
- No external AI provider calls are made yet.
- No billing, STT, or production hardening is included.
- Add-on integration is intentionally deferred.
