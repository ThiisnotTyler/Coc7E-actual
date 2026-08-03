# README-WORLDBUILDING — Build Your Own Haunted House

*A friendly guide for Keepers, writers, and players who want to build custom
scenarios, items, rooms, and NPCs for the CoC 7e LLM Keeper — no programming
required. Everything here is written in plain English. Where a technical word
is unavoidable, it is explained the first time it appears.*

The game reads its worlds from **one folder per scenario**, each containing
**one text file** called `scenario.json`. That file is just structured text
(you can edit it in Notepad). If you can write a grocery list with curly
braces, you can build a haunted house.

---

## A. What You Can Build

With a single `scenario.json` you can create:

- **Haunted houses, cult compounds, mystery locations** — rooms connected by
  doors, stairs, and secret passages.
- **Custom investigators** — with the built-in wizard
  (`py -m src.main --new-character`), no editing needed.
- **Weapons** — revolvers, shotguns, knives, clubs, with real CoC 7e damage
  dice and ranges.
- **Ammunition** — boxes of cartridges and shells that actually run out.
- **Keys** — small items that open specific locked things.
- **Locked doors and containers** — that stay locked until someone brings the
  right key.
- **Containers** — cabinets, chests, safes.
- **Documents and clue items** — letters, ledgers, notebooks players can pick
  up and read.
- **Consumables** — whiskey, bandages, things that get used up.
- **Light sources** — flashlights that switch on and off.
- **NPCs** — people (and worse) with stats, skills, carried items, and an
  attitude.
- **Fronts and timeline pressure** — the bad thing getting worse on a clock,
  and scheduled events that fire on later turns.

---

## B. The Five-Minute Scenario

This complete, working scenario ships with the game, ready to play in mock
mode. It has one house exterior, one hallway, one locked study, one key, one
clue document, and one NPC.

Folder structure:

```text
data/
└── scenarios/
    └── five-minute-house/
        └── scenario.json      <- the whole world lives in this one file
```

The exact, complete file (this is what the game loads — the offline test
suite checks that this copy matches the shipped file, so it can never go
stale):

```json five-minute-scenario
{
  "id": "five-minute-house",
  "title": "The Five-Minute House",
  "era": "1920s",
  "expected_sessions": 1,
  "description": "A teaching scenario from README-WORLDBUILDING.md: one exterior, one hallway, one locked study, one key, one clue, one NPC.",
  "starting_location": "house_exterior",
  "fronts": {
    "the_cold": {
      "name": "The Cold Inside",
      "clock": 0,
      "max": 4,
      "triggers": [
        {"clock": 4, "event": "Every candle in the house gutters out at once"}
      ]
    }
  },
  "locations": {
    "house_exterior": {
      "name": "Outside the House",
      "description": "A narrow terrace house with curtains that never move.",
      "first_visit": "Rain has polished the front steps. The door knocker is a brass hand, and the fingers are a little too long.",
      "revisit": "The brass hand knocker waits where you left it. The rain has not stopped.",
      "details": {
        "knocker": "A brass hand, green with age, fingers slightly too long."
      },
      "lighting": "grey daylight",
      "tags": [],
      "connections": {
        "house_hallway": {"type": "door", "state": "open"}
      },
      "sound_propagation": {},
      "line_of_sight": [],
      "occupants": []
    },
    "house_hallway": {
      "name": "Hallway",
      "description": "A narrow hallway that smells of dust and dried lavender.",
      "first_visit": "The door sighs shut behind you. Dust hangs in the air like it was disturbed a moment ago, and under the dust, dried lavender.",
      "revisit": "Dust and lavender. The study door waits at the end of the hall.",
      "details": {
        "runner_rug": "A threadbare runner rug, worn more on the left side, as if something is often dragged toward the study."
      },
      "lighting": "dim",
      "tags": [],
      "connections": {
        "house_exterior": {"type": "door", "state": "open"},
        "house_study": {"type": "door", "state": "locked", "key_id": "brass_key", "object_id": "study_door"}
      },
      "sound_propagation": {
        "house_study": "muffled"
      },
      "line_of_sight": [],
      "occupants": []
    },
    "house_study": {
      "name": "Study",
      "description": "A cramped study. Every flat surface carries stacks of paper covered in tally marks.",
      "first_visit": "The lamp is still warm. Tally marks cover every page, every envelope, the margin of the newspaper — and they stop mid-stroke.",
      "revisit": "The tally marks have not moved. You check. You keep checking.",
      "details": {
        "tally_marks": "Groups of five, over and over. The last group has only four."
      },
      "lighting": "lamplight",
      "tags": [],
      "connections": {
        "house_hallway": {"type": "door", "state": "open"}
      },
      "sound_propagation": {
        "house_hallway": "muffled"
      },
      "line_of_sight": [],
      "occupants": []
    }
  },
  "items": [
    {
      "id": "brass_key",
      "name": "Brass Key",
      "item_type": "key",
      "tags": ["key"],
      "description": "A heavy brass key, recently oiled."
    },
    {
      "id": "torn_letter",
      "name": "Torn Letter",
      "item_type": "document",
      "tags": ["document", "clue"],
      "description": "Half a letter. The ink has run where it got wet: '...do not let him finish the counting...'"
    }
  ],
  "placed_items": [
    {"template": "brass_key", "location": "house_hallway"},
    {"template": "torn_letter", "location": "house_study"}
  ],
  "objects": [
    {
      "id": "study_door",
      "name": "Study Door",
      "location_id": "house_hallway",
      "object_type": "door",
      "state": "closed",
      "properties": {"locked": true, "key_id": "brass_key"},
      "tags": ["door"]
    }
  ],
  "npcs": [
    {
      "id": "mr_hobbs",
      "name": "Mr Hobbs",
      "characteristics": {"STR": 40, "CON": 45, "SIZ": 50, "DEX": 50, "APP": 55, "INT": 70, "POW": 55, "EDU": 65},
      "skills": {"Accounting": 70, "Library_Use": 55, "Spot_Hidden": 45},
      "hp": 9,
      "san": 38,
      "location": "house_study",
      "attitude": "nervous but polite"
    }
  ],
  "clues": [
    {
      "id": "the_counting",
      "name": "The Counting",
      "location": "house_study",
      "skill": "Spot_Hidden",
      "difficulty": "Regular",
      "type": "CORE",
      "plot_point": "hobbs_counts_the_missing",
      "visible": true
    }
  ],
  "timeline": [
    {"turn": 6, "event": "The knocking starts under the floor"}
  ]
}
```

Play it right now, offline, for free:

```bat
py -m src.main --mock --scenario data/scenarios/five-minute-house
```

---

## C. How the World Is Organized

Seven ideas. That's the whole map.

- **Locations are rooms.** A house exterior, a hallway, a crypt. Each room has
  an `id` (a short code like `house_hallway`, used everywhere else) and a
  `name` (what players see, like `Hallway`).
- **Connections are doors, hallways, and exits.** Each room lists which rooms
  it connects to. A connection can be `open`, `closed`, `locked`, `blocked`,
  `hidden`, or `destroyed`.
- **Items are physical things.** Keys, letters, revolvers, whiskey. They can
  sit in a room or be carried. Players pick them up with `take <name>`.
- **Objects are important scenery.** Doors, cabinets, things players interact
  with but don't pocket. They have a `state` (open, closed, locked, broken...)
  that the game remembers.
- **NPCs are characters.** People with stats and skills who stand in rooms.
  The engine owns their bodies; the AI narrator gives them voices.
- **Fronts are the bad thing getting worse.** A countdown clock. When it fills
  up, something terrible happens.
- **Clues are what the players can discover.** Each clue lives in a room and
  points at a revelation.

The golden rule of this game engine: **the engine owns truth, the AI owns
voice.** If your scenario file says the study is locked, it is locked — the
narrator can describe the locked door beautifully, but it cannot wish it open.

---

## D. Create Your First Room

Rooms live in the `locations` section. Every field except `name` is optional —
missing fields fall back to safe defaults, so old scenarios keep working.

```json
"house_cellar": {
  "name": "Cellar",
  "description": "A low cellar that smells of wet coal.",
  "first_visit": "The steps creek the whole way down. Something down here is dripping, slowly, like it has all the time in the world.",
  "revisit": "The dripping continues. Patient as ever.",
  "details": {
    "coal_pile": "A slumped pile of coal. Someone has been digging in it."
  },
  "connections": {
    "house_hallway": {"type": "stairs", "state": "open"}
  },
  "lighting": "one swaying bulb",
  "tags": []
}
```

- **`id`** — the key on the left (`house_cellar`). Used by connections, NPCs,
  clues, and `starting_location`. Lowercase, underscores, no spaces.
- **`name`** — what players see.
- **`description`** — the stable room text. Shown whenever nothing more
  specific applies.
- **`first_visit`** — shown the first time this character sees the room.
- **`revisit`** — shown on later visits. Leave it out and players always get
  `description`.
- **`details`** — named public details shown with the room. Never hide secrets
  here — everything in `details` is visible to any player who looks.
- **`connections`** — the exits (see below).
- **`objects`** — not written inside the room; objects live in the top-level
  `objects` list and name their room with `location_id` (see section F).
- **`lighting`** — a short phrase (`"dim"`, `"pitch black"`, `"lamplight"`).
- **`span`** — how big the room is, for distance math: `"small"`, `"medium"`,
  or `"large"` (leave it out for `"medium"`). A large room (a gymnasium, a
  rifle gallery, an open field) spreads positions out — being `far` from
  someone is about 30 yards instead of 10, so guns reach further and fists
  reach less. A small room halves the distances. Melee range itself never
  changes: a fight is a fight, whatever the room.
- **`tags`** — labels the engine reacts to. `"hazard"` or `"trap"` makes
  entering the room a dramatic moment (the AI narrator takes over). `"mythos"`
  or `"san"` marks SAN-pressure rooms.

**Connection fields:**

```json
"house_study": {"type": "door", "state": "locked", "key_id": "brass_key", "object_id": "study_door"}
```

- **`type`** — flavor: `door`, `stairs`, `ladder`, ...
- **`state`** — `open` (default), `closed`, `locked`, `blocked`, `hidden`,
  `destroyed`. Locked needs `key_id`; hidden exits are invisible; destroyed
  exits are passable (the barrier is gone).
- **`key_id`** — the `id` of the item template that unlocks this exit.
- **`object_id`** — link this exit to a door object so the door and the exit
  always agree (see section F).
- **`travel_time`**, **`noise_level`** — optional flavor used by older fields.
- **`one_way`** — optional label. Exits are one-directional anyway: to make a
  one-way passage, simply don't define the return connection.

**Two-way rule:** connections are not automatic in both directions. If the
hallway connects to the cellar, the cellar must also connect back to the
hallway, or players who go down can't come up.

---

## E. Create Your First Item

Items are defined as **templates** (the *kind* of thing) in the `items`
section, then **placed** in rooms or given to characters with `placed_items`.
One template can produce many physical copies.

Required fields for every item: **`id`**, **`name`**, **`item_type`**.
Everything else is optional. Players see the `name` and, when they
`look at` it, the `description`.

```json
"items": [
  {"id": "brass_key", "name": "Brass Key", "item_type": "key",
   "tags": ["key"], "description": "A heavy brass key, recently oiled."}
],
"placed_items": [
  {"template": "brass_key", "location": "house_hallway"}
]
```

**A weapon:**

```json
{"id": "old_revolver", "name": "Old Revolver", "item_type": "weapon",
 "tags": ["weapon", "firearm", "handgun"], "skill_key": "Firearms_Handgun",
 "damage": "1D8", "base_range": 15, "rof": 1, "ammo_capacity": 6,
 "malfunction": 100, "ammo_type": "handgun",
 "description": "Pitted steel, but the action is smooth."}
```

`skill_key` picks the roll (`Firearms_Handgun`, `Firearms_Rifle_Shotgun`,
`Fighting_Brawl`); `damage` is CoC dice notation; `base_range` in yards (0 =
melee); `malfunction` is the d100 jam threshold (100 = never).

**Ammunition:**

```json
{"id": "pistol_rounds", "name": "Pistol Rounds", "item_type": "ammo",
 "tags": ["ammo"], "stackable": true, "max_stack": 20, "ammo_type": "handgun",
 "description": "A cardboard box of cartridges."}
```

`ammo_type` must match the weapon (`handgun`, `shotgun`, or `generic` which
fits anything). Players load it with `reload <weapon>`.

**A key:**

```json
{"id": "cellar_key", "name": "Cellar Key", "item_type": "key",
 "tags": ["key"], "description": "Cold iron, heavy for its size."}
```

The `id` is what `key_id` on doors and locked exits refers to.

**A document / clue item:**

```json
{"id": "torn_letter", "name": "Torn Letter", "item_type": "document",
 "tags": ["document", "clue"],
 "description": "Half a letter. The ink has run where it got wet..."}
```

Players can `take` it and `look at` it. What it *means* is the clue system —
see section G.

**A consumable:**

```json
{"id": "laudanum", "name": "Laudanum", "item_type": "consumable",
 "tags": ["medicine"], "description": "A brown glass bottle."}
```

`use <item>` consumes one dose. (Healing and other mechanical effects are
narrated by the Keeper for now — the engine tracks the doses.)

**A light source:**

```json
{"id": "storm_lantern", "name": "Storm Lantern", "item_type": "light_source",
 "tags": ["tool", "light"], "default_state": {"on": false},
 "description": "It rattles when you walk."}
```

`use <item>` toggles it on and off.

**Placing and hiding:** `placed_items` accepts `template`, `location`,
`owner`, `quantity`, `name`, and `tags`. Add `"tags": ["hidden"]` and the item
is invisible until the story reveals it:

```json
{"template": "torn_letter", "location": "house_study", "tags": ["hidden"]}
```

---

## F. Create a Locked Door and Key

The complete recipe, in six steps:

**1. Create the key item** (in `items`):

```json
{"id": "brass_key", "name": "Brass Key", "item_type": "key",
 "tags": ["key"], "description": "A heavy brass key, recently oiled."}
```

**2. Place the key somewhere** (in `placed_items`):

```json
{"template": "brass_key", "location": "house_hallway"}
```

**3. Create the door object** (in the top-level `objects` list):

```json
{"id": "study_door", "name": "Study Door", "location_id": "house_hallway",
 "object_type": "door", "state": "closed",
 "properties": {"locked": true, "key_id": "brass_key"}, "tags": ["door"]}
```

**4. Connect the object to the exit** (in the hallway's `connections`):

```json
"house_study": {"type": "door", "state": "locked",
                "key_id": "brass_key", "object_id": "study_door"}
```

`object_id` links the exit and the door so they can never disagree: unlock
one and the other knows. (Also add the return connection inside the study:
`"house_hallway": {"type": "door", "state": "open"}`.)

**5. Test taking the key** — run mock mode and type:

```text
take brass key
```

You should see `[You takes the Brass Key.]` (with your investigator's name).

**6. Test opening the locked thing** — two ways now work:

- `open study door` (uses the key, opens the door object), or
- `enter the study` — the engine sees your key, unlocks the exit, and moves
  you through.

Try `enter the study` *before* taking the key, too: you should be refused and
shown your real exits.

---

## G. Create a Clue

Two layers, and it is important to know which is which.

**Implemented today:**

- **Clue items** — documents and objects players can find, `take`, and
  `look at` (section E). This is fully working.
- **Clue entries** — the top-level `clues` list records each clue's id, room,
  skill, difficulty, and the plot point it unlocks:

```json
"clues": [
  {"id": "the_counting", "name": "The Counting", "location": "house_study",
   "skill": "Spot_Hidden", "difficulty": "Regular", "type": "CORE",
   "plot_point": "hobbs_counts_the_missing", "visible": true}
]
```

- **`"visible": true`** — the room-reveal hook: the first time someone enters
  the clue's room, the engine marks the clue discovered and treats the entry
  as a dramatic moment (the AI narrator describes what they stumble into).
  Leave `visible` out and the clue stays quiet.

**Future-only (not implemented yet — do not rely on them):**

- **Formal clue triggers** — rolling Spot Hidden in the right room to earn a
  specific clue arrives in v2.8.4. The `skill` and `difficulty` fields above
  are stored for that phase; today they are informational.
- **Facts and character knowledge** — tracking who knows what (v2.8.3).

---

## H. Create an NPC

```json
"npcs": [
  {
    "id": "mr_hobbs",
    "name": "Mr Hobbs",
    "characteristics": {"STR": 40, "CON": 45, "SIZ": 50, "DEX": 50,
                        "APP": 55, "INT": 70, "POW": 55, "EDU": 65},
    "skills": {"Accounting": 70, "Library_Use": 55, "Spot_Hidden": 45},
    "hp": 9,
    "san": 38,
    "location": "house_study",
    "attitude": "nervous but polite"
  }
]
```

- **`name`, `location`, `characteristics`, `skills`** — the body. `hp` and
  `san` are optional (derived from the characteristics if omitted).
- **`attitude`** — a free-text note the narrator reads (`"hostile"`,
  `"terrified but willing to talk"`).
- **Carried items** — give the NPC a weapon with a `weapon` block
  (`"weapon": {"name": "Knife", "damage": "1D4", "base_range": 0}`), or place
  items on them with `placed_items` using `"owner": "mr_hobbs"`.

**What is deterministic today:** where the NPC stands, their stats, their
health, their carried items, and combat math when dice are rolled. Entering
their room is automatically a dramatic moment handled by the narrator.

**What is still LLM-narrated:** everything the NPC says, wants, and decides.
NPC objectives, awareness, and routines arrive with the NPC Director phase
(v2.8.5) — until then the AI plays them, bounded by the engine's truths.

---

## I. Scenario Checklist

Before you share your scenario, walk this list:

- [ ] Every room has an `id`, and `starting_location` points at a real one.
- [ ] Every connection points to a room that exists.
- [ ] Every room players must reach is reachable (walk it in mock mode).
- [ ] Every item template has a unique `id`.
- [ ] Every `key_id` matches the `id` of a real key item.
- [ ] Every NPC has a `location` that is a real room.
- [ ] Every clue is in a room players can actually enter.
- [ ] No required key is inside the locked thing it opens.
- [ ] No secrets are written in `description`, `details`, or object names —
      those are always public.
- [ ] Return connections exist for every two-way passage.
- [ ] No API keys anywhere in the folder (keys live only in
      `config/api-key.json`).
- [ ] The scenario loads in mock mode (section K) and `py test_engine.py`
      is still green.

---

## J. Common Mistakes

- **Typo in a room id.** `"connections": {"house_celar": ...}` — the exit
  silently vanishes. Walk every exit in mock mode.
- **Two items with the same id.** The second template overwrites the first.
  Keep ids unique across the whole scenario.
- **The key is inside the locked container it opens.** Classic. The key to
  the cellar is *in the cellar*. Nobody plays your scenario ever again.
- **Forgetting the return connection.** `A → B` does not give you `B → A`.
  Players walk in and can't walk out.
- **Forgetting the player start.** `starting_location` must be one of your
  room ids, or investigators spawn in the void.
- **Making a clue unreachable.** A clue in a room behind a blocked exit with
  no other way in is a clue that doesn't exist.
- **Putting secrets in public descriptions.** `description`, `details`, item
  names, and object names are shown to anyone who looks. The butler's guilt
  does not go in the hallway text.
- **Using display names where ids are required.** `key_id`, `object_id`,
  `location`, `owner`, and `starting_location` all want the short `id` code
  (`brass_key`, `house_study`), not the pretty name (`Brass Key`).
- **Typing `exit` to leave a room.** `exit` (with `quit`/`save`) leaves the
  *game*. To leave a room, say `leave`, `go back`, or `enter the hallway`.

---

## K. Testing Your World

All offline, no API key, no cost. Run from the project root.

```bat
:: load your scenario in mock mode (offline narrator)
py -m src.main --mock --scenario data/scenarios/five-minute-house
```

Then, at the prompts:

```text
help                    show every command
observe                 look at the room (also: look, look around)
enter the hallway       walk through an exit
go back                 retrace your last step
take brass key          pick something up
inventory               check your pockets
open study door         use the key on the door
enter the study         walk through (locked without the key!)
look at torn letter     read a document
save                    save and leave (also: quit)
```

Resume exactly where you left off — the save remembers visited rooms, unlocked
doors, and moved items:

```bat
py -m src.main --mock --scenario data/scenarios/five-minute-house
```

And run the offline suite to make sure the game itself is healthy:

```bat
py test_engine.py
py test_charcreate.py
py test_items.py
```

`test_engine.py` also loads every scenario in `data/scenarios/` — including
yours — and checks that it parses, that the starting room exists, and that
ids are unique. If the guide's example above ever drifts from the shipped
five-minute scenario, the suite fails on purpose.

---

## L. Worldbuilding Style Tips

- **Give every location one memorable sensory detail.** The too-long fingers
  on the brass knocker. Lavender under the dust. One is enough; three is mud.
- **Do not over-explain the mystery in room text.** Show the tally marks;
  don't say who made them or why. Rooms ask questions. Clues answer them.
- **Put useful clues in more than one place.** Players miss things. A second
  copy of the revelation — a letter and a ledger — keeps the mystery fair.
- **Make locked things point toward alternate routes.** A locked study is
  interesting when the key is *somewhere interesting*, not when it's a wall.
- **Use fronts to create pressure.** A clock that fills while players dawdle
  turns a slow mystery into a race.
- **Let NPCs want something.** Even one line of `attitude` ("nervous but
  polite") gives the narrator a spine to build on.
- **Reward investigation instead of hiding all progress behind one roll.**
  If one failed Spot Hidden can end your scenario, the dice are playing the
  game, not your players.

---

## M. Copy/Paste Templates

**scenario.json (skeleton):**

```json
{
  "id": "my-scenario",
  "title": "My Scenario",
  "era": "1920s",
  "expected_sessions": 1,
  "description": "One-line hook for the menu.",
  "starting_location": "room_one",
  "fronts": {},
  "locations": {},
  "items": [],
  "placed_items": [],
  "objects": [],
  "npcs": [],
  "clues": [],
  "timeline": []
}
```

**Location:**

```json
"room_one": {
  "name": "Room One",
  "description": "",
  "first_visit": "",
  "revisit": "",
  "details": {},
  "connections": {"room_two": {"type": "door", "state": "open"}},
  "lighting": "",
  "tags": [],
  "sound_propagation": {},
  "line_of_sight": [],
  "occupants": []
}
```

**Item (plain):**

```json
{"id": "my_item", "name": "My Item", "item_type": "misc",
 "tags": [], "description": ""}
```

**Weapon:**

```json
{"id": "my_weapon", "name": "My Weapon", "item_type": "weapon",
 "tags": ["weapon", "melee"], "skill_key": "Fighting_Brawl",
 "damage": "1D6", "base_range": 0, "rof": 1, "malfunction": 100,
 "ammo_type": null, "description": ""}
```

**Ammunition:**

```json
{"id": "my_ammo", "name": "My Ammo", "item_type": "ammo",
 "tags": ["ammo"], "stackable": true, "max_stack": 20,
 "ammo_type": "generic", "description": ""}
```

**Key:**

```json
{"id": "my_key", "name": "My Key", "item_type": "key",
 "tags": ["key"], "description": ""}
```

**Document / clue item:**

```json
{"id": "my_document", "name": "My Document", "item_type": "document",
 "tags": ["document", "clue"], "description": ""}
```

**Container (object):**

```json
{"id": "my_chest", "name": "My Chest", "location_id": "room_one",
 "object_type": "container", "state": "closed",
 "properties": {"locked": true, "key_id": "my_key"}, "tags": ["container"]}
```

**NPC:**

```json
{"id": "my_npc", "name": "My NPC",
 "characteristics": {"STR": 50, "CON": 50, "SIZ": 50, "DEX": 50,
                     "APP": 50, "INT": 50, "POW": 50, "EDU": 50},
 "skills": {}, "location": "room_one", "attitude": ""}
```

**Front:**

```json
"my_front": {
  "name": "The Bad Thing",
  "clock": 0,
  "max": 6,
  "triggers": [{"clock": 3, "event": "It gets worse"},
               {"clock": 6, "event": "It arrives"}]
}
```

**Timeline event:**

```json
{"turn": 10, "event": "Something scheduled happens"}
```

---

## N. What Is Coming Later

These are **future-only** — planned, not implemented. Do not build scenarios
that depend on them yet:

- **Formal clue triggers** (v2.8.4) — skill rolls in the right room earning
  specific clues. (`skill`/`difficulty` on clues are stored for this.)
- **Facts and character knowledge** (v2.8.3) — who knows what, beliefs, lies.
- **NPC objectives and awareness** (v2.8.5, the NPC Director) — NPCs with
  routines and goals of their own.
- **Advanced action grammar** — richer parsing of what players declare.
- **Multiplayer** (v3.0) — multiple players with separate screens and
  knowledge.

Build for the world as it is: rooms, exits, items, objects, NPCs, fronts,
and clues. The rest is gravy that hasn't been cooked yet.
