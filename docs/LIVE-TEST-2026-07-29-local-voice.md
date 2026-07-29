# DOC — LIVE TEST LOG: local voice + untouched-NPC packet (2026-07-29)

**This file is the designated DOC log for the live acceptance run of
2026-07-29.** It contains the COMPLETE transcripts of both live sessions
(`--debug`, real Kimi API, provider `kimi`, model `kimi-k2.6`) plus the
verdicts against the four required behaviors. Raw captures also live in
the gitignored `logs/` folder (`live_session_A.txt`, `live_session_B.txt`).

Scenario: `testing-hall` (fresh `--reset` campaign each session).
Investigator: Jess Harlow (solo).
Note: `�` in the transcripts is a Windows console codepage artifact in the
redirected capture (em-dash/box glyphs), not game text.

---

## VERDICTS

| # | Required behavior | Result |
|---|-------------------|--------|
| 1 | Voiceless fallback reads as Keeper sentences (hit/miss/verdict/wound band), never a bare dice dump | **PASS** (Session A) |
| 2 | Throw at one NPC while another watches: no invented injuries on the bystander | **PASS** (Session B, throw turn + 2 follow-up combat turns) |
| 3 | `throw knife at guman` → menu → NO alert lines → `2` → roll → alerts only after the resolved round | **FAIL** — alert lines fire immediately after the menu, BEFORE the `2` answer and the roll (reproduces in `--mock`, so it is deterministic engine behavior, not LLM noise) |
| 4 | Entry narration still mentions the Range Door without going voiceless | **PASS** — but the initial entry narration was rejected once and recovered via the compact validation retry (5.7s, one extra call) |

Incidental observations:

- Two of four live narrated turns needed the compact `narration_validation_retry`
  (entry turn, punch turn, shoot turn — 3 of 4 actually; all recovered, none
  went voiceless). The retry path works, but the initial-call rejection rate
  on this scenario is high.
- One `[STATE REJECTED]`: the model tried to write `state_delta.squads`
  (unapproved top-level field). StateDeltaValidator rejected it; engine
  truth untouched. Firewall working as designed.
- Token cost of Session B: 4 initial calls + 3 compact validation retries
  (k2.6, ~4k prompt tokens each). Session A: ~0 (0.05s deadlines).

---

## SESSION A — forced voiceless turn (governor deadlines pinned to 0.05s; settings.json backed up before and restored after)

Input script: `take revolver` / `take range key` / `equip revolver` /
`unlock door with key` / `enter short range` / `3` / `shoot gunman` / `3` / `quit`

```
[llm config] path=config/settings.json default_budget=5120 heavy_budget=8192 override_budget=None effective_default=5120 effective_heavy=8192
  [Registry audit: roster reconciliation — removed 1 unresolved item reference(s).]
[Fresh campaign: 1 investigator(s) ready]

============================================================
 CALL OF CTHULHU 7th — LLM KEEPER  v2.8.1
============================================================
Scenario: testing-hall | Turn: 0 | Scene: th_hall
Type 'quit' to save and exit. Gear commands: inventory, equip, unequip, take, drop, give, reload, open, look at, use. 'help' lists all commands.

Jess harlow (player1) [Testing Hall]:   [Jess harlow takes the .38 Revolver.]
Jess harlow (player1) [Testing Hall]:   [Jess harlow takes the Range Key.]
Jess harlow (player1) [Testing Hall]:   [Jess harlow readies the .38 Revolver.]
Jess harlow (player1) [Testing Hall]:   [Jess harlow opens the Range Door.]
Jess harlow (player1) [Testing Hall]:   [You catch The Brawler unaware — you have the drop on them.]
  [You catch The Gunman unaware — you have the drop on them.]
  [Jess harlow -> Short Range — the moment calls for the Keeper.]
[governor] tier=standard model=default budget=5120 timeout=0s dynamic=4825ch system=11488ch total=16313ch cap=17000ch trimmed=- reasons=squad routine;escalation:npc:The Brawler;escalation:npc:The Gunman;escalation overrides minimal

[Querying kimi (kimi-k2.6) — governor: standard tier, 0s deadline...]
[llm    0.2s] kimi-k2.6 initial budget=5120 prompt=16313ch resp=0ch -> FAIL (timeout)
[LLM timed out after 0.05s. Compact retry (16313 -> 1617 chars)...]
[llm    0.2s] kimi-k2.6 initial-compact-retry budget=4096 prompt=1617ch resp=0ch -> FAIL (timeout)

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
The Keeper fell silent twice (provider timeout). Your turn is NOT consumed yet.
(initial call and compact retry both timed out)
Choose how to continue:
  1. retry compact
  2. switch to the Human Keeper (a human host narrates)
  3. use minimal local outcome text (no narration)
  4. save and quit
degraded [1-4]: 3
============================================================
(The Keeper is voiceless — the engine reports plainly.)
You are in the Short Range.
A padded room the size of a cellar. Two figures wait with their backs turned: a slab-shouldered brawler cracking his knuckles, and a nervous gunman weighing a revolver. A curtain at the far end hides a longer gallery.
Present: The Brawler (healthy) — has not noticed you
Present: The Gunman (healthy) — has not noticed you
Exits: Long Gallery; Testing Hall

What do you do?
============================================================

Jess harlow (player1) [Short Range]:   [adjudicate] ranged_attack/shoot target=npc:gunman skill=Firearms_Handgun conf=0.65 -> roll (target bound; outcome opposed or uncertain)
  » Jess harlow — Firearms Handgun 20%: rolled 16 — Regular (9 damage)
[governor] tier=standard model=default budget=5120 timeout=0s dynamic=3523ch system=11488ch total=15011ch cap=17000ch trimmed=- reasons=individual mode;escalation:active-combat

[Querying kimi (kimi-k2.6) — governor: standard tier, 0s deadline...]
[llm    0.2s] kimi-k2.6 initial budget=5120 prompt=15011ch resp=0ch -> FAIL (timeout)
[LLM timed out after 0.05s. Compact retry (15011 -> 1437 chars)...]
[llm    0.2s] kimi-k2.6 initial-compact-retry budget=4096 prompt=1437ch resp=0ch -> FAIL (timeout)

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
The Keeper fell silent twice (provider timeout). Your turn is NOT consumed yet.
(initial call and compact retry both timed out)
Choose how to continue:
  1. retry compact
  2. switch to the Human Keeper (a human host narrates)
  3. use minimal local outcome text (no narration)
  4. save and quit
degraded [1-4]: 3
============================================================
(The Keeper is voiceless — the engine reports plainly.)
Jess harlow's .38 Revolver finds its mark — Regular. The Gunman suffers a major wound (damage: 9).

What do you do?
============================================================

[The Brawler is now alert to your presence.]
[The Gunman is now alert to your presence.]
Jess harlow (player1) [Short Range]: quit → Session saved. The shadows wait...
```

**Session A verdicts:** the entry fallback is the plain room report (room
name, stable description, present NPCs + alert status, exits); the combat
fallback is one composed Keeper sentence — weapon, level, wound band,
damage figure — never a bare dice dump. Alert lines here fire correctly,
AFTER the resolved shoot round.

---

## SESSION B — normal live flow (default timeouts)

Input script: `take knife` / `take revolver` / `take range key` /
`equip knife` / `unlock door with key` / `enter short range` /
`throw knife at guman` / `2` / `close distance gunman` / `punch gunman` /
`equip revolver` / `shoot gunman` / `quit`

```
[llm config] path=config/settings.json default_budget=5120 heavy_budget=8192 override_budget=None effective_default=5120 effective_heavy=8192
  [Registry audit: roster reconciliation — removed 1 unresolved item reference(s).]
[Fresh campaign: 1 investigator(s) ready]

============================================================
 CALL OF CTHULHU 7th — LLM KEEPER  v2.8.1
============================================================
Scenario: testing-hall | Turn: 0 | Scene: th_hall

Jess harlow (player1) [Testing Hall]:   [Jess harlow takes the Knife.]
Jess harlow (player1) [Testing Hall]:   [Jess harlow takes the .38 Revolver.]
Jess harlow (player1) [Testing Hall]:   [Jess harlow takes the Range Key.]
Jess harlow (player1) [Testing Hall]:   [Jess harlow readies the Knife.]
Jess harlow (player1) [Testing Hall]:   [Jess harlow opens the Range Door.]
Jess harlow (player1) [Testing Hall]:   [You catch The Brawler unaware — you have the drop on them.]
  [You catch The Gunman unaware — you have the drop on them.]
  [Jess harlow -> Short Range — the moment calls for the Keeper.]
[governor] tier=standard model=default budget=5120 timeout=180s dynamic=4789ch system=11488ch total=16277ch cap=17000ch trimmed=- reasons=squad routine;escalation:npc:The Brawler;escalation:npc:The Gunman;escalation overrides minimal

[Querying kimi (kimi-k2.6) — governor: standard tier, 180s deadline...]
[llm   47.6s] kimi-k2.6 initial budget=5120 prompt=16277ch resp=2655ch -> ok tok=4110+611
[llm    5.7s] kimi-k2.6 narration_validation_retry budget=4096 prompt=2941ch resp=407ch -> ok tok=708+106

============================================================
Jess Harlow slips through the Range Door into the padded cellar. The Brawler stands close, knuckles cracking, back still turned. Further in, the Gunman hefts his revolver, likewise unaware. Both figures wait, oblivious, as Jess enters their space.

What do you do?
============================================================

Jess harlow (player1) [Short Range]:   [adjudicate] athletics/throw target=- skill=Throw conf=0.60 -> clarify (throw at which target?)
    1. The Brawler (close)
    2. The Gunman (near)
  [Throw at which? e.g. 'throw 1']
[The Brawler is now alert to your presence.]
[The Gunman is now alert to your presence.]
Jess harlow (player1) [Short Range]:   [adjudicate] athletics/throw target=npc:gunman skill=Throw conf=0.70 -> roll (athletics is uncertain; failure matters)
  » Jess harlow — Throw 75%: rolled 36 — Hard (2 damage)
[governor] tier=standard model=default budget=5120 timeout=180s dynamic=3440ch system=11488ch total=14928ch cap=17000ch trimmed=- reasons=squad routine;escalation:active-combat;escalation overrides minimal

[Querying kimi (kimi-k2.6) — governor: standard tier, 180s deadline...]
[llm   13.4s] kimi-k2.6 initial budget=5120 prompt=14928ch resp=2100ch -> ok tok=3800+496

============================================================
Jess Harlow's wrist flicks with the practiced economy of a carnival pitchman, and the knife describes a flat arc through the padded stillness of the Short Range. It strikes the Gunman just below the collarbone, not deep but true — the blade bites and sticks a moment before gravity tugs it free. He grunts, staggers half a step, and claps his left hand to the wound. Dark spreads between his fingers, soaking into the gray fabric of his shirt.

The Gunman's revolver dips. His eyes, which had held that flat target-range indifference, now narrow with the particular shock of someone who has never been on the receiving end of his own trade. He breathes through his teeth, checks the wetness on his palm, and looks at Jess with something that might, in another context, resemble respect.

The Brawler, close enough to smell the oiled leather of the Gunman's holster, has not moved. He watches the blade clatter to the mat, a flat sound the padding swallows almost completely. His hands remain loose at his sides, but his weight has shifted to the balls of his feet — a boxer waiting for the bell, or for permission.

The Knife lies where it fell, equidistant between Jess and the wounded man, its blade smeared and dull under the range lights. The Gunman still holds his revolver, though the muzzle drifts now, no longer tracking anything in particular. Blood threads down his chest and disappears beneath his belt. He does not speak. In this room built for testing, the test has changed its nature.

Beyond the curtain to the Long Gallery, nothing stirs. The Rifleman waits in his own silence, unconnected to this moment.

The air tastes of iron and old canvas. Somewhere in the building's bones, a ventilation fan cycles on and off with the irregular rhythm of a failing heart.

What do you do?
============================================================

[STATE REJECTED]
 - state_delta.squads: top-level field is not approved for model writes (value={})
Jess harlow (player1) [Short Range]:   [adjudicate] close_distance/close distance target=npc:gunman skill=- conf=0.75 -> local (deterministic local action)
  [Jess harlow closes the distance to The Gunman.]
Jess harlow (player1) [Short Range]:   [adjudicate] melee_attack/punch target=npc:gunman skill=Fighting_Brawl conf=0.65 -> roll (target bound; outcome opposed or uncertain)
  » Jess harlow — Fighting Brawl 45%: rolled 75 — Failure
  » The Gunman — Dodge 55%: rolled 98 — Failure (dodges)
[governor] tier=standard model=default budget=5120 timeout=180s dynamic=3634ch system=11488ch total=15122ch cap=17000ch trimmed=- reasons=individual mode

[Querying kimi (kimi-k2.6) — governor: standard tier, 180s deadline...]
[llm   24.4s] kimi-k2.6 initial budget=5120 prompt=15122ch resp=2669ch -> ok tok=3863+617
[llm   13.2s] kimi-k2.6 narration_validation_retry budget=4096 prompt=2792ch resp=514ch -> ok tok=667+136

============================================================
Jess Harlow steps in on the gunman, fist cocked. She throws a hard right cross meant to fold him where he stands—but the nervous man flinches sideways at the last instant, and her knuckles whistle through empty air. The gunman staggers back wide-eyed, revolver still clutched white-knuckled. Behind her, the brawler's knuckles crack again, watching.

What do you do?
============================================================

Jess harlow (player1) [Short Range]:   [Jess harlow readies the .38 Revolver.]
Jess harlow (player1) [Short Range]:   [adjudicate] ranged_attack/shoot target=npc:gunman skill=Firearms_Handgun conf=0.65 -> roll (target bound; outcome opposed or uncertain)
  » Jess harlow — Firearms Handgun 20%: rolled 58 — Failure
[governor] tier=standard model=default budget=5120 timeout=180s dynamic=3489ch system=11488ch total=14977ch cap=17000ch trimmed=- reasons=individual mode

[Querying kimi (kimi-k2.6) — governor: standard tier, 180s deadline...]
[llm   16.3s] kimi-k2.6 initial budget=5120 prompt=14977ch resp=2611ch -> ok tok=3818+596
[llm    5.8s] kimi-k2.6 narration_validation_retry budget=4096 prompt=2730ch resp=593ch -> ok tok=657+153

============================================================
Jess raises her pistol, sights down the nervous gunman, and squeezes the trigger. The shot goes wide, punching into the padded wall with a dull thud. The gunman flinches, whipping around with revolver half-drawn, eyes wild. The brawler's knuckles crack louder, his broad back still turned, oblivious to the near miss behind him. Cordite hangs sharp in the cellar air. The gunman's breath comes ragged now—he knows he's a target.

What do you do?
============================================================

Jess harlow (player1) [Short Range]: quit → Session saved. The shadows wait...
```

**Session B verdicts:**

- **Test 2 (bystander) — PASS.** The throw-turn narration wounds ONLY the
  Gunman (2 damage, engine-dealt; blood on him is a legal reference). The
  Brawler gets posture/attention prose only ("has not moved", "weight has
  shifted") — no injury, no blood, no collapse, across this and the two
  following combat turns. The "untouched this turn" packet lines did their
  job.
- **Test 3 (menu/alert timing) — FAIL.** The menu prints
  (`1. The Brawler (close) / 2. The Gunman (near)`), and the VERY NEXT
  lines are `[The Brawler is now alert…]` / `[The Gunman is now alert…]` —
  before the `2` answer, before the roll. The clarify-menu round is being
  counted as a "resolved" round by the round-end `_alert_check`, which
  contradicts the documented invariant ("rounds of free commands (look,
  distance, inventory, menus) and pure passes keep the window open").
  Reproduces identically in `--mock`, so it is engine-side and
  deterministic. The roll itself, the menu ownership, and the no-dice
  before-answer behavior are all correct; only the alert timing is wrong.
- **Test 4 (entry narration) — PASS with a caveat.** The final entry
  narration names the Range Door and does not go voiceless
  (fix-pack-#4 Prompt 2 intact). Caveat: the initial entry narration was
  rejected by the validator and recovered via the compact retry — 3 of 4
  narrated turns this session needed that retry. It works, but it is
  costing one extra compact call per turn on this scenario.
