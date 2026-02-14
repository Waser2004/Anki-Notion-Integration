# Noteck AI API (Dev Milestone)

This service hosts the external API for AI features used by the Noteck Anki add-on.

Current milestone status:
- Auth endpoints are fully implemented.
- `generate-question-variants` is implemented with OpenAI (HTTP).
- `text-to-speech` is implemented with OpenAI audio synthesis.
- `evaluate-answer` is implemented with OpenAI answer evaluation.
- Environment is local/dev focused and uses SQLite by default.

## Endpoint status matrix

| Endpoint | Status |
|---|---|
| `POST /v1/auth/register` | Implemented |
| `POST /v1/auth/token` | Implemented |
| `POST /v1/auth/refresh` | Implemented |
| `GET /v1/auth/me` | Implemented |
| `POST /v1/static/generate-question-variants` | Implemented |
| `POST /v1/static/text-to-speech` | Implemented |
| `POST /v1/active/evaluate-answer` | Implemented |

## Local setup

1. Create and activate your project virtual environment.
2. Install service dependencies:

```bash
pip install -r src/ai_api/requirements.txt
```

3. Optional: create a local `.env` file in the repo root.

Example:

```env
AI_API_DATABASE_URL=sqlite:///./noteck_ai_api_dev.db
AI_API_JWT_SECRET=dev-change-me
AI_API_ENABLE_STARTUP_ADMIN_SEED=false
AI_API_STARTUP_ADMIN_EMAIL=
AI_API_STARTUP_ADMIN_PASSWORD=
AI_API_OPENAI_API_KEY=
AI_API_OPENAI_MODEL=gpt-5-mini-2025-08-07
AI_API_OPENAI_TTS_MODEL=gpt-4o-mini-tts
AI_API_OPENAI_TIMEOUT_SECONDS=20
AI_API_OPENAI_BASE_URL=https://api.openai.com/v1
```

## Run the API locally

```bash
uvicorn app.main:app --app-dir src/ai_api --reload
```

OpenAPI docs:
- `http://127.0.0.1:8000/docs`

## Run tests

```bash
python -m unittest discover -s src/ai_api/tests
```

## Text-to-speech usage

Example request against the running API:

```bash
curl -X POST "http://127.0.0.1:8000/v1/static/text-to-speech" \
  -H "Authorization: Bearer <ACCESS_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"text":"What is photosynthesis?","voice":"alloy","format":"mp3","speed":1.0}' \
  --output tts_sample.mp3
```

Generate sample audio and auto-play it locally:

```bash
python scripts/request_text_to_speech.py --base-url http://127.0.0.1:8000
```

## Evaluate-answer usage

Example request against the running API:

```bash
curl -X POST "http://127.0.0.1:8000/v1/active/evaluate-answer" \
  -H "Authorization: Bearer <ACCESS_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"question":"What is a group?","expected_answer":"A set with a binary operation that is associative, has identity and inverses.","user_answer":"A set with an operation, identity, and inverses.","grading":{"strictness":"medium","allow_paraphrase":true},"output_format":"short"}'
```

Run the local helper script:

```bash
python scripts/request_evaluate_answer.py --base-url http://127.0.0.1:8000
```

## Run with Docker

The folder includes a multi-stage `Dockerfile` with:
- `dev` target: `uvicorn --reload`
- `prod` target: `gunicorn` + `uvicorn` workers

### Dev container

1. Prepare env file:

```bash
cp src/ai_api/.env.dev.example src/ai_api/.env.dev
```

2. Start container:

```bash
docker compose -f src/ai_api/docker-compose.dev.yml --project-directory src/ai_api up --build -d
```

3. Open:
- `http://127.0.0.1:8000/docs`

4. Stop:

```bash
docker compose -f src/ai_api/docker-compose.dev.yml --project-directory src/ai_api down
```

### Prod-like container (for later)

1. Prepare env file:

```bash
cp src/ai_api/.env.prod.example src/ai_api/.env.prod
```

2. Start container:

```bash
docker compose -f src/ai_api/docker-compose.prod.yml --project-directory src/ai_api up --build -d
```

3. Stop:

```bash
docker compose -f src/ai_api/docker-compose.prod.yml --project-directory src/ai_api down
```

### Optional shortcuts

From `src/ai_api`:

```bash
make dev-up
make dev-down
make prod-up
make prod-down
```

## Notes

- `POST /v1/static/generate-question-variants` calls OpenAI over HTTP and returns `502` for upstream provider errors.
- The error envelope is stable across routes:

```json
{
  "error": {
    "code": "NOT_IMPLEMENTED",
    "message": "Endpoint scaffolded but not implemented",
    "details": {}
  }
}
```
