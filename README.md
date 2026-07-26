# CoC 7e LLM Keeper — v2.8.1 (Room Truth and Offline Movement, July 2026)

Call of Cthulhu 7th Edition Keeper powered by a swappable LLM backend, with
local-folder or Google Docs chronicle persistence. Dice, combat math, sanity
math, and spatial logic run locally and deterministically; the LLM only
narrates.

**Default backend: Kimi (Moonshot AI).** Any OpenAI-compatible API works too —
DeepSeek, OpenAI, OpenRouter, Groq, Together, xAI, Ollama, LM Studio — plus
Google Gemini via its native SDK. See **docs/LLM-PROVIDERS.md** for setup and
the full compatibility table.

## Create an investigator (the 60-second version)

```bat
py -m src.main --new-character
```

(`py` = Windows launcher; on macOS/Linux use `python -m src.main --new-character`.)

The wizard runs entirely in the terminal and enforces the 7e Investigator
Handbook for you: name and age (age table applied automatically), pick a
characteristic method (dice / Quick-Fire / Point-Buy), pick an occupation,
spend skill points (occupation points + INT x2 interests), set Credit Rating
in range, pick a weapon. Done — it prints a summary and saves.

- **Personal-interest skills** are the 7e "hobbies outside your day job"
  rule. Standard allocation: you get INT x2 extra points for any non-Mythos
  skill. Quick Skills: you pick 4 skills, each gets +20 over its base value.
  The picker is free-text — type `list` to see every skill; blank, Mythos,
  or misspelled answers are re-asked, never silently applied.
- **Saved to `data/investigators.json`.** Run the wizard once per investigator;
  re-using a name replaces that investigator instead of duplicating it.
- **They join the game through the lobby** — a fresh campaign (no save file)
  opens the character selection screen; pick by number, take `all`, or fall
  back to the pregens. Mid-campaign already? The save owns the party, so the
  lobby only appears on fresh starts; `--reset` wipes the save to re-pick.
- **Nothing saves until the wizard finishes** — Ctrl+C any time to bail.
- **`error: unrecognized arguments: --new-character`?** You're running a
  pre-v2.4 `src/main.py` (check your folder — an old `coc7-keeper-v2.3.x`
  copy). Replace it with this bundle's `src/main.py`.
- Occupations live in `data/occupations.json` — edit or add your own.

## What changed in v2.8.1

**Room Truth and Offline Movement.** Rooms stop redecorating themselves, and
ordinary walking stops costing API calls. The engine owns truth; the model
owns voice.

- **Authored room descriptions.** Scenarios can give each room `description`,
  `first_visit`, `revisit`, `details`, `lighting`, and `tags`. Old scenarios
  and saves work unchanged.
- **`observe` / `look` / `look around`** print the deterministic room view —
  no LLM call, no turn consumed. Hidden items, hidden exits, and locked
  contents never leak.
- **Offline movement.** `go to`, `enter`, `head to`, `walk to`, `step into`,
  `leave`, `go back`, `return` resolve locally: graph-validated, exit-state
  aware, zero API calls, no turn consumed. Bad moves list the valid exits.
  (Note: `exit` still quits the *game* — use `leave` or `go back`.)
- **Exit state.** Exits can be `open`, `closed`, `locked` (opens with the
  right key, permanently), `blocked`, `hidden`, or `destroyed`, and can be
  linked to door objects so map and scenery always agree.
- **Dramatic entries still use the LLM.** Walking into an NPC's room, a
  hazard, or a clue reveal hands the moment to the narrator — after the
  engine has already decided what happened.
- **Build your own:** `README-WORLDBUILDING.md` is a plain-English guide to
  scenarios, items, locked doors, clues, and NPCs, with a playable
  five-minute example (`data/scenarios/five-minute-house/`).

v2.8.0 (item/object registry) and the v2.8.0.1 hardening pass are covered in
`docs/v2.8.0-release-notes.md` and `docs/v2.8.0.1-testing-notes.md`.

## What changed in v2.7.6

**The spoiler channel.** Field log: a table in debug mode saw
`[PRIVATE — Elias Lusk]` print the NPC's *scheme* mid-scene. The print was
unconditional — every table was one suspicious NPC away from spoilers. Now:

- **A player's own thoughts** always reach their screen (`[PRIVATE — name]`).
- **An NPC's thoughts** are keeper-view: they print only when `llm.debug`
  is on, tagged `[KEEPER — name]` so nobody mistakes them for player mail.

Plus the strategic deliverable: **`docs/v2.8-offload-roadmap.md`** — what
moves from the model into Python next (zero-LLM movement turns, static room
descriptions, object state, facts ledger, noise→front clocks, reasoning
budget), ranked against the measured cost anatomy: input ≈ 10% of a turn,
hidden reasoning ≈ 90%. The engine owns truth; the model owns voice.

## What changed in v2.7.5

**Force verbs by proximity + the cost meter.** Field log: *"walk up to the
door and kick the door in"* — the net knew `kick down`, not `kick … in`, so
zero dice again, and the model did its setup beat while skipping the
mandatory roll request. Fixed both ends:

- **Force verbs match by object proximity** — `kick`, `break`, `ram`,
  `shoulder`, `barge`, `burst`, `force` now roll when a breakable object is
  in the phrase (door, window, gate…), and return nothing when there isn't
  one: *"break the news to her"* is not an assault. Attack verbs keep their
  nearest-NPC fallback; force verbs never get one.
- **The prompt now says it plainly**: narrating a setup without a matching
  `dice_requests` entry in the SAME response is a defect, not pacing.

And the meter you asked for — **what does this game actually cost?**

- Every API attempt logs the provider's own `usage` (`pt`/`ct`/`cached`
  tokens) into `logs/llm_timing.jsonl`; `--debug` shows `tok=2100+780`
  live per call.
- `python test_latency.py --report` now prints a **tokens & estimated cost**
  block per model, priced from the editable `pricing` table in
  `config/settings.json` (shipped with Moonshot's published rates:
  k2.6 $0.95/$0.16/$4.00, k3 $3.00/$0.30/$15.00 per 1M
  input/cached-input/output). Older log rows without usage are counted,
  never costed — no invented numbers.

## What changed in v2.7.4

**The flaky-die patch.** Field report: `test_engine.py` failed on the
maintainer's machine at "a shotgun fires on Firearms_Rifle_Shotgun" while
passing everywhere else. Root cause: the check fired a **real d100**, and a
roll of 96+ (the 12-gauge's malfunction threshold — a 4% chance per shot)
made `resolve_attack` return early *without* the target/level keys the
assertion wanted. Two fixes:

- **The jam path no longer hides the attempt.** A malfunction now records
  roll, target, and level alongside the jam note — the DICE RESULTS block
  and the table display stay truthful on a bad roll:
  `» Tyler Moss — Firearms Rifle Shotgun 50%: rolled 97 — Fumble — WEAPON JAMS`
- **Skill-selection tests are deterministic.** Checks that pin *which* skill
  rolls now stub the dice instead of gambling on them. Suite stress-tested
  with five consecutive green runs.

## What changed in v2.7.3

**Weapon-skill truth + the equipment menu.** Two channels, cleanly split:
narrative stays free text, gear stays deterministic.

- **The shotgun bug is dead.** The combat engine keyed every firearm attack
  off `Firearms_Handgun` — a shotgunner with no handgun training fired
  their 12-gauge at the 20% base. Now the weapon in hand decides:
  shotguns roll `Firearms_Rifle_Shotgun` (7e base 25), handguns
  `Firearms_Handgun` (base 20).
- **Inventory is real.** Every character carries an `inventory` (auto-seeded
  from their equipped weapon — old saves migrate themselves). Manage it at
  any prompt with system commands that never reach the model:
  - `inventory` (or `inv`) — what you carry and what's in hand
  - `equip <name>` — ready something you carry (`equip shotgun` finds the
    12-gauge by partial name)
  - `unequip` — put it away
- **Weapons are instances, not catalog entries.** Two investigators with
  12-gauges no longer share one ammo count; the creation wizard, pregens,
  and `equip` all hand out independent copies. Eleanor Vance now actually
  carries the .32 revolver her Firearms_Handgun 50 sheet always implied.

No `{SHOOT_GUN}` tags, ever: declarations stay prose, the preroll net and
`dice_requests` keep the dice honest, and gear lives in the system channel
where it belongs.

## What changed in v2.7.2

**The commitment rule** — from the first post-v2.7.1 field log. A player
declared *"attempt to breach the door with the shotgun"* and got a full turn
of atmospheric setup ending *"The trigger waits."* They confirmed —
*"blast the door."* — and got a **second** setup beat ending *"You pull the
trigger. What do you do?"* Two declarations, zero dice, no resolution. When
you tell a DM you commit to an action, it happens. Now:

- **The combat verb net is wider** — `blast`, `breach`, `aim`, `smash`,
  `fire`, `kick down`, `break down`, `force open` join the list.
- **Objects are valid targets.** Shooting a padlock or blasting a door rolls
  the firearm skill (shotgun vs handgun, from the weapon in hand); forcing
  something barehanded rolls raw STR. Named NPCs still route through the
  full combat engine — an object phrase only wins when nobody is named.
  Display: `» Tyler Moss — Firearms Rifle Shotgun 50%: rolled 12 — Extreme   (object: door)`.
- **The commitment rule is in the system prompt**: a committed action
  resolves in the SAME narration — the act, the outcome from DICE RESULTS,
  and the side details. Never two setup beats in a row; if last turn was a
  wind-up and the player confirms, the thing has happened.

## What changed in v2.7.1

**The aura-farming patch** — from the first tallow-chapel field log. A player
declared *"I sneak up to the side and attempt to climb onto the balcony"* and
the turn resolved with **zero dice rolled**: the preroll net only knew
search/listen/combat verbs, so the model quietly decided the climb itself.
Worse, when the model later asked for "Locksmith (target 60)" — in prose,
where the engine can't see it — the player typed `roll!` and the model, again
holding no dice, improvised an action-man combat roll into the room. Three
hard rules now:

- **Every risky declaration meets the dice BEFORE the model sees the turn.**
  The preroll net now covers Stealth, Climb, Jump, Swim, Throw, Locksmith,
  Sleight of Hand, Disguise, Dodge, Intimidate, Charm, Fast Talk, Persuade,
  First Aid, Track, Drive Auto, and Library Use — with 7e base values when
  the investigator lacks the skill.
- **Every engine roll is shown at the table** before the narration lands:
  `» Jane Doe — Locksmith 60%: rolled 34 — Regular success`.
- **The model requests rolls only through `dice_requests`** (never in prose),
  and may not narrate the outcome of a risky action that has no dice result.
  Requested rolls queue as **pending rolls**: answer with `roll!` (or `yes`,
  `go ahead`…) and the engine rolls for real; declare a different action and
  the stale request is abandoned; they survive quit/resume.
- **Options no longer carry risk labels.** The "(safe, slow) / (risky,
  rewarding) / (advances the danger)" tags are banned — judging risk is the
  players' job.
- **Default token budget back to 8192.** The same log showed k2.6's 4096
  initial calls coming back empty (`finish_reason=length`) on 2 of 5 rich
  turns, each costing a 50–90s retry; 8192 succeeded every time. The v2.6.0
  sprawl concern belonged to k3, which has its own `max_output_tokens_heavy`.

## What changed in v2.7

**The lobby update — scenario select, party select, local chronicle, and a
prompt diet.** Driven by the July 19-20 timing log (k2.6 55.4s vs k3 371.0s
for the same smoke turn — the heavy tier's 4096-token first attempt burned
its budget on hidden reasoning and came back as 140 chars of invalid JSON).

- **Scenario selection screen on startup.** `python -m src.main` with no
  `--scenario` now opens a numbered menu of every folder in
  `data/scenarios/` — title, era, expected sessions, a one-line hook, and a
  `[save: turn N]` marker where a campaign is resumable. Two scenarios ship:
  **The Haunting** (the classic) and **The Tallow Chapel** (new, ~2
  sessions). `--scenario <path>` skips the menu exactly as before, and
  headless runs (redirected stdin) never prompt — they take the legacy
  default. Disable with `"game": {"startup_menu": false}`.
- **Character selection screen before going hot.** Fresh campaigns list the
  roster (name — occupation, age, top 3 skills) and take `1,3` multi-picks,
  `all`, `pregens`, or `new` (runs the 7e wizard mid-menu and re-lists the
  grown roster). Resumed saves skip it — the save owns the party.
- **Local folder chronicle** — the offline Google Docs. Same interface
  (`append`/`flush`/`get_last_paragraphs`), same entry format, zero network
  calls and no google dependencies: one markdown file per scenario under
  `chronicle/`. Select with `"chronicle": {"backend": "local"}` (now the
  shipped default); `"google"` keeps the old Docs path; `"off"` disables.
  Configs without a `chronicle` section behave exactly as pre-v2.7.
- **Latency knobs.** `llm.compact_prompt: true` (shipped default) strips
  pretty-printing from every JSON block in the turn prompt — roughly 10%
  fewer prompt characters per turn, same content. `llm.extra_body` merges
  provider-specific switches (e.g. reasoning effort) into every API call
  with no code change. `llm.max_output_tokens_heavy: 8192` starts the heavy
  tier's retry ladder where the field data says k3 actually succeeds,
  skipping a paid 175-second known-too-short first attempt.
- **Shipped defaults now match the field findings**: `heavy_escalation:
  "combat"` (solo k3 turns measured 199-371s; combat-only escalation keeps
  set pieces on k3 and exploration on k2.6). Code defaults are unchanged, so
  old configs without these keys behave exactly as before.

## What changed in v2.6

**Thematic loading presence + token-budget diet** — field benchmark drove both:

- Long API calls now paint a self-erasing line while you wait —
  `|  34.0s — Something shifts behind the walls...` — spinner, live timer,
  rotating Keeper flavor. Appears only after 1.5s (fast calls stay clean),
  never in `--mock`, never when output is redirected, and it cannot crash a
  session. Disable with `"llm": {"loading_bar": false}`.
- `llm.max_output_tokens` default 8192 -> **4096**. Benchmark turns returned
  ~1.6-3.5k chars (~400-900 visible tokens); the 8k budget mostly invited
  hidden-reasoning sprawl. If a call does truncate (`finish_reason=length`),
  the existing ladder still retries at 2x/4x — same ceiling as before,
  roughly half the routine thinking time.
- Field numbers (your line, July 19): **k2.6 47.5s vs k3 210.8s** for equal
  narration — 4.4x faster on the default tier. With
  `heavy_escalation: "combat"` (v2.5.1), exploration turns cost a quarter of
  what they did. The log also caught a provider hiccup (three instant
  empty/length failures on one turn) — the ladder refunded it cleanly;
  transient, not a bug.

## What changed in v2.5

**LLM timing diagnostics** — answer "why was that turn slow/expensive?" with
data instead of vibes:

- Every API attempt is logged to `logs/llm_timing.jsonl` (model, tier,
  attempt, token budget, prompt/response size, seconds, ok/error). Always on,
  zero config, and it can never crash a session.
- `--debug` (`py -m src.main --debug`) echoes the same lines live while you
  play. Or set `"llm": {"debug": true}` in config/settings.json.
- `python test_latency.py` — live benchmark: one paid call each on the
  default (k2.6) and heavy (k3) tiers, timed.
- `python test_latency.py --report` — free, no API: aggregates the log
  (per-model avg/max seconds, failure counts, retry spread, slowest calls).

Why turns were taking 3-4 minutes: one turn is up to THREE paid generations —
initial (8k tokens), strict-retry (16k), final-retry (32k) — whenever the
model returns malformed JSON, and INDIVIDUAL-mode turns route to the slower
k3. Retry rows in the report are the multiplier; the knobs are
`llm.max_output_tokens` and the system prompt's length. The
character-creation wizard makes no API calls — it never appears in this log.

**v2.5.1 — heavy-tier escalation policy (the solo k3 fix).** First field
report from the timing log: every solo turn routed to kimi-k3 (86s and 199s
for routine exploration). Cause: SQUAD mode needs 3+ investigators, so a
one-character party is always INDIVIDUAL — and INDIVIDUAL always escalated
to heavy. New knob in `config/settings.json`:

```json
"llm": { "heavy_escalation": "individual" }
```

- `"individual"` — default, old behavior (every INDIVIDUAL turn uses heavy)
- `"combat"` — heavy only when combat keywords are declared; exploration
  stays on the cheap tier. **Recommended for solo play.**
- `"never"` — always the default model, maximum savings.

Zero-patch alternative: point `"llm": {"models": {"heavy": "kimi-k2.6"}}` and
heavy turns stop touching k3 at all. Measure your own line first with
`python test_latency.py` (one paid call per tier).

**v2.5.2:** README only — consolidated command reference: first-time setup
vs. updating from repeat downloads, every command in order.

## What changed in v2.4

**Character creation wizard** (`python -m src.main --new-character`) that
enforces the actual 7th-edition Investigator Handbook rules:

- Characteristics: dice (3D6x5 / 2D6+6x5), Quick-Fire array (40/50/50/50/60/60/70/80),
  or Point-Buy (460, 15-90, INT/SIZ min 40)
- Full age table: 15-19 (-5 STR-or-SIZ, -5 EDU, Luck best-of-two), 20s-30s
  (+1 EDU check), 40s-80s physical/APP deductions, EDU improvement checks
  (1D100 > EDU -> +1D10, max 99), MOV penalties by decade
- 15 sourced occupations (data/occupations.json — editable) with real skill-point
  formulas (EDU*4, EDU*2+DEX*2|STR*2, ...) and Credit Rating ranges
- Standard allocation (occupation points + INT x2 interests) or Quick Skills
  (70/60/60/50/50/50/40/40/40 + four interests at +20)
- Enforcement: no Cthulhu Mythos at creation, occupation points to occupation
  skills only, Credit Rating must land in range, optional starting-skill cap
  (default 75 — game.creation_skill_cap)
- Saves to data/investigators.json; the game loads your roster automatically
  on a fresh campaign (no save file present)

**v2.4.1 field fix:** a stale pre-v2.4 `src/main.py` in the field rejected
`--new-character` ("unrecognized arguments"). The CLI parser is now extracted
(`build_parser()`) and pinned by regression tests in `test_engine.py` — a
stale or reverted parser now fails the offline suite instead of reaching
the table.

**v2.4.2 field fix:** picking a multi-word skill from an occupation choice
group (Soldier's "First Aid / Mechanical Repair / Other Language", Criminal's
choose-4 group, "Fast Talk", ...) crashed the wizard with
`ValueError: list.remove(x): x not in list` — the chooser returned
canonicalized names while the option pool held raw ones. Fixed in
`resolve_occupation_skills` and pinned in `test_charcreate.py`, including a
scripted end-to-end Soldier run.

**v2.4.3 field fix:** the quick-skills interest phase accepted blank answers —
four Enters stacked +20 each into a phantom empty-named skill (saved to the
roster at 75%), and a Mythos answer silently burned one of the four slots.
The picker now shows examples, accepts `list`, and re-prompts on
blank/Mythos/unknown input without losing slots. Pinned in
`test_charcreate.py`. Also clarified `test_kimi.py` output: the smoke test
spends default-tier (k2.6) tokens only, never heavy/k3.

## What changed in v2.3

1. **Provider layer** (`src/llm_client.py`): one-line provider switch in
   `config/settings.json`. Keys resolve from `config/api-key.json` or env vars.
2. **Kimi API support**: `provider: "kimi"` (intl, api.moonshot.ai) or
   `"kimi-cn"` (China, api.moonshot.cn). Ships configured for `kimi-k2.6`
   (default) + `kimi-k3` (heavy, 1M context).
3. **DeepSeek support** with the current `deepseek-v4-flash/-pro` IDs (the old
   aliases die 2026-07-24).
4. Client auto-degrades when a model rejects `temperature` or JSON mode.
5. `test_kimi.py` live smoke test; provider layer covered in `test_engine.py`.
6. `docs/v3.0-architecture.md` — the multi-player/multi-scene roadmap proposal.
7. **v2.3.1/2/3/4 field fixes:** malformed `api-key.json` (unquoted key) gives a
   human-readable error; provider tests are isolated from your real key file;
   the response parser repairs classic almost-JSON (raw newlines in strings,
   trailing commas, Python literals, unclosed fences); **v2.3.4:** three-strike
   retry with growing token budgets (8192 default, retries at 2x/4x), empty
   responses detected with `finish_reason` surfaced, per-attempt raw dumps in
   `logs/llm_raw_*.txt`, and an LLM failure no longer crashes the session —
   the turn is refunded and you simply re-declare. Reminder:
   `pip install -r requirements.txt` is mandatory (`openai` package).

## v2.2 fixes (kept from the previous build)

- Model names updated off the retired gemini-1.5-* series; SDK migrated to
  `google-genai`; scenario loader no longer crashes on nested `characteristics`;
  `sanity.py` and `state.py` implemented; combat NameError and negative damage
  bonus fixed; rulebook tens-die bonus/penalty + 96+ fumble threshold; MOV 9
  branch fixed; SQUAD mode restored; `--mock` offline mode; graceful chronicle
  fallback. Details in git history / the v2.2 bundle.

## Command reference

Windows: `py` and `python` are interchangeable in every command below.
Always run from the project root (the folder containing `config/`).

### A. First-time setup (run once, in this order)

```bat
cd coc7-keeper                      :: 1. open a terminal in the extracted project root
python -m venv venv                 :: 2. create the virtual environment
venv\Scripts\activate               :: 3. activate it (macOS/Linux: source venv/bin/activate)
pip install -r requirements.txt     :: 4. install dependencies
notepad config\api-key.json         :: 5. create your key file (shape below)
python test_engine.py               :: 6. offline engine tests — no key, no tokens
python test_charcreate.py           :: 7. offline creation-wizard tests
python -m src.main --mock           :: 8. full game loop offline — type actions, 'quit' to save
python -m src.main --new-character  :: 9. build your investigator(s)
python test_kimi.py                 :: 10. live smoke test (first paid call, pennies)
python test_latency.py              :: 11. OPTIONAL: 2 paid calls, k2.6 vs k3 timing
python -m src.main                  :: 12. play — your campaign starts
```

Step 5, `config/api-key.json` — key from platform.moonshot.ai -> API Keys:

```json
{
  "kimi_api_key": "sk-PASTE-YOURS-HERE",
  "gemini_api_key": "",
  "deepseek_api_key": "",
  "openai_api_key": ""
}
```

Using Gemini or DeepSeek instead of Kimi? One-line change — docs/LLM-PROVIDERS.md.
The Google Docs chronicle is optional: `python test_docs.py` checks it, and
`"google_docs": {"enabled": false}` disables it entirely.

### B. Updating (every repeat download, in this order)

The zip never contains `config/api-key.json`, `saves/`, or `venv/` — your key,
campaign progress, and created investigators all survive the merge.

```bat
:: 1. extract the new zip; drag its inner "coc7-keeper" folder over yours,
::    Replace/Merge all files when prompted
cd coc7-keeper
venv\Scripts\activate               :: 2. activate the existing venv
python test_engine.py               :: 3. both suites green = update landed clean
python test_charcreate.py
python -m src.main                  :: 4. play — the save resumes automatically
```

Only re-run `pip install -r requirements.txt` if the changelog says the
requirements changed. Once the new version passes its tests, delete the OLD
`coc7-keeper-v2.x.x` folder — two copies on disk is how stale-file bugs happen.

### C. Everyday commands

```bat
python -m src.main                  :: lobby -> pick scenario -> pick party -> play
python -m src.main --scenario data/scenarios/tallow-chapel  :: skip the scenario menu
python -m src.main --new-character  :: add an investigator (same name = replace)
python -m src.main --debug          :: play with live per-call LLM timing on screen
python -m src.main --mock           :: play offline — no key, no tokens
python -m src.main --reset          :: wipe the save and start the campaign over
python test_latency.py --report     :: free analysis of logs/llm_timing.jsonl
python test_latency.py              :: paid benchmark: one k2.6 call, one k3 call
python test_latency.py --ab         :: paid A/B: output budgets 8192 vs 4096/6144
python test_docs.py                 :: Google Docs chronicle check (optional)
python src/main.py ...              :: alternate invocation — identical behavior
```

In-game (typed at a player's prompt — all free except narration turns):

```text
help                    every command
observe / look          see the room again (no LLM, no turn used)
go to / enter <room>    move (no LLM on ordinary rooms; bad moves list exits)
leave / go back         retrace your last step  (NB: 'exit' quits the game)
inventory, equip, unequip, take, drop, give, reload, open, look at, use
quit / save             save and leave
```

## Adding your own scenario

**Start here: `README-WORLDBUILDING.md`** — a plain-English worldbuilding kit
covering rooms, exits, locked doors and keys, items, clues, NPCs, fronts, and
a complete five-minute example you can copy and play offline.

One folder per scenario under `data/scenarios/`, holding a single
`scenario.json`. It appears in the startup menu automatically — with a
`[save: turn N]` marker once a campaign exists. Schema, using
`data/scenarios/tallow-chapel/scenario.json` as the reference:

| key | required | meaning |
| --- | --- | --- |
| `id` | yes | unique slug — save files live at `saves/<id>/` |
| `title` | yes | shown in the lobby menu |
| `era` | yes | shown in the lobby menu |
| `expected_sessions` | yes | shown in the lobby menu |
| `description` | no | one-line hook under the menu entry |
| `starting_location` | yes | must be a key of `locations` |
| `locations` | yes | name, connections (type/state/key_id/object_id), sound_propagation, line_of_sight, occupants; v2.8.1 adds description, first_visit, revisit, details, lighting, tags |
| `placed_items` | no | item instances placed in rooms or on NPCs at load |
| `objects` | no | world objects: doors/containers with state and locked/key_id |
| `fronts` | no | countdown clocks: name, clock, max, triggers |
| `npcs` | no | characteristics block, skills, hp/san, weapon dict, location, attitude |
| `clues` | no | id, name, location, skill, difficulty, type, plot_point, visible |
| `timeline` | no | scheduled events: turn, event |

All shipped scenarios are validated by `test_engine.py` (loads cleanly,
starting location mapped, ids unique) — your homebrew gets the same check
the moment it sits in the folder, so run the suite after editing.

## Notes

- Always run from the project root so `config/` and `saves/` resolve.
- Chronicle backends: `"chronicle": {"backend": "local"}` (default — markdown
  files under `chronicle/`), `"google"` (the Docs integration; also set
  `google_docs.enabled: true`), or `"off"`.
- The v3.0 multiplayer roadmap is in docs/v3.0-architecture.md — implement it
  only after v2.7 passes field testing.
