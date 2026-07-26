# HANDOFF — Current State (v2.8.1 + hotfixes through v2.8.1.7 "Governor Accounting + Field Truth")

**Completed:** Phase 2 of the Persistent Campaign Engine Roadmap, plus the v2.8.1.1–v2.8.1.7 hotfixes, the P0 continuation hotfix, and the party-turn-contract hotfix  
**Baseline:** v2.8.0 + v2.8.0.1 "Registry Proving Ground"  
**Current version stamp:** `2.8.1` in `src/__init__.py` (v2.8.1.x are hotfixes, not version bumps)

> **v2.8.1.x — CoC 7e combat conversion (2026-07-27, researched against the
> 7e rules before implementation).** Melee is now OPPOSED roll-vs-roll RAW:
> the defender Dodges or Fights Back (engine-owned policy: fight back when
> Fighting_Brawl >= Dodge, else dodge; helpless or unaware = no defense);
> success levels compared by rank with a new CRITICAL tier (01 is always a
> critical and outranks Extreme). Dodge wins ties; the initiator wins
> fight-back ties; both failing = nothing; a winning fight-back
> counter-hits the ATTACKER for regular damage (no extreme bonus on a
> fight-back). Extreme success on an initiated attack: blunt = max weapon +
> max DB; impaling = that plus one rolled weapon damage. Firearms: point
> blank is a BONUS DIE within 1/5 DEX in feet (was: doubled damage + a
> feet-vs-yards unit bug ~3x too wide); bullets impale on Extreme, but at
> extreme range only a Critical (01) impales; nothing lands past 4x base
> range; firing into melee costs a penalty die and a fumble hits the
> lowest-Luck ally; bonus/penalty dice cancel 1:1. Surprise: scenario NPCs
> may start alerted=false — defenseless until the round ends; entering
> their room announces the drop, attacking alerts them, and
> keeper._alert_check() flips room-sharers at the end of each round.
> Constructor-weapon migration no longer degrades stats (synthesized
> templates are registered; impales carried; catalog knife impales RAW).
> New scenario data/scenarios/testing-hall — a proving ground: weapons on
> racks (revolver/shotgun/rifle/knife/club), a Range Key on the floor, a
> locked range, and unaware targets at close/near/far positions (brawler
> fights back, gunman dodges, rifleman at rifle range). New test_dice.py
> statistical audit (18 checks: uniformity, level distribution, fumble
> thresholds, bonus/penalty probabilities, independence, bad-luck
> binomials). Engine suite ~563 (+35), dice audit 18. Files: src/dice.py,
> src/combat.py, src/character.py, src/items.py, src/keeper.py,
> src/action_resolver.py, data/items.json, data/scenarios/testing-hall/,
> test_engine.py, test_items.py, test_dice.py, docs/HANDOFF.md.

> **v2.8.1.x — kimi instant mode + provider temperature fix (2026-07-27,
> live-verified against the production key).** New config knob
> `llm.disable_thinking` (shipped `true`): for the DEFAULT model on kimi
> providers only, every request merges `{"thinking": {"type": "disabled"}}`
> into extra_body (never replacing config extra_body keys). Live A/B through
> the project's own client: thinking-ON k2.6 burned thousands of hidden
> reasoning tokens per call (field avg 170.3s, 45% failure); INSTANT k2.6
> returned valid JSON in **8.2s / 151 completion tokens** on a keeper-style
> turn. Separately — and bigger than the knob — live probes proved
> **kimi-k2.6 AND kimi-k3 reject any temperature but the pinned one with
> HTTP 400** ("only 1 is allowed for this model"). Until now every
> production call's first attempt 400'd and the _generate ladder silently
> retried WITHOUT json_mode — a hidden driver of the invalid-JSON failure
> rate. `_call` now never sends temperature to kimi providers (non-kimi
> unaffected, pinned by tests). k3 never sees the thinking parameter (it
> does not accept one). +12 engine checks (suite ~526). Files:
> src/llm_client.py, config/settings.json, test_engine.py, docs/HANDOFF.md.

> **v2.8.1.x P0 continuation, part 2 (2026-07-26) — party-turn contract +
> party location truth.** Field report from two-player hotseat: pass/done
> felt dead and the engine lost track of where the party was. Reproduced
> four failure modes and fixed each, test-first (+11 engine checks, suite
> now 515): (1) An all-pass round went silent and re-asked instantly — now
> every pass is acknowledged (`[Anna passes.]`) and an all-pass round says
> so (`[Everyone passes — the moment holds. Type 'end' to let time pass.]`).
> (2) `done` with an empty batch swallowed the rest of the party's turn —
> it now prints `[Nothing to resolve yet — no declarations.]` and moves on
> to the next player. (3) `done`/`resolve` with a live batch names the
> undeclared players it treats as passing before resolving. (4) New
> `end`/`end turn`: with an empty batch it lets time pass locally (turn
> advances, zero LLM calls); with a batch it resolves like `done`. The
> declaration prompt now states where you stand and who is with you
> (`Anna (player1) [Hallway — with Bert] [Enter=pass, 'done'=resolve,
> 'end'=time passes]:`), and the help text documents the turn contract:
> one declaration per investigator per turn; pass; done/resolve; end.
> Prompt-side party truth: a new `PARTY LOCATIONS` section gives the model
> engine-verified room + companions for every investigator; anyone in a
> declaring player's room is ACTIVE (never off-screen); and every room a
> declaring player acts in gets its own deterministic ROOM VIEW section,
> not just the current scene. Local hotseat only — no remote service, no
> DeclarationQueue, no map. Known suite fragility noted: a failing run can
> leave `saves/five-minute-house/world-state.json` behind, which trips the
> lobby `save_turn is None` check on the next run; delete it (or let a
> green run's cleanup remove it).

> **v2.8.1.7 — P0 field hotfix: governor accounting, compact retry, actor
> ownership, adjudication truth.** First live field test after the Governor
> (10 narrated turns, 24 attempts, 48m25s provider wait, 1/10 clean
> first-try) found seven defects. (1) The governor measured only the dynamic
> payload; the ~11.1k system prompt was invisible — telemetry, debug lines,
> timing rows, and `--report` now carry dynamic/system/total chars, and caps
> operate on the total (shipped caps are now total-basis: 15000/17000/21000).
> (2) Degraded "retry compact" rebuilt the ordinary path — it now sends the
> stored compact prompt with `COMPACT_SYSTEM_PROMPT` and a
> `plan.for_compact_retry()` plan (compact budget, compact deadline, exactly
> one attempt). (3) Numbered-menu answers could move the WRONG actor —
> pending menus now carry an owner; a hotseat answer routes to the owner
> with an audit line (`answered '2' for Jack's pending enter`), and the
> answering player's state is untouched. (4) Adjudication: flying knee /
> judo slam / grapple / sweep kick are Fighting_Brawl (never Throw);
> third-person stage directions (`(grabs Hobbs...)`) are kept and matched;
> firearm-aimed threats are Intimidate with no ammo; questions split off as
> table talk; `burn them` binds the papers, never the NPC; destination-less
> movement stays unresolved. +6 corpus phrases, +10 live cases. (5)
> Narration validator now rejects first-visit continuity claims, unsupported
> NPC mechanical state (bleeding, major wound, unconscious, prone, pinned,
> broken bones, near death, preexisting injury), and invented scenario facts
> (countdowns, deadlines, new monsters, front/timeline events) — one strict
> retry, then the plain local outcome; unsupported narration is never
> accepted. (6) Escalation facts (NPC reveal, combat, clue/front/timeline
> triggers, multi-character outcomes) override minimal party routing, with
> `tier_reasons` in telemetry and debug. (7) Compact retry budget is
> provider-aware: kimi floors at 4096 (EMPTY/invalid-JSON at 2048 in the
> field), others keep 2048, config-overridable. +26 engine checks (baseline
> 447), adjudicator suite now 238.

> **v2.8.1.6 — Latency Governor, stage one.** Every real LLM narration call
> now passes through `src/latency_governor.py`: prompt tier (minimal /
> standard / cinematic / compact_retry), model tier, output budget, prompt
> cap, per-call deadline, retry policy, and the degraded fallback are one
> decision (`CallPlan`). Field data drove it: 12–14k-char prompts, 110–260s
> routine calls, and "180s timeouts" that stalled 542s because the SDK
> retried internally — `max_retries=0` plus `run_with_deadline` (abandon the
> wait, close the HTTP session) makes a deadline real. A timeout earns
> exactly ONE compact retry built FRESH (scenario/tone, location, actor,
> declaration, dice, visible NPCs, state changes, short voice task) — never
> the original prompt; a second failure raises `GovernorDegraded` and the
> table picks: retry compact / switch to Human Keeper / minimal local
> outcome text / save and quit. Prompts are sectioned (system, scenario,
> scene, characters, items/objects, fronts/plot, adjudication, chronicle,
> commands/help, other) with caps from `settings.json -> latency`; over-cap
> prompts slim the room view then drop off-screen characters and
> fronts/plot. Output-length policy rides the prompt as a VOICE TASK line
> (routine 150–300, standard 300–500, complex 500–700, cinematic 700–900
> words). `--debug` dumps prompt + telemetry to `logs/prompt_debug.txt`;
> `--report` gains timeout rate, per-tier p50/p95, avg prompt chars and
> output tokens by tier, compact-retry and recovery success rates. Mock
> sessions keep the legacy prompt path; the governed path is forced in
> tests via `keeper._force_governor`. The first live benchmark caught two
> real defects the suites had not: k2.6 starved at 2048/3072-token budgets
> (EMPTY, finish_reason=length — budgets are now 4096/5120/8192/2048), and
> the "compact" retry was 90% system prompt (now a ~600-char compact
> system prompt; EMPTY compact responses degrade to the menu instead of
> escaping). +28 deterministic checks (baseline now 421).

> **v2.8.1.5 — Human Keeper Provider.** A human host can narrate instead of
> an AI API: `llm.provider: "human"` or `--human-keeper`. The engine still
> owns truth (dice, adjudication, items, movement, NPC condition, object
> state); the human replaces only the voice. `src/human_keeper.py` keeps
> packet generation (`build_human_keeper_packet` / `render_human_keeper_packet`)
> separate from terminal I/O (`HumanKeeperClient.narrate`) so a future remote
> service can ship the packet to a host client. The packet carries scenario/
> scene, acting character, declarations, adjudication + dice, mechanical
> outcomes, engine-applied world changes, the deterministic room view,
> first-visit status, and seven restrictions. Input protocol: multiline
> public narration by default; `/private <char_id>`, `/public`, `/end`,
> `/cancel` (refunds the turn), `/skip`, `/help`. No API timeout, retry
> budget, token estimates, or cost accounting; human turns log separately to
> `logs/human_keeper.jsonl` (provider=human, elapsed_host_time,
> narration_chars, private_note_count). Narration-safety rules warn the host
> instead of hard-rejecting; `--debug` shows the packet, the parsed
> structure, and warnings. +35 deterministic checks (baseline now 393).

> **v2.8.1.3 — Adjudication semantics + latency stabilization.** Conditional
> and quoted threats are coercion (Intimidate), never committed fire — no
> ammunition, no combat until the player explicitly commits ("I shoot him").
> NPCs are not items: `npc_handling` (grab/drag/shove/march/restrain/force)
> with deterministic forced movement (NPC + player + scene + door state +
> movement events) on success and an explicit failure flag the narrator
> cannot override. Clarification anywhere in a compound pauses the WHOLE
> declaration. Later compound frames are conditional on earlier outcomes.
> Fire needs a canonical ignition source. Ordinary entry never grants a
> passive inspection (authored `entry_check` only). An output validator
> strict-retries narration that gives NPCs engine-less world changes.
> Budgets: default 5120 (5120/8192), per-call 180s timeout + one compact
> retry + consecutive-timeout circuit breaker (logged to llm_timing.jsonl),
> `--debug` prints the effective budget line, and heavy (k3) routing is
> reserved for CINEMATIC mode, Mythos/creature scenes, and front thresholds —
> threats and ordinary combat stay on the default model. Narration contract:
> only the new outcome, 250–500 words routine, no static re-description.
> Deterministic baseline: 358 engine checks + 222 adjudicator checks.

> **v2.8.1.2 — Keeper Adjudication Layer.** The engine, not the player,
> decides when a declaration needs dice. Declarations run through a local,
> deterministic pipeline (`src/adjudicator.py`): normalize → compound split →
> intent frames (`src/action_intent.py`) → scene-context target/instrument
> binding → skill scoring → roll / local / clarify / impossible / passthrough.
> Data-driven prototypes live in `data/action_prototypes.json` (20 types);
> natural skill aliases in `src/skill_graph.py`; execution in
> `src/action_resolver.py`. Confidence: act ≥ 0.65, clarify 0.45–0.65 (only
> with two concrete readings), passthrough below. Explicit `roll <skill>`
> remains an override, never a requirement. `--debug` prints every frame;
> normal play shows only the roll line. Corpus: `tests/action_phrases.jsonl`
> (162 phrases, 100% as expected) + `test_adjudicator.py` (206 checks).
> No extra LLM call anywhere in the pipeline.

> **v2.8.1.1 hotfix (field-test).** Command normalization (bare/numbered/aliased
> local commands, `read`, use-suggestions, no bare `None` in inventory, `exit`
> reserved for quitting), committed melee/social declarations roll before
> narration (hit/strike/tackle/slam/buttstock/pistol-whip/knockout → Fighting
> Brawl; demand/order/command/warn/gunpoint → Intimidate; idiom stoplist),
> nonlethal knockouts are engine truth, object attacks consume ammo, check
> jams, and break objects/exits deterministically, first-visit continuity
> state rides the prompt (`visits` map), items cannot drift through prose,
> narration may not recite verb lists, and the output budget is back to 8192
> (the 4096 default starved at the real 11.1k-char prompt — EMPTY after 90s,
> 185.5s turn total; 5120 stays a candidate only after repeated A/B proof).
> +33 deterministic checks. **P0 follow-up (same hotfix):** the `open door`
> registry crash (roster legacy string inventories) is fixed at both layers —
> reconciliation at `add_player`, crash-safe lookups, registry audit after
> take/drop/give/equip/open — and natural pickup aliases (`grab`, `pick up`,
> `pickup`, `collect`, `pocket`, `snatch`) plus `unlock`/`use ... on ...`
> forms resolve locally. **Movement-packet follow-up (same hotfix):** the
> Hallway/Study narration desync is closed — bare numbers select from menus,
> locked exits resolve key+move BEFORE the LLM is called, escalated entries
> carry a canonical packet (origin, destination, after-action location,
> blocking object, unlock result, destination room view, first-visit state)
> with explicit narration instructions, and `open door` from the far side of
> a doorway answers "already open, behind you". +29 more checks (baseline
> now 348).

---

## 1. What changed

v2.8.1 gives rooms deterministic truth and takes ordinary movement off the
API meter. Rooms have stable authored descriptions, `observe`/`look` and
pure movement declarations resolve locally with zero LLM calls, exits carry
state (open/closed/locked/blocked/hidden/destroyed, keys, door objects,
one-way), and the LLM is only called for entries that deserve drama (NPCs,
hazards, clue reveals, front/timeline events) — with the engine's move
already decided and re-asserted if the model disagrees.

Key behavioral changes:

- **New `src/room_view.py`** builds the deterministic room view (description,
  first-visit/revisit per character, object state, visible items, visible
  characters with readied markers, lighting, exits). Hidden items/objects/
  exits and clue data never leak — to screen or prompt.
- **`Location` schema extended:** `description`, `first_visit`, `revisit`,
  `details`, `lighting`, `tags` (all optional; old scenarios/saves unchanged).
- **Local commands:** `observe`, `look`, `look around`, `examine room` — no
  LLM, no turn consumed. (`exit` still quits the *game*; use `leave`/`go back`.)
- **Offline movement:** `go to`, `enter`, `head to`, `walk to`, `step into`,
  `leave`, `go back`, `return`, ... resolve locally with graph + exit-state
  validation. Invalid moves list the valid exits. Freeform non-movement text
  falls through to the normal Keeper path untouched.
- **Exit state:** `locked` exits unlock permanently with a carried key;
  `object_id` links keep door objects and exits consistent; `hidden` exits
  are invisible everywhere; `destroyed` exits are passable and flagged.
- **Hybrid escalation:** NPC presence, hazard/trap/SAN tags, visible-clue
  reveal (`"visible": true` — engine stamps `discovered_clues`), front
  triggers, and pinned timeline events route the entry to the LLM with a
  `MOVEMENT EVENTS` block; the engine owns the move.
- **Prompt integration:** `ROOM VIEW` block + visible-only `EXITS`; system
  prompt ROOM TRUTH section; readied-vs-carried distinction.
- **Scenario `placed_items`** place item instances in rooms/on NPCs at load.
- **Saves round-trip** authored location fields, connection state, visited
  rooms, and discovered clue ids.
- **New teaching scenario** `data/scenarios/five-minute-house/` and the
  worldbuilding guide `README-WORLDBUILDING.md` (pinned to the shipped file
  by a test so it cannot drift).

---

## 2. Files added or modified (v2.8.1)

### Added

- `src/room_view.py` — deterministic room view, exit rules, offline movement, escalation triggers
- `data/scenarios/five-minute-house/scenario.json` — teaching scenario
- `README-WORLDBUILDING.md` — worldbuilding guide for non-programmers
- `docs/v2.8.1-release-notes.md`

### Modified

- `src/__init__.py` — version stamp `2.8.1`
- `src/spatial.py` — Location gains `description`, `first_visit`, `revisit`, `details`, `lighting`, `tags`
- `src/keeper.py` — observe commands, offline movement, escalation re-assertion, `ROOM VIEW` prompt block, `placed_items`, visited/clue persistence
- `src/state.py` — save/load of new location fields, `visited`, `discovered_clues`
- `config/system-prompt.txt` — ROOM TRUTH section; MOVEMENT and exit-inventing narration lines updated
- `test_engine.py` — +50 deterministic v2.8.1 checks; v2.8.1 section cleans up its own saves
- `README.md`, `AGENTS.md`, `docs/HANDOFF.md`

(v2.8.0/v2.8.0.1 file history: see `docs/v2.8.0-release-notes.md` and
`docs/v2.8.0.1-testing-notes.md`.)

---

## 3. Test results

Run from the project root (`coc7-keeper/`):

```bat
py test_engine.py
py test_charcreate.py
py test_items.py
echo "quit" | py -m src.main --mock
```

Latest results:

```text
ALL TESTS PASSED (293-298 checks)            # test_engine.py  (see note below)
ALL CREATION TESTS PASSED (64 checks)        # test_charcreate.py
ALL ITEM TESTS PASSED (131 checks)           # test_items.py
```

**Why the engine check count moves between runs (245 vs 242 is NOT a regression).**
`test_engine.py` contains one probabilistic counter: in the dice section, the
`"d100 in range"` check only fires when a roll lands exactly on 1 or 100
(~2% chance per roll, 500 rolls). The suite therefore reports
`deterministic baseline + N`, where `N ~ Binomial(500, 0.02)` — mean 10,
standard deviation ~3. A 245-check handoff and a 242-check run were the same
v2.8.0 suite with 9 vs 6 edge hits. **The v2.8.1.7 deterministic baseline is
447 checks** (236 from v2.8.0 + 50 Room Truth + 33 hotfix + 17 P0 registry +
12 movement-packet + 10 routing/stabilization + 35 human Keeper + 28 Latency
Governor + 26 P0-1.7, all deterministic); observed totals like 293 (+7),
358 (+10), 431 (+10), and 459 (+12) match the corresponding baseline
exactly. Compare suites by the deterministic baseline, not the printed
total.

Mock mode starts cleanly, displays `v2.8.1 [MOCK MODE]`, saves and exits when given `quit`.
The five-minute-house scripted session (help → observe → move → locked refusal
→ key → NPC escalation → save/load) is pinned in `test_engine.py`.

---

## 4. Known limitations

- **Clue triggers are still informational.** `skill`/`difficulty` on clues are stored for v2.8.4; only the `"visible": true` room-reveal hook is live (the engine stamps `discovered_clues` on first entry and escalates the narration).
- **Containers are shallow.** `open` toggles state and locked/key works, but nested containers, lockpicking skill rolls, and trapped containers are future work.
- **Facts and knowledge are not yet separated.** Per-character knowledge, beliefs, and lies arrive in v2.8.3.
- **NPCs have no persistent director brain.** Objectives, awareness, routes, and relationships arrive in v2.8.5.
- **Some escalation triggers are hooks only.** Active combat, major object-state reveals, and private perception are documented hooks in `room_view.escalation_triggers` for their roadmap phases (v2.8.2+).
- **`exit` quits the game, not the room.** Movement uses `leave`, `go back`, or `return` (the quit check predates offline movement and wins at the prompt).
- **Scenery drift is mitigated, not eliminated.** Authored rooms and the ROOM VIEW prompt block keep stable rooms stable, but unauthored rooms still rely on the model; a rendered-details ledger remains future work.

---

## 5. Save compatibility notes

- v2.7.x and v2.7.6.1 saves load without modification and migrate on first load:
  - legacy `weapon` dicts become item instances,
  - string inventory entries become item instances,
  - `weapon_instances` become carried instances,
  - the first matching inventory entry is folded into the equipped instance; additional same-name entries remain separate instances.
- v2.8.0 saves load cleanly; the transient `weapon` field is no longer serialized, preventing duplicate instance creation on every load.
- v2.8.1 saves add `visited`, `discovered_clues`, and the authored location fields; older saves load with empty defaults (every room is a first visit again, unlocked exits revert to scenario state — scenario-authored `state` is the fallback).
- **No save migration script is required.** Loading an old save automatically bridges it into the new format.

---

## 6. Exact next tasks for v2.8.2 — Event and Trigger Engine

v2.8.1 is now considered proven and ready for v2.8.2 planning.

1. **Noise → front clocks.** Gunshots, forced doors, and screaming advance scenario noise clocks deterministically; thresholds advance fronts.
2. **Fumble SAN checks.** A fumbled combat roll in a Mythos-tagged scene triggers the engine's SAN ladder automatically.
3. **Timeline dispatch.** Timeline events fire on their turn through an engine event, not just prompt presence.
4. **Object-state mutation events.** Combat/force results mutate door/object state through an engine event (`destroyed` doors stay destroyed in map AND prompt).
5. **Wire the remaining escalation hooks** (active combat, major object-state reveal) into `room_view.escalation_triggers`.
6. **Update `test_engine.py`** with event/trigger regression tests.

---

## 7. Notes for the next AI coding session

- **Always run from the project root** (`coc7-keeper/`). Relative paths resolve from there.
- **Use `py`, not `python`**, in this Windows Git Bash environment.
- **Offline test suite is the gate:** `py test_engine.py && py test_charcreate.py && py test_items.py` must stay green.
- **The Truth Firewall is central.** Any future feature that lets the model propose mechanical state must go through `StateDeltaValidator` or an engine-owned event, not direct mutation.
- **Do not expand `ENGINE_OWNED_CHARACTER_FIELDS` without a roadmap reason.** The list in `src/state_validator.py` is the contract.
- **The item registry is the source of truth for physical things.** `Character.weapon` is a transient view; canonical ammo/condition live in `ItemInstance`.
- **`src/room_view.py` is the source of truth for space.** Room views, exits, and movement come from there — never re-derive them ad hoc in keeper code, and never let a hidden item/exit reach a render or a prompt.
- **Engine-resolved moves are re-asserted after `state_delta` application.** If you add new local-resolution paths, route them through `_engine_moved` so the model cannot out-vote the engine.
- **When creating `Character(..., weapon=...)`, the instance is created in the runtime registry.** `CoCKeeper.__init__` preserves pre-existing instances by copying the runtime registry before replacing it.
- **If you add new proposal fields**, add them to `PROPOSAL_TOP_LEVEL_FIELDS` in `state_validator.py` and validate their shape before they reach engine code.
- **Read `docs/v2.8-offload-roadmap.md` and `docs/CoC7-Keeper-Persistent-Campaign-Engine-Roadmap.pdf`** before starting v2.8.2.
- **Keep changes minimal and test-backed.** This codebase values regression tests over comments.
