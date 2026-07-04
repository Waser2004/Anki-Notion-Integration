# AGENTS.md

AI agents: follow these repo rules. Keep changes minimal and scoped.

## Repo map
- `docs/`: product + architecture documentation (start here for context).
- `docs/documentation`: code documentation.
- `src/Noteck/`: main Python package.
- `src/Noteck/ui/`: UI code lives here.
- `tests/`: automated tests live here.

## Guardrails
- Don’t refactor unrelated code.
- Don’t change deps, build, CI, or infra unless asked.
- Don’t touch secrets; never commit credentials.
- Ask/flag if requirements are ambiguous or risky.
- Don’t assume a function’s output format; verify it from the code, tests, docs, and when helpful the web.
- Don’t assume a function’s input format; verify it from the code, tests, docs, and when helpful the web.
- Use all available reliable sources before making a decision. If no source gives a clear answer, make the smallest reasonable assumption and label it clearly in code and in the response.

## Output expectations
- Prefer clear diffs + brief rationale.
- Note files changed and commands run.
- Leave TODOs only when unavoidable and clearly scoped.
- Always comment code to ensure readability by humans.
- When preparing a PR, update the docs if the change affects behavior, usage, or any documented workflow.
- A PR must always be created as ready for review.
- If a PR is not ready for review, resolve the limiting factor and keep going until it is ready.
- If the blocker is missing information, ask for clarification.

## Testing
- Activate the project virtual environment (`.venv\Scripts\Activate.ps1`).
- From the repository root run: `python -m unittest discover -s tests`.
- Ensure dev/test dependencies are installed in the active environment.
