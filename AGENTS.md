# CoC 7e LLM Keeper — Agent Guide

## Project overview

This is **CoC 7e LLM Keeper v2.8.1**, a terminal-based Call of Cthulhu 7th Edition game master.
It pairs a deterministic Python game engine with a swappable LLM narrator.
Dice, combat math, sanity math, spatial logic, room truth, movement, character creation, and save/resume all run locally;
the LLM only narrates what the engine produces.

The default LLM backend is **Kimi (Moonshot AI)**, but the provider layer is agnostic:
any OpenAI-compatible API works, plus native Google Gemini.
The project also has a fully offline `--mock` mode for testing and CI,
and a v2.8.1.5 **human Keeper provider** (`--human-keeper` or `"provider": "human"`)
where a human host narrates from engine-built packets instead of an AI API.

## Technology stack

- **Language:** Python 3 (no web framework; plain stdlib + small SDKs).
- **LLM SDKs:**
  - `openai` — for all OpenAI-compatible providers (Kimi, DeepSeek, OpenAI, OpenRouter, Groq, Together, xAI, Ollama, LM Studio, custom).
  - `google-genai` — for Gemini only.
- **Optional Google Docs:** `google-auth`, `google-auth-oauthlib`, `google-auth-httplib2`, `google-api-python-client` (only if `google_docs.enabled = true`).
- **No database:** persistence is JSON files on disk (`saves/`, `data/`, `logs/`).

## Project layout

```
coc7-keeper/
├── config/
│   ├── settings.json        # game, LLM, combat, sanity, chronicle knobs
│   ├── system-prompt.txt    # Keeper system prompt + required JSON schema
│   └── api-key.json         # API keys (gitignored, created by user)
├── data/
│   ├── investigators.json   # created investigators (gitignored in practice)
│   ├── occupations.json     # 15 CoC 7e occupations for the wizard
│   └── scenarios/           # one folder per scenario, each with scenario.json
│       ├── the-haunting/
│       ├── tallow-chapel/
│       └── five-minute-house/  # teaching scenario from README-WORLDBUILDING.md
├── docs/                    # release notes, roadmaps, provider setup
├── logs/                    # llm_timing.jsonl, turn_timing.jsonl, llm_raw_*.txt (gitignored)
├── saves/                   # campaign state: saves/<scenario>/world-state.json
├── src/                     # all source code
├── test_*.py                # test suites
├── README-WORLDBUILDING.md  # scenario/item/NPC authoring guide (non-programmers)
└── requirements.txt
```

## Build and run commands

Run from the project root (the folder containing `config/`).

First-time setup:

```bat
py -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
notepad config\api-key.json
```

Everyday commands:

```bat
py -m src.main                          # lobby -> scenario -> party -> live session
py -m src.main --scenario data/scenarios/the-haunting
py -m src.main --new-character          # CoC 7e investigator creation wizard
py -m src.main --mock                   # offline full-loop test, no API key needed
py -m src.main --human-keeper           # human host narrates from engine packets (no API)
py -m src.main --reset                  # wipe save and restart the scenario
py -m src.main --debug                  # live per-call LLM timing on screen
```

On macOS/Linux use `python` instead of `py`.

## Test commands

All tests are run from the project root.

Offline suites (no API key, no network, no tokens):

```bat
py test_engine.py       # 447+ checks covering dice, combat, sanity, state, lobby, mock turns, provider wiring, room truth, latency governor
py test_charcreate.py   # 64+ checks covering the 7e creation wizard math
py test_items.py        # 130+ checks covering the v2.8.0 item/object registry (v2.8.0.1)
py test_adjudicator.py  # 238 checks covering the adjudication layer (201-phrase corpus + live cases)
```

Live smoke tests (require a valid key in `config/api-key.json` and cost tokens):

```bat
py test_kimi.py         # one default-tier Kimi call
py test_latency.py      # one default + one heavy-tier call, timed
py test_latency.py --report   # free: analyze logs/llm_timing.jsonl
py bench_governor.py    # v2.8.1.6: routine solo / complex solo / routine duo + timeout sim
py test_docs.py         # Google Docs chronicle append test (only if enabled)
```

**Note:** `test_engine.py` assumes a clean `saves/` directory for the lobby tests.
If it fails on `no save -> save_turn is None`, clear or move `saves/` before running.
Inside the suite, keeper saves/loads under real scenario ids (`the-haunting`,
`five-minute-house`, `tallow-chapel`) are redirected to `saves/rld-<id>/` — a full
run never creates or deletes a live campaign save; only the lobby scan still reads
the real paths, which is why the clean-`saves/` precondition remains.
The printed check total also wobbles by design: one dice-edge check fires only when a
d100 lands on 1 or 100 (500 rolls, ~2% each). The v2.8.1.x partyturn2 deterministic
baseline is **~515 checks**; anything within ~±15 of that is the same suite
(see docs/HANDOFF.md §3).

## Main module divisions

| File | Responsibility |
|------|----------------|
| `src/main.py` | CLI entry point, argument parsing, startup lobby orchestration. |
| `src/keeper.py` | `CoCKeeper` — turn loop, preroll net, LLM prompt building, state-delta application, save/load. |
| `src/character.py` | `Character` and `Weapon` dataclasses; derived stats, serialization, active/summary formats. |
| `src/charcreate.py` | Investigator creation wizard (rulebook-accurate), roster save/load, `ConsoleIO`/`ScriptedIO`. |
| `src/combat.py` | `CombatEngine` — attacks, damage rolls, range bands, malfunctions. |
| `src/dice.py` | `DiceEngine` — d100, skill checks with bonus/penalty dice, fumble thresholds. |
| `src/sanity.py` | `SanityEngine` — SAN rolls, temporary/indefinite insanity, Mythos gain. |
| `src/spatial.py` | `Location` dataclass (incl. v2.8.1 authored room fields) and `SpatialEngine` — BFS distance, perception levels, sound propagation, occupant moves. |
| `src/room_view.py` | v2.8.1 Room Truth: deterministic room views, exit state rules, offline movement matching, LLM-escalation triggers. |
| `src/state.py` | `save_world` / `load_world` — full campaign snapshot to JSON (items, objects, visited rooms, clue stamps). |
| `src/mode.py` | `ModeSelector` — picks `SQUAD` / `INDIVIDUAL` / `CINEMATIC` mode from declarations and scene state. |
| `src/llm_client.py` | Provider-agnostic factory and `OpenAICompatClient`; tolerant JSON parsing/repair; timing logs; retry ladder; governed query path. |
| `src/latency_governor.py` | v2.8.1.6 Latency Governor: `CallPlan` per LLM call (prompt tier/cap, model tier, budget, deadline), true deadline cancellation, fresh compact-retry prompts, section telemetry, degraded fallback. |
| `src/human_keeper.py` | v2.8.1.5 Human Keeper provider: packet builder/renderer, host input protocol (`/private`, `/public`, `/end`, `/cancel`, `/skip`, `/help`), `HumanKeeperClient` — no API, no timeout, no cost. |
| `src/adjudicator.py` | v2.8.1.2: declaration → intent frames → roll/local/clarify/passthrough decisions (data-driven, `data/action_prototypes.json`). |
| `src/action_intent.py` | `IntentFrame` — the adjudicator↔resolver contract + normalization. |
| `src/action_resolver.py` | Executes intent frames through dice/combat/local commands; builds outcome packets. |
| `src/skill_graph.py` | Natural-language skill aliases → canonical 7e skills; base values. |
| `src/gemini_client.py` | Native Gemini client using `google-genai`. |
| `src/mock_keeper.py` | `MockKeeperClient` — deterministic fake LLM for `--mock` mode and tests. |
| `src/chronicle.py` | `LocalChronicle` (default) and legacy `Chronicle` (Google Docs) backends. |
| `src/lobby.py` | Scenario scanner and interactive scenario/party selection menus. |

## Key runtime architecture

1. **Startup:** `main.py` loads `config/settings.json`, optionally shows the scenario lobby, loads a scenario, resumes or creates a party, then calls `keeper.run_session()`.
2. **Turn loop:** `run_session()` collects player declarations, then `take_turn()`:
   - Resolves any pending LLM-requested rolls answered with `roll!` / `yes` / etc.
   - Runs the **preroll net** on risky declarations (search, listen, combat, stealth, climb, locksmith, etc.) so dice are resolved before the LLM sees the turn.
   - Announces every roll at the table.
   - Builds a JSON prompt with scene, characters, declarations, and dice results.
   - Calls the LLM (`default` or `heavy` tier based on mode + `heavy_escalation` policy).
   - Parses the JSON response, applies `state_delta`, queues new `dice_requests`, writes to chronicle, and saves state.
3. **Persistence:** campaigns live in `saves/<scenario_id>/world-state.json`. The chronicle (default `chronicle/` folder or Google Docs) is a log, not the authoritative save.
4. **Mode selection:** `SQUAD` is used for 3+ players doing the same non-risky thing; `INDIVIDUAL` for combat, sanity, chase, or differentiated actions; `CINEMATIC` for chases.

## Configuration files

- **`config/settings.json`** — all knobs:
  - `llm`: provider (incl. `"human"` for the v2.8.1.5 human Keeper), models, temperature, token budgets, `heavy_escalation`, `compact_prompt`, `extra_body`, `loading_bar`.
  - `game`: max players, squad threshold, skill cap, startup menu toggle.
  - `state`: compression, delta-only, summary length.
  - `latency`: v2.8.1.6+ hard targets — `routine/complex/heavy/compact_retry_timeout`,
    `max_routine/complex/cinematic_prompt_chars` (v2.8.1.7: TOTAL-basis, system
    prompt included), `compact_budget_by_provider`.
  - `chronicle`: backend (`local` / `google` / `off`), folder, batch size.
  - `google_docs`: optional service-account path and document ID.
  - `combat` / `sanity` / `pricing`: subsystem tuning and cost rates.
- **`config/system-prompt.txt`** — the Keeper system prompt. It includes the required JSON output schema. The LLM must return an object with `mode`, `narration`, `private_narrations`, `state_delta`, `required_actions`, `dice_requests`, and `mode_switch`.
- **`config/api-key.json`** — user-created key file, gitignored. Shape: `{"kimi_api_key": "...", "gemini_api_key": "...", ...}`.

## Code style and conventions

- Python style is functional-where-simple; small modules with clear responsibilities.
- Use `dataclasses` for entities (`Character`, `Weapon`, `Location`).
- Skill names are canonicalized with underscores (e.g., `Firearms_Rifle_Shotgun`, `Library_Use`). `canon_skill()` in `charcreate.py` handles free-text input.
- Lazy-import optional dependencies (`openai`, `google.genai`) so `--mock` mode and offline tests work before packages are installed.
- Comments often cite field regressions (e.g., "v2.7.1 field log") — keep this pattern when fixing bugs.
- All user-visible strings are in English; the code base is English-only.
- Prefer `os.path.join` and run from project root; paths are relative to the root.

## Testing strategy

- **Offline unit/integration tests:** `test_engine.py` and `test_charcreate.py` cover the engine, rules, state round-trip, mock turns, provider wiring, and CLI flags. These should stay green.
- **Live smoke tests:** `test_kimi.py`, `test_latency.py`, `test_docs.py` require real keys and cost money. Use them after offline suites pass.
- **Manual smoke:** `py -m src.main --mock` lets you type actions and verify the full loop without an API call.
- Tests use a lightweight `check()` helper and `ScriptedIO` for deterministic wizard input.

## Security considerations

- API keys and service-account files live in `config/` and are **gitignored**. Never commit them.
- The default chronicle backend is `local`, so no network is required and no Google credentials are used unless explicitly enabled.
- `--mock` mode never touches the network and needs no keys; safe for public CI.
- LLM raw failures are written to `logs/llm_raw_*.txt`; these may contain prompt text but should not contain keys.
- The engine never sends dice randomness or character stats to the LLM in a way that affects mechanical outcomes; the LLM only receives the results.

## Common gotchas

- Run everything from the project root so relative paths (`config/`, `saves/`, `data/`, `logs/`) resolve.
- If `test_engine.py` fails on lobby save-turn checks, clear `saves/` first — the suite expects no pre-existing campaign files.
- `py -m src.main` and `py src/main.py` behave identically (a path shim is in `main.py`).
- The character wizard saves to `data/investigators.json`; reusing a name replaces that investigator.
- System-channel gear commands (`inventory`, `equip <name>`, `unequip`) are handled by `_meta_command` and never become declarations sent to the LLM.
- v2.8.1 observation (`observe`, `look`, `look around`, `examine room`) and pure movement declarations (`go to`, `enter`, `leave`, `go back`, ...) are also local: no LLM call, no narrative turn. `src/room_view.py` owns rooms — never re-derive exits or visibility ad hoc.
- Typing `exit` quits the GAME (pre-v2.8.1 quit check); room movement uses `leave`, `go back`, or `return`.
- Hidden items/exits (`"hidden"` tag / exit state) must never reach a render, a room view, or the LLM prompt — room_view enforces this; don't bypass it.
- Engine-resolved moves are re-asserted after `state_delta` application via `_engine_moved`; new local-resolution paths must use the same pattern so the model cannot out-vote the engine.
- A pending roll requested by the LLM via `dice_requests` is answered by typing `roll!` (or `yes`/`go ahead`/etc.) at the player's prompt.
- Human Keeper mode (`--human-keeper` / `"provider": "human"`) never touches the API: no key, timeout, retry, or cost. The engine builds the packet (`build_human_keeper_packet`), `HumanKeeperClient.narrate()` handles terminal I/O, and human turns log separately to `logs/human_keeper.jsonl`. `/cancel` refunds the turn like an LLM error.
- The Latency Governor (v2.8.1.6) shapes every non-mock, non-human LLM call. Mock sessions intentionally keep the legacy prompt path — tests force the governed path with `keeper._force_governor = True`. The SDK runs `max_retries=0`: deadlines are enforced by `run_with_deadline` + `abort_in_flight`, not by the SDK's retry multiplication (the 542s field stall).
- Pending numbered menus (`enter` → `2`) are owned by the character who triggered them (v2.8.1.7): a hotseat answer from another player routes to the owner with an audit line — the answering player's state never changes. Keep this invariant; remote play will depend on it.
- v2.8.1.x: pending menus are runtime-only. They are cleared when answered (valid or invalid), when ANY player commits a new declaration, at turn completion, and before save; old saves are stripped on load. Cross-player routing exists ONLY for the explicit numeric forms (bare `2`, `enter 2`) — a bare `enter` or any non-numeric input is always the typing player's own command.
- v2.8.1.x: a rejected narration earns exactly ONE compact correction attempt (`COMPACT_SYSTEM_PROMPT` + compact outcome packet + violations, `CallPlan.for_validation_retry()` — compact budget/deadline, no ladder). Any failure falls back to `_minimal_outcome_result`; the full prompt is never re-sent. Timing categories: `narration_validation_retry` / `narration_validation_local_fallback`.
- v2.8.1.x: an engine-resolved move is never re-adjudicated (`_engine_moved` skip in `take_turn`). Entering a room named 'Study' is not the verb 'study'; without an authored `entry_check` there is no passive entry roll. A visible clue still escalates and is stamped discovered.
- v2.8.1.x: `position` is engine-owned mechanical state (combat range derives from it). It is NOT in `MODEL_SAFE_CHARACTER_FIELDS`; only deterministic systems change it (close distance, retreat/cover, forced/combat movement, scenario placement). Narration may describe distance, never assign position.
- v2.8.1.x: a known too-far melee attack (>3y) resolves locally with zero LLM calls and no narrative turn; 'close distance'/'charge' is a deterministic local action that sets the mover's position adjacent to the target; leaping/charging attacks too far away clarify close-first instead of narrating a known miss.
- v2.8.1.x: narration validation also rejects internal-id leaks (clue/front/location/object/template/NPC snake_case ids — player-facing names only) and key/door continuity contradictions (the movement packet carries `key_used`, `door_open`, origin/destination, first_visit).
- v2.8.1.x: party declaration UX — the prompt explains `[Enter=pass, 'done'=resolve]`; `pass`/`wait` skip, `done`/`resolve` stop collecting and resolve the current batch.
- Test hygiene: `test_charcreate.py` writes rosters to a per-run temp dir, never `saves/test/`; `data/investigators.json` is user-local (repo ships `data/investigators.example.json`). A full test run must leave the tracked tree untouched.
