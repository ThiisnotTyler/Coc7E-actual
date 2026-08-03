# Movement / Stance / Stealth — Design + Testing Field Tracker

Status: **design phase — nothing implemented.** This document is the tracking
surface for the movement/stealth feature family: the agreed design, the open
rulebook questions, and the test fields each phase must satisfy before it
ships. Update it as phases land. Truth Firewall applies throughout: stance
and sneaking are ENGINE-OWNED mechanical state; the UI only projects them.

## 1. Agreed design constraints (do not relitigate without a field failure)

- **No coordinates, no room dimensions, no square footage.** CoC 7e is a
  range-band game; the engine already is one too. Grid-model distance is a
  non-goal — it fights the LLM narrator, the authoring guide, and the test
  surface.
- **Build on the existing substrate:**
  - position bands with nominal yards (`close/near/far/elevated/behind_cover`
    → 2/5/10/8/5y, `combat.py`), driving melee reach, firearm bands, and
    effective skill targets;
  - `spatial.py`: room graph, BFS distance, perception levels
    (ADJACENT/OFF_SCREEN), sound propagation (loud noise through muffled
    walls), occupant tracking;
  - `loc.lighting` (already authored and allowlisted in room view);
  - deterministic band transitions (`close distance`, forced movement,
    retreat/cover);
  - the surprise/alert system (`_alert_check`, round-start snapshots,
    FIX A turn-counter gate).
- **Stance and sneaking are new engine fields**, consumed by combat and the
  alert system. `status_view.py` (planned) renders them only once they
  exist. Never let the display layer invent them.

## 2. Open questions — VERIFY against the CoC 7e rulebook BEFORE speccing

Mark each `[verified: source/page]` when checked. Do not implement from memory.

- [ ] **Posture modifier to Stealth.** Is crawling/crouching easier than
  standing when sneaking? (Field intuition says yes; core 7e may leave it
  to Keeper discretion — if so, we define it as engine rule: posture grants
  bonus/penalty die on Stealth, deterministic, not narrator fiat.)
- [ ] **Movement in combat rounds.** Does 7e meter ordinary movement inside
  a fight (MOV-based), or is non-chase movement fiat? Our cost model
  (moving while sneaking = your action; running breaks stealth; crossing
  into a perception band triggers detection) must not contradict RAW.
- [ ] **Chase rules.** 7e has a formal chase minigame (MOV ratings,
  chase-track positions, hazards). How much of it do we adopt vs. abstract
  into the existing CINEMATIC mode? Current engine: `CINEMATIC` mode exists
  for chases but has no chase track.
- [ ] **Stealth vs. detection mechanics.** Opposed roll (Stealth vs.
  Listen/Spot Hidden) or threshold by band + lighting? What does 7e say
  about group sneaking (worst roll? one roll?)?
- [ ] **Breaking stealth.** What breaks it by RAW — attacking, running,
  noise, entering line of sight? Our noise/sound-propagation data already
  tags gunshots (noise: 4); define which events force detection checks.

## 3. Vehicles / flight / chase distance — the scope trap

Decision: **abstract by default, system only if a scenario demands it.**

- Outdoors distance: same band model, wider nominal spans. No new system —
  a large `span` on outdoor locations (see Phase 1).
- Chase scenes: use CINEMATIC mode + authored chase beats. A real chase
  track (MOV vs MOV) is a *maybe-later*, gated on rulebook review (§2).
- **Cars, biplanes, helicopters, jets: NON-GOAL for the engine.** Vehicle
  speed tables belong in scenario data/flavor text, not mechanics. If a
  future campaign ships a vehicle chase, spec it then as a scenario-driven
  CINEMATIC sequence with authored outcomes — never a vehicle-physics
  system. (7e's own vehicle chase rules are an optional minigame; the
  rulebook check in §2 will confirm how optional.)
- Era note: classic CoC is 1920s — biplanes plausible, jets/helicopters
  are modern-era. Either way: narrative, not systems.

## 4. Phases and their testing fields

Each phase: failing regression tests FIRST, full offline gate green after
(test_engine / test_lobby / test_charcreate / test_items / test_adjudicator /
test_dice), then a live smoke. Update AGENTS.md when conventions change.

### Phase 0 — status_view (no new mechanics) — **LANDED 2026-07-29**
- `src/status_view.py`: `build_status(keeper, char) -> dict` (data-first
  contract for the future UI) + `render_status(dict) -> str`; `status`/`st`
  free local command in `commands.py` (no LLM, no turn).
- Test fields: projection dict matches engine state (HP, condition, weapon
  + kind + ammo, position, inventory); renderer output stable; command
  consumes no turn and calls no LLM; stance/sneaking keys ABSENT until the
  engine owns them.

### Phase 1 — authored room scale (`span`) — **LANDED 2026-07-29**
- Optional scenario field `span: small|medium|large` per location
  (default medium). Scales nominal band yards and detection ranges.
- Test fields: default unchanged for all shipped scenarios (zero behavior
  drift); a large-span room widens firearm bands deterministically;
  save/load round-trips the field; worldbuilding guide documents it.

### Phase 2 — player stance (small) — **LANDED 2026-08-02**
- One engine-owned stance field (mirrors NPC `defender_stance`:
  dodge/fight back/none), set by the free local `stance` command
  (`stance dodge|fight back|none|auto`, bare `stance` reports),
  consumed by opposed melee via `CombatEngine.defender_stance`.
- Test fields: stance affects opposed-melee resolution deterministically;
  stance is engine-owned (state_delta cannot write it); narration may
  reference it but never set it; console shows it (status sheet +
  command feedback). All landed — test_engine.py Phase 2 section.
- Notes: `None` = engine policy (brawl >= dodge fights back, else dodge);
  helpless/unaware still overrides a chosen stance; NPCs always use the
  policy. The `stance` command is the declaration channel (system
  channel, like `equip`) — no LLM, no turn.

### Phase 3 — sneaking (the big one — most regression-sensitive)
- Engine-owned `sneaking` flag per character; declaration to enter/exit;
  movement while sneaking costs the action; detection checks (per §2
  rulebook outcome) at entry, band-crossing, noise events; integrates with
  `_alert_check` and the surprise window — does NOT bypass FIX A.
- Test fields: sneaking character not detected at range by unalerted NPCs;
  detection at close band/loud noise/attack is deterministic; stealth
  breaks on the RAW-defined events; free-command and clarify-menu rounds
  still never alert; save/load round-trips the flag; narration cannot
  declare someone hidden/revealed.

### Phase 4 (maybe, gated on §2) — chase mechanics
- Only if the rulebook review and a concrete scenario justify it.
  CINEMATIC-mode chase beats with authored outcomes; MOV-vs-MOV track is
  the stretch version.

## 5. Standing reminders

- Version stamp stays 2.8.1 (hotfixes don't bump it).
- Never weaken StateDeltaValidator or the narration validator; posture/
  stealth vocabulary will need validator support ADDED when Phase 2/3
  land (a 'he crouches' narration is only legal once the engine owns
  crouching).
- Live evidence goes in docs/LIVE-TEST-*.md next to the 2026-07-29 log.
