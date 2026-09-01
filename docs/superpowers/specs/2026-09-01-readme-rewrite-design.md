# README Rewrite — Design

Date: 2026-09-01
Topic: Rewrite README.md and add a Chinese translation

## Goal

Replace the existing `README.md` (which contains multiple factual errors about the
codebase) with a clean, minimal bilingual README pair:

- `README.md` — English (default, what GitHub shows)
- `README.zh.md` — Chinese translation

Both files cross-link at the top so readers can switch languages.

## Constraints

- Each file ≤ 60 lines.
- No emoji, no screenshots, no internal-only notes.
- Keep code blocks, tables, and bullets — Markdown-friendly.
- All factual claims must match the code state verified in this session.

## Sections (both files)

1. **Title + one-line description** (2-3 lines)
2. **Quick Start** — install + `python main.py` (≤ 10 lines)
3. **Features** — 5 short bullets
4. **CLI flags** — one compact table
5. **In-session slash commands** — one compact table
6. **Configuration** — minimal `harness.yaml` snippet + one-line env-var hint
7. **License** — MIT, one line

## Slash commands to keep

`/help /tools /status /session /cancel /compact /fork /new /resume /quit`

Drop from the README: `/reload /clear /tree /filter /nav /name` (internal-use).

## CLI flags to keep

`--cwd / --resume / --continue-last / --no-drift-check`

Drop from the README: `--skills-dir / --sessions-dir` (defaults work, mentioned in Quick Start).

## Factual corrections (vs old README)

| Old README claim | Reality | Action |
|---|---|---|
| Default model `MiniMax-M3` via `api.minimaxi.com` | `deepseek-v4-flash` via `api.deepseek.com` | Fix in Quick Start |
| LLM client supports ReAct text protocol | Only OpenAI `tool_calls` | Remove ReAct text claim |
| SQLite checkpoint `agent_state.db` | Does not exist | Remove entirely |
| `ApprovalGate` / `AntiOscillationWatchdog` classes | `make_approval_hook` / `make_watchdog_hook` factories | Update names |
| `antioscillation_watchdog.py` | `agent/hooks_watchdog.py` | Update path |
| ~600 tests | ~990 tests (1005 collected, 1 collection error) | Update count — but this section is removed |
| Harness is "Hg" everywhere | `main.py` builds prompt with `harness_name="Logi"` | Use `Hg` (project name) in README; don't mention internal `Logi` |

## Out of scope

- Removing the failing `tests/config/test_bootstrap_pi_align.py` collection error.
- Rewriting internal `CLAUDE.md`.
- Adding screenshots, demo GIFs, or contributor guide.
- CI / packaging / Docker.

## Acceptance

- Both files render correctly on GitHub.
- `README.md` is the file shown on the repo homepage.
- Each file begins with a language toggle link.
- No factual claim contradicts the codebase as of 2026-09-01.
