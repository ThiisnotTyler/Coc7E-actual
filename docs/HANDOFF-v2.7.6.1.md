# HANDOFF — v2.7.6.1 "The Truth Firewall"

**Completed:** Phase 0 of the Persistent Campaign Engine Roadmap  
**Baseline:** v2.7.6 "The Spoiler Channel"  
**Current version stamp:** `2.7.6.1` in `src/__init__.py`

---

## 1. What changed

Phase 0 closes the hole where the LLM could directly overwrite canonical campaign state through `state_delta`. The engine now owns truth; the model owns voice.

Key behavioral changes:

- **New `src/state_validator.py`** validates every model-produced `state_delta` before it touches world state.
- **Engine-owned fields are blocked** from direct LLM mutation: HP, SAN, MP, Luck, characteristics, skills, weapons, inventory, location, wounds, insanity, Cthulhu Mythos, and identity fields.
- **Scene transitions must follow the location graph** — unknown characters, unknown destinations, and disconnected destinations are rejected.
- **Front clocks are clamped** to `0..max` and must reference existing fronts.
- **Weapon instances are persistent per character** via `Character.weapon_instances`. Re-equipping retrieves the same physical weapon, so ammunition and malfunction state survive unequip/equip.
- **Character prompt serialization now includes inventory**, reducing the chance the model narrates gear the investigator does not carry.
- **System prompt** updated with `STATE AUTHORITY` rules and tightened movement instructions.

---

## 2. Files added or modified

### Added

- `src/state_validator.py`
- `docs/v2.7.6.1-release-notes.md`
- `docs/HANDOFF-v2.7.6.1.md` (this file)

### Modified

- `src/__init__.py` — version bumped to `2.7.6.1`
- `src/character.py` — added `weapon_instances`; inventory shown in `to_active_format()`; save/load round-trips instances
- `src/keeper.py` — integrated `StateDeltaValidator`; persistent unequip/equip; rejected-write debug logging; clamped fronts; graph-validated movement
- `config/system-prompt.txt` — added `STATE AUTHORITY` section and tightened movement rules
- `test_engine.py` — added 12 Phase 0 regression checks

---

## 3. Test results

Run from the project root (`coc7-keeper/`):

```bat
py test_engine.py
py test_charcreate.py
py -m src.main --mock
```

Latest results at the time of the v2.7.6.1 release:

```text
ALL TESTS PASSED (231 checks)         # test_engine.py
ALL CREATION TESTS PASSED (64 checks) # test_charcreate.py
```

Mock mode ran cleanly and displayed `v2.7.6.1 [MOCK MODE]`.

---

## 4. Known limitations

- **Weapon instance object identity is not preserved through save/load.** After deserialization, `char.weapon` and `char.weapon_instances[name]` are separate Python objects with identical initial ammo values. The first `unequip` re-synchronizes them. This is a deliberate Phase 0 bridge; the full item registry in v2.8.0 solves it properly.
- **Front clocks are still model-proposable.** Phase 3 will replace model-driven front updates with deterministic trigger-driven events. For now, updates are clamped and validated but still originate from the model.
- **Proposal fields are accepted but not yet acted upon.** `proposed_facts`, `proposed_consequences`, and `npc_reactions` pass validation and are stored in the cleaned delta, but no downstream system consumes them yet. That work begins in v2.8.x / v2.8.3.
- **Sound events are sanitized but not propagated.** The validator cleans `sound_events`, but the engine does not yet dispatch them through the spatial graph. That arrives in v2.8.2.
- **Scenery can still drift on re-observation.** The Truth Firewall blocks mechanical state mutation, but the LLM can still hallucinate new room details when asked to "look around" again. There is no engine-owned ledger of previously rendered narrative details, so the model may add, remove, or redecorate objects that were already described. The planned fix is a **rendered-details ledger** (a precursor to the v2.8.1 facts ledger) that records what the Keeper has already narrated about each location and injects those details back into the prompt on subsequent observations.
- **Rendered-detail storage will be compressed after the roadmap.** Once the facts/rendered-details ledger exists, the prompt representation of already-rendered and pre-rendered location details will be summarized into a shortform format to keep context size bounded.

---

## 5. Save compatibility notes

Existing v2.7.x saves load without modification:

- Old saves without `weapon_instances` seed the store from the equipped weapon on first `Character.from_dict()`.
- Empty inventories still seed from the equipped weapon.
- New fields are ignored by older code if a save is ever loaded by a pre-v2.7.6.1 binary.
- The legacy `state_delta` shape is still accepted, but only after validation.

**No save migration script is required.** Loading an old save automatically bridges it into the new format.

---

## 6. Exact next tasks for v2.8.0 — Canonical Items and Objects

Do not start these until v2.7.6.1 is accepted and tagged.

1. **Create `src/items.py`** with:
   - `ItemTemplate` dataclass (catalog definition)
   - `ItemInstance` dataclass (campaign-specific instance with owner, location, quantity, state, condition)
2. **Add item templates to scenarios** or a shared catalog (`data/items.json` or per-scenario).
3. **Add `World.items` registry** and room item references.
4. **Migrate `Character.inventory`** from display-name strings to item instance IDs.
5. **Move `weapon_instances` into the item registry** compatibility layer.
6. **Add persistent gear commands:**
   - `take <item>`
   - `drop <item>`
   - `give <item> to <character>`
   - `reload <weapon>`
   - `open <container>`
   - `look at <item>`
7. **Add room object records** for doors, containers, clue objects, and destructible scenery.
8. **Update combat** to consume ammunition from the equipped item instance via the item registry.
9. **Update save/load migration** from v2.7.6.1 `weapon_instances` + string inventory to v2.8.0 item IDs.
10. **Update `test_engine.py`** with item registry regression tests.

---

## 7. Notes for the next AI coding session

- **Always run from the project root** (`coc7-keeper/`). Relative paths (`config/`, `saves/`, `data/`, `logs/`) resolve from there.
- **Use `py`, not `python`**, in this Windows Git Bash environment.
- **Offline test suite is the gate:** `py test_engine.py && py test_charcreate.py` must stay green.
- **The Truth Firewall is central.** Any future feature that lets the model propose mechanical state must go through `StateDeltaValidator` or an engine-owned event, not direct mutation.
- **Do not expand `ENGINE_OWNED_CHARACTER_FIELDS` without a roadmap reason.** The list in `src/state_validator.py` is the contract.
- **v2.8.0 is the item registry, not more state validation.** Do not redesign the validator in v2.8.0; build the item system around it.
- **Read `docs/v2.8-offload-roadmap.md` and `docs/CoC7-Keeper-Persistent-Campaign-Engine-Roadmap.pdf`** before starting v2.8.0.
- **If you add new proposal fields**, add them to `PROPOSAL_TOP_LEVEL_FIELDS` in `state_validator.py` and validate their shape before they reach engine code.
- **Keep changes minimal and test-backed.** This codebase values regression tests over comments.
