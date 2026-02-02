# AGENTS.md

AI agents: follow these repo rules. Keep changes minimal and scoped.

## Repo map
.
├─ AGENTS.md
├─ docs/
│  ├─ plans/
│  ├─ documentation/
│  ├─ overview.md
│  ├─ architecture.md
│  └─ user_interface.md
├─ src/
│  └─ anki_notion_integration/
│     ├─ __init__.py
│     ├─ ...
│     ├─ ui/
│     └─ db/
└─ tests/

Quick notes:
- `docs/`: product + architecture documentation (start here for context).
- `docs/documentation`: code documentation.
- `src/anki_notion_integration/`: main Python package.
- `src/anki_notion_integration/ui/`: UI code lives here.
- `tests/`: automated tests live here.

## Guardrails
- Don’t refactor unrelated code.
- Don’t change deps, build, CI, or infra unless asked.
- Don’t touch secrets; never commit credentials.
- Ask/flag if requirements are ambiguous or risky.

## Output expectations
- Prefer clear diffs + brief rationale.
- Note files changed and commands run.
- Leave TODOs only when unavoidable and clearly scoped.
- Always comment code to ensure readability by humans.

## Testing
- Activate the project virtual environment (`.venv\Scripts\Activate.ps1`).
- From the repository root run: `python -m unittest discover -s tests`.
- Ensure dev/test dependencies are installed in the active environment.

Use a dedicated Git feature branch for each feature.
