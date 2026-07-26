# CoC 7e LLM Keeper — v2.8.0 Post-Acceptance Testing Program

**Working title:** v2.8.0.1 “The Registry Proving Ground”  
**Audience:** Kimi Code CLI / next coding agent  
**Status:** Required hardening work after v2.8.0 acceptance  
**Priority:** Complete before starting v2.8.1

---

## 1. Mission

v2.8.0 has been accepted as the Canonical Items and Objects release. Before building v2.8.1, the item/object registry needs a dedicated hostile testing layer.

The current release reports:

- `245` engine checks passing
- `64` character-creation checks passing
- item registry, object registry, migration, combat sync, containers, and new meta-commands implemented

That is a strong start, but the v2.8.0 feature surface is much larger than the visible increase in regression coverage. This task closes that gap.

> **Do not begin v2.8.1 during this task.**

This release is about proving the v2.8.0 foundation, not adding new gameplay features.

---

## 2. Core doctrine

The tests should not merely prove that commands work on the happy path.

They should prove that the item registry cannot be made to lie.

A persistent campaign engine must guarantee:

1. An item exists in exactly one authoritative place.
2. An item cannot be duplicated by take/drop/give/equip/reload/save/load.
3. Two identical templates always produce independent physical instances.
4. Ammo, condition, and custom state belong to the instance, not the template.
5. The transient `Character.weapon` compatibility view can never become stale truth.
6. Inspection commands cannot reveal hidden or locked content.
7. `use` cannot bypass the Truth Firewall.
8. Legacy saves migrate without collapsing legitimate duplicate gear.
9. Corrupted or partial saves fail safely and visibly.
10. Local commands do not call the LLM.

---

## 3. Non-goals

Do **not** implement these during this task:

- v2.8.1 static room descriptions
- zero-LLM movement
- exit-state mechanics
- clue triggers
- facts and knowledge
- NPC Director behavior
- new item types
- nested containers
- lockpicking
- trapped containers
- major refactors unrelated to test failures

Only fix production code when a new test exposes an actual defect.

---

# 4. Required test systems

Create dedicated testing infrastructure instead of burying all new checks inside one increasingly large happy-path suite.

The exact filenames may be adapted to the project’s existing conventions, but the systems below are required.

---

## 4.1 Item test fixture factory

Create a reusable fixture builder for item-registry tests.

Suggested location:

```text
test_items.py
tests/fixtures/items.py
```

or, if this project keeps root-level test scripts:

```text
test_items.py
```

The fixture factory should build a minimal `CoCKeeper` in mock mode with:

- two player characters
- one NPC
- at least three connected locations
- two instances of the same weapon template
- one ammunition stack
- one key
- one locked container
- one unlocked container
- one consumable
- one light source
- one hidden item
- one world object
- one malformed or unsupported item for negative tests

The point is to avoid repeating fragile setup code across dozens of tests.

### Required helper assertions

Add reusable helpers such as:

```python
assert_item_location(keeper, item_id, expected_location)
assert_item_owner(keeper, item_id, expected_owner)
assert_item_unique(keeper, item_id)
assert_no_duplicate_item_instances(keeper)
assert_inventory_consistent(keeper, character_id)
assert_transient_weapon_synced(keeper, character_id)
assert_save_load_item_equivalence(before_keeper, after_keeper)
```

These helpers should check invariants across:

- `Character.inventory`
- `Character.equipped_item_id`
- `CoCKeeper.item_instances`
- `CoCKeeper.world_objects`
- `Location` or room item references, if present
- save/load serialized data

---

## 4.2 Item registry invariant checker

Add a reusable audit function that scans the entire world and reports impossible item states.

Suggested name:

```python
audit_item_registry(keeper) -> list[str]
```

It should detect:

- item IDs referenced but missing from `item_instances`
- items with both an owner and a room location, unless explicitly allowed
- items in a character inventory but owned by someone else
- equipped item IDs not present in inventory
- equipped item IDs pointing to nonexistent items
- multiple characters owning the same item
- multiple room references to the same item
- item quantities below zero
- weapon ammo below zero
- item condition values outside the supported set
- duplicate instance IDs
- container contents referencing missing items
- contents listed in more than one container
- world objects referencing missing items or locations

This checker should run after every major command test.

If the project already has or later gains `src/audit.py`, place the reusable production version there and let tests call it. Otherwise keep the first version test-only.

---

## 4.3 Save/load round-trip harness

Create a helper that performs:

```text
mutate world → save → load → compare canonical state
```

It should compare:

- item instance IDs
- template IDs
- owner IDs
- location IDs
- quantities
- ammo
- condition
- custom state
- equipped item IDs
- inventories
- world object state
- container contents
- lock state

The comparison must focus on canonical state, not Python object identity.

### Required round-trip scenarios

- fresh campaign
- after firing a weapon
- after partial reload
- after taking/dropping/giving items
- after opening a container
- after using a consumable
- after jamming a weapon
- after migrating an old save

---

## 4.4 Legacy migration fixture builder

Create synthetic v2.7.x and v2.7.6.1 save fixtures in code.

Do not rely only on whatever old save happens to exist locally.

Required fixtures:

1. equipped weapon only
2. string inventory only
3. equipped weapon plus matching string inventory
4. equipped weapon plus two duplicate string inventory entries
5. multiple characters with the same weapon template
6. one character with two same-template weapons
7. weapon instance bridge with reduced ammo
8. missing `weapon_instances`
9. malformed legacy weapon dictionary
10. old save with unknown weapon name

Each fixture should test both:

- successful intended migration
- safe failure or quarantine for invalid data

---

## 4.5 Command transcript harness

Add a way to run deterministic command sequences and compare output/state.

Example shape:

```python
run_commands(keeper, [
    ("eleanor", "inventory"),
    ("eleanor", "take shotgun"),
    ("eleanor", "equip shotgun"),
    ("eleanor", "drop shotgun"),
])
```

For every command sequence, assert:

- expected output or output category
- expected canonical state
- no LLM call occurred
- no turn was consumed
- no unexpected event/log entry occurred, where measurable

The project already has meta-command behavior; this harness should make those command paths cheap to test.

---

## 4.6 No-LLM command tracker

Extend the mock client or test fixture to count calls.

Every local meta-command test should assert:

```python
mock_client.calls == 0
```

or the project’s equivalent.

This applies to:

- `inventory`
- `equip`
- `unequip`
- `take`
- `drop`
- `give`
- `reload`
- `open`
- `look at`
- `examine`
- `use`
- `help`
- `list`

If any command calls the model, the test must fail unless the command is explicitly designed to escalate.

---

## 4.7 Hostile `state_delta` generator

The Truth Firewall already has tests. Expand them for item/object authority.

Add generated hostile deltas attempting to mutate:

- `inventory`
- `equipped_item_id`
- `weapon`
- `weapon_instances`
- `ammo`
- `condition`
- `location`
- `hp`
- `san`
- `item_instances`
- `item_templates`
- `world_objects`
- container lock state
- key ownership

The validator must reject all of them.

Also test malformed payloads:

- `None`
- strings instead of dictionaries
- lists instead of dictionaries
- unknown character IDs
- unknown front IDs
- unknown locations
- negative front clocks
- non-numeric clocks
- malformed sound events
- deeply nested garbage in `extra`

The engine should reject or sanitize without crashing.

---

# 5. Required test matrix

Use these test IDs in names or comments so the final report can map failures to coverage.

---

## A. Duplicate item instances — `DUP`

### `DUP-01` — same character, same template

Create two instances of the same weapon template for one character.

Assert:

- unique instance IDs
- independent ammo
- independent condition
- independent state
- both appear in inventory

### `DUP-02` — same template across characters

Give the same weapon template to two characters.

Assert:

- separate instances
- firing one does not affect the other
- jamming one does not affect the other

### `DUP-03` — duplicate persistence

Save/load after mutating duplicate instances.

Assert both remain separate and retain their individual state.

### `DUP-04` — duplicate template migration

Migrate a legacy save with duplicate same-name inventory entries.

Assert legitimate duplicates are not collapsed incorrectly.

---

## B. Transient weapon compatibility — `WPN`

### `WPN-01` — fire sync

Fire a weapon through combat.

Assert:

- transient `Character.weapon.ammo` decreases
- canonical `ItemInstance` ammo decreases
- inventory command displays the same value

### `WPN-02` — malfunction sync

Force a malfunction.

Assert:

- transient weapon condition updates
- canonical instance condition becomes `jammed`
- save/load preserves `jammed`

### `WPN-03` — unequip/equip identity

Fire, unequip, equip.

Assert the same item instance is equipped and ammo does not reset.

### `WPN-04` — save/load re-equip

Fire, save, load, unequip, equip.

Assert ammo and condition remain correct.

### `WPN-05` — no stale view after reload

Reload through the canonical item system.

Assert the transient weapon view immediately reflects the new ammo.

---

## C. Reload behavior — `RLD`

### `RLD-01` — full reload

A partially empty weapon and enough ammo reload to capacity.

### `RLD-02` — partial reload

Insufficient ammo reloads only what is available.

### `RLD-03` — wrong ammo type

Correctly fails without changing weapon or ammo stack.

### `RLD-04` — no ammo

Fails without changing state.

### `RLD-05` — ammo stack depletion

Ammo quantity decreases; empty stack is removed or marked according to the intended convention.

### `RLD-06` — jam persistence

Reloading does not silently clear `jammed` unless the rules explicitly say it should.

### `RLD-07` — reload persistence

Partial reload survives save/load.

---

## D. Transfer integrity — `TRN`

### `TRN-01` — take once

Taking an item removes it from the room and adds it to the character.

### `TRN-02` — no duplicate take

Repeating `take` cannot duplicate the item.

### `TRN-03` — drop consistency

Dropping removes owner/inventory and sets room location.

### `TRN-04` — give consistency

Giving transfers ownership and inventory between characters in the same room.

### `TRN-05` — cross-room give fails

No state changes when recipient is elsewhere.

### `TRN-06` — equipped item transfer fails

Equipped item cannot be dropped or given unless unequipped first.

### `TRN-07` — no owner/location conflict

After every transfer, the registry invariant checker returns no errors.

### `TRN-08` — NPC transfer policy

Giving to an NPC either works intentionally or fails cleanly; no ambiguous ownership.

---

## E. Containers and keys — `CTR`

### `CTR-01` — wrong key fails

State remains unchanged.

### `CTR-02` — correct key succeeds

Container opens and state persists.

### `CTR-03` — key not carried fails

A key lying elsewhere does not open the container unless intended.

### `CTR-04` — contents hidden while locked

`look at` or `examine` does not reveal contents.

### `CTR-05` — contents visible after open

Only the intended contents are shown.

### `CTR-06` — repeated open is idempotent

No duplicated contents or repeated events.

### `CTR-07` — lock state survives save/load

### `CTR-08` — malformed key reference fails safely

Missing `key_id`, missing key item, or wrong key type cannot crash the engine.

---

## F. Use command safety — `USE`

### `USE-01` — consumable quantity

Using a consumable decrements quantity.

### `USE-02` — consumable depletion

Quantity zero removes the item or marks it spent according to convention.

### `USE-03` — healing uses engine rules

Any HP effect goes through deterministic engine code, not direct model or command shortcuts.

### `USE-04` — unsupported item fails safely

No crash and no state change.

### `USE-05` — light-source state persists

Lit/unlit state survives save/load.

### `USE-06` — cannot use absent item

Fails without state changes.

### `USE-07` — cannot use item owned by someone else

Fails unless explicitly intended.

### `USE-08` — Truth Firewall remains intact

`use` cannot write HP, SAN, skills, ammo, or inventory outside engine-owned handlers.

---

## G. Inspection and spoiler safety — `SEE`

### `SEE-01` — visible item inspection

`look at` shows intended public details.

### `SEE-02` — hidden item not revealed

Generic room inspection does not expose hidden items.

### `SEE-03` — locked contents not revealed

Locked containers do not leak contents.

### `SEE-04` — NPC gear visibility policy

NPC carried items are not shown unless intended.

### `SEE-05` — clue data not leaked

Inspection output cannot expose hidden clue IDs, triggers, or plot secrets.

### `SEE-06` — examine unavailable object fails safely

No crash when examining missing, removed, or destroyed things.

---

## H. Registry and firewall authority — `AUTH`

### `AUTH-01` — equipped item ID protected

Hostile `state_delta` cannot assign `equipped_item_id`.

### `AUTH-02` — inventory protected

Hostile `state_delta` cannot assign inventory.

### `AUTH-03` — ammo protected

Hostile `state_delta` cannot assign weapon ammo or item ammo.

### `AUTH-04` — condition protected

Hostile `state_delta` cannot set `condition`.

### `AUTH-05` — registries protected

Unknown top-level fields such as `item_instances`, `item_templates`, and `world_objects` are rejected.

### `AUTH-06` — object state protected

The model cannot mark a door/container/object open, broken, or destroyed directly.

### `AUTH-07` — malformed payloads fail safely

The validator and Keeper survive malformed state deltas without crashing.

---

## I. Save/load and corruption resilience — `SAVE`

### `SAVE-01` — fresh world round-trip

### `SAVE-02` — post-combat round-trip

### `SAVE-03` — post-transfer round-trip

### `SAVE-04` — post-container round-trip

### `SAVE-05` — missing item ID fails visibly

A character inventory references a missing item; audit catches it.

### `SAVE-06` — duplicate owner/location caught

An item appears owned and room-located inconsistently; audit catches it.

### `SAVE-07` — malformed object state caught

Unknown object state or invalid container contents fail safely.

### `SAVE-08` — old save migration round-trip

Migrate old save, save again, load again, and verify canonical state.

### `SAVE-09` — unknown template policy

A save referencing a missing template either quarantines the item or raises a controlled audit error.

---

## J. Local command behavior — `CMD`

### `CMD-01` — inventory calls no LLM

### `CMD-02` — equip calls no LLM

### `CMD-03` — unequip calls no LLM

### `CMD-04` — take calls no LLM

### `CMD-05` — drop calls no LLM

### `CMD-06` — give calls no LLM

### `CMD-07` — reload calls no LLM

### `CMD-08` — open calls no LLM

### `CMD-09` — look/examine calls no LLM

### `CMD-10` — use calls no LLM unless explicitly designed to escalate

### `CMD-11` — help/list calls no LLM

### `CMD-12` — commands do not consume a narrative turn

---

# 6. Implementation plan

## Step 1 — Read and orient

Read:

- `AGENTS.md`
- `docs/HANDOFF.md`
- `docs/v2.8.0-release-notes.md`
- `docs/HANDOFF-v2.7.6.1.md`
- `docs/roadmap/CoC7-Keeper-Persistent-Campaign-Engine-Roadmap.md`
- `src/items.py`
- `src/character.py`
- `src/keeper.py`
- `src/combat.py`
- `src/state.py`
- `src/state_validator.py`
- `test_engine.py`

Report any mismatch between the release notes and actual source before writing tests.

---

## Step 2 — Add test infrastructure

Implement:

1. item fixture factory
2. registry invariant checker
3. save/load round-trip helper
4. legacy migration fixture builder
5. command transcript harness
6. no-LLM mock call tracker
7. hostile `state_delta` test helpers

Keep infrastructure deterministic and offline.

---

## Step 3 — Add the required matrix

Implement all test IDs listed above unless a test is genuinely impossible with the current architecture.

If a test cannot be implemented, document:

- test ID
- reason
- missing architecture
- planned phase that will make it testable

Do not silently skip coverage.

---

## Step 4 — Fix only exposed defects

If tests fail because the implementation is wrong, fix the implementation.

Do not:

- weaken assertions
- delete hostile cases
- special-case tests
- add gameplay features to hide the defect
- begin v2.8.1

---

## Step 5 — Run the full offline suite

From the project root:

```bat
py test_engine.py
py test_charcreate.py
py test_items.py
echo "quit" | py -m src.main --mock
```

If the project does not use `test_items.py` as a separate suite, run whatever equivalent root-level test command is added.

All suites must pass.

---

## Step 6 — Update documentation

Update:

- `docs/v2.8.0-release-notes.md`
- `docs/HANDOFF.md`

Add:

- final test counts
- new test files
- defects found
- defects fixed
- remaining known limitations
- whether v2.8.0 is now considered fully proven

Create:

```text
docs/v2.8.0.1-testing-notes.md
```

with a concise summary of the hardening release.

---

# 7. Acceptance criteria

This task is complete only when all of the following are true:

1. The duplicate-instance matrix passes.
2. The transient weapon compatibility matrix passes.
3. The reload matrix passes.
4. The transfer integrity matrix passes.
5. The container/key matrix passes.
6. The use-command matrix passes.
7. The inspection/spoiler matrix passes.
8. The registry/firewall authority matrix passes.
9. The save/load and corruption matrix passes.
10. The no-LLM command matrix passes.
11. The registry invariant checker reports no errors after command sequences.
12. Legacy migration fixtures pass.
13. Existing `test_engine.py` and `test_charcreate.py` remain green.
14. Mock mode starts and exits cleanly.
15. Documentation reflects the final test count and limitations.

---

# 8. Required final report format

The coding agent should finish with this exact summary:

```text
v2.8.0.1 Registry Proving Ground Report

Status: PASS/FAIL

Files added:
- ...

Files modified:
- ...

Test systems added:
- ...

New checks:
- Duplicate instances: N
- Weapon compatibility: N
- Reload: N
- Transfers: N
- Containers/keys: N
- Use safety: N
- Inspection safety: N
- Authority/firewall: N
- Save/load/corruption: N
- Command/no-LLM: N

Defects found:
- ...

Defects fixed:
- ...

Known limitations:
- ...

Commands run:
- py test_engine.py — PASS/FAIL, N checks
- py test_charcreate.py — PASS/FAIL, N checks
- py test_items.py — PASS/FAIL, N checks
- echo "quit" | py -m src.main --mock — PASS/FAIL

Recommendation:
- READY / NOT READY for v2.8.1
```

---

# 9. Copy/paste prompt for Kimi Code

Use this after placing this file in the project, for example as:

```text
docs/testing/CoC7-Keeper-v2.8.0-Post-Acceptance-Testing-Program.md
```

Then run Kimi Code from the project root and give it:

```text
Read @AGENTS.md, @docs/HANDOFF.md, @docs/v2.8.0-release-notes.md, @docs/HANDOFF-v2.7.6.1.md, and @docs/testing/CoC7-Keeper-v2.8.0-Post-Acceptance-Testing-Program.md.

Your task is to implement the v2.8.0.1 Registry Proving Ground exactly as specified.

Do not begin v2.8.1.
Do not add new gameplay features.
Do not redesign the item system unless a hostile test exposes a defect.

First inspect the current source and report any mismatch between the v2.8.0 release notes and the actual implementation. Then add the required test systems, implement the full test matrix, fix any exposed defects, run the complete offline suite, and update the handoff/release documentation.

Required final commands:

py test_engine.py
py test_charcreate.py
py test_items.py
echo "quit" | py -m src.main --mock

Finish with the exact v2.8.0.1 Registry Proving Ground Report format from the testing program.
```

---

## 10. Why this matters

v2.8.0 is the physical foundation of the persistent campaign engine.

If item truth is unreliable, every later phase inherits the corruption:

- room overlays show the wrong objects
- clues duplicate or disappear
- NPCs carry impossible gear
- containers leak secrets
- saves cannot be trusted
- multiplayer eventually syncs broken state

This hardening pass is how we make v2.8.0 worthy of the phases built on top of it.
