Read:
- @AGENTS.md
- @docs/HANDOFF.md
- @docs/v2.8.0-release-notes.md
- @docs/v2.8.0.1-testing-notes.md
- @docs/field-tests/v2.8.1-five-minute-house.md
- @src/keeper.py
- @src/items.py
- @src/combat.py
- @src/state_validator.py
- @test_engine.py
- @test_items.py

We are doing a v2.8.1 field-test hotfix. Do not begin v2.8.2. Do not refactor the whole Keeper. Fix the command/adjudication seam exposed by the transcript.

PRIMARY GOAL:
The engine must resolve local commands and committed risky actions deterministically before the LLM narrates. The model must never narrate equipment, item, object, exit, ammunition, or condition changes that the engine did not apply.

1. COMMAND NORMALIZATION

Make command parsing tolerant of natural arguments:

- "unequip", "unequip shotgun", "unequip 12-gauge shotgun", "put away shotgun", and "lower shotgun" must resolve locally.
- Bare "take", "equip", "drop", "give", "reload", "open", "look at", "examine", "use", "enter", and "go to" must never call the LLM merely because the target is missing.
- If one valid target exists, use it or ask for confirmation.
- If multiple targets exist, list them.
- If no target exists, say so locally.
- Add numbered selection where practical, e.g. "take 1".

Fix "use letter" style failures:
- if a matching document/item is visible in the room but not carried, suggest "take <item>", "read <item>", or "look at <item>".
- add a local "read <document>" command for readable items.

Fix command UX:
- inventory must not print bare "None".
- remove the "exit" alias conflict; use "back", "go back", or "return" for movement and reserve "exit" for quitting if that is the established behavior.
- local commands must not consume a narrative turn.

2. COMMITTED ACTION RESOLUTION

Expand the preroll/action system so committed risky declarations resolve dice BEFORE the LLM sees the turn.

Add target-aware patterns for melee and improvised attacks:

- hit
- strike
- smash
- swing
- punch
- kick
- tackle
- slam
- buttstock
- pistol-whip
- knock out
- knock unconscious
- incapacitate

Add target-aware patterns for social coercion:

- demand
- order
- command
- threaten
- warn
- tell him to stop
- make him stop
- force him to
- at gunpoint

Avoid false positives such as "hit the road".

When a committed melee or social declaration is detected, roll the appropriate skill before narration and provide the result to the LLM as an outcome packet.

3. COMBAT AND CONDITION TRUTH

A melee attack must resolve through deterministic combat logic, not narration alone.

For the buttstock/knockout case:

- roll Fighting Brawl or the appropriate combat skill;
- account for the intended nonlethal/knockout outcome;
- resolve target response if applicable;
- roll/apply damage through the engine;
- update HP, major wound, unconscious, dying, or other condition through engine-owned logic;
- only then allow the LLM to narrate the result.

The model must not mark Mr. Hobbs unconscious unless the engine has marked him unconscious.

4. OBJECT ATTACKS, AMMUNITION, AND EXITS

For "blast the door lock off, then kick it in":

- consume one shell;
- check malfunction where applicable;
- resolve the firearm attack against the door/lock object;
- update the door object state deterministically;
- update the exit state from locked to open/broken when appropriate;
- emit the correct noise event;
- only use the kick/force component if the door remains closed;
- make local movement available immediately after the object state changes.

After the successful shotgun blast, "enter", "enter study", or "go to study" should resolve locally if the exit is now passable.

5. FIRST-VISIT CONTINUITY

Add explicit room-visit state to the prompt/outcome packet:

- visit_count
- first_visit or revisit
- whether the acting character has personally seen the room
- known occupants
- known items
- known facts

On a first visit, forbid continuity language such as:

- "back"
- "still"
- "again"
- "hasn't moved"
- "where you left it"
- "familiar"
- "you keep checking"

unless prior campaign facts justify it.

6. ITEM LOCATION TRUTH

The model must not move physical items through prose.

The Torn Letter may not drift from desk to floor to boot unless the engine records an item transfer or object-state event. Scene prompts must include canonical item locations, and the LLM must describe items where the registry says they are.

7. NARRATION HYGIENE

Remove generic meta command lists from normal narration.

The model should offer fiction-first choices, not lines like:

"Available verbs: inventory, equip, unequip..."

Only show command syntax after invalid commands or explicit help requests.

8. VERSION RECONCILIATION

The game reports v2.8.1, while the prior engineering report used v2.8.0-stamped benchmark rows. Reconcile the actual version across:

- src/__init__.py
- AGENTS.md
- README.md
- release notes
- timing rows
- handoff documentation

Explain which features belong to v2.8.0.1 versus v2.8.1.

9. REGRESSION TESTS

Add tests proving:

- "unequip 12-gauge shotgun" resolves locally and calls no LLM;
- bare "take" lists visible items and calls no LLM;
- bare "enter" lists or selects valid exits and calls no LLM;
- "use letter" suggests take/read/look when the letter is visible but not carried;
- "read letter" works for a visible document;
- buttstock/knockout declarations trigger Fighting Brawl before narration;
- demand/threat declarations trigger Intimidate before narration;
- combat applies actual damage/condition;
- shooting a door consumes ammo and updates object/exit state;
- movement is local after the door is broken;
- first-visit prompts forbid revisit-only continuity language;
- item locations cannot drift through narration;
- inventory does not print "None";
- local commands do not consume a turn;
- the LLM cannot directly move items or set NPC unconsciousness.

10. VALIDATION

Run:

py test_engine.py
py test_charcreate.py
py test_items.py
py test_latency.py --report

Then run a mock five-minute-house transcript covering:

1. help
2. inventory
3. unequip shotgun
4. enter hallway
5. observe
6. blast door lock off, then kick it in
7. observe
8. enter
9. look at letter
10. read letter
11. take
12. take letter
13. inventory


NOTE: 4096 should be the new standard for budget size, please replace that 

FINAL REPORT:
Return:
1. files changed;
2. command parser fixes;
3. preroll patterns added;
4. combat/object truth fixes;
5. first-visit prompt fixes;
6. tests added;
7. test results;
8. remaining known issues;
9. whether the five-minute-house field test is now clean.