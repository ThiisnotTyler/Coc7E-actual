"""Command interpreter (v2.8.1.x) — decoupled from keeper.py.

Every player input that is NOT a narrative declaration is resolved here:
system-channel gear commands (inventory/equip/unequip/take/drop/give/
reload/open/read/use/look at), free information commands (observe/look,
distance), bare and numbered menu forms ('take' -> 'take 1', 'enter' ->
'2') with their owner-routing rules, and the normalization layer that
keeps natural arguments from leaking to the LLM as declarations.

Nothing here narrates: a command either resolves deterministically
against engine state (items, room_view, exits) or hands off to
keeper.take_turn / keeper._meta_move when the moment escalates. Functions
take `keeper` explicitly (the local_voice.py / narration_validator.py
pattern). CoCKeeper keeps one-line delegates for the methods called from
outside this module (run_session, take_turn, action_resolver, and the
test surface — _meta_command alone has ~70 test references).

This module is the seam a future UI layer talks to: input text in,
engine-resolved outcome out, no terminal ownership beyond print().
"""
import re
from typing import List, Optional

from src.character import Character
from src import items as items_mod
from src import room_view
from src import status_view

# Leading articles are noise in command arguments ('take the key').
_ARTICLE = re.compile(r"^(?:the|a|an)\s+")


def _cmd_observe(keeper, char: Character):
    """Local observation: deterministic room view, no LLM, no turn."""
    view = room_view.build_room_view(keeper, char)
    print(room_view.render_room_text(view))
    keeper.mark_visited(char.id, char.location)

def _cmd_distance(keeper, char: Character):
    """Local range readout (v2.8.1.x player request): no LLM, no turn,
    no menus. Distances drive range bands, which drive skill targets —
    the player deserves them BEFORE committing to an attack."""
    others = [c for c in keeper.characters.values()
              if c.id != char.id and c.location == char.location
              and not c.extra.get("hidden")]
    if not others:
        print("  [Nobody else here to measure against.]")
        return
    weapon = char.weapon
    skill_name = base_skill = None
    if weapon is not None and weapon.base_range > 0:
        _inst = (items_mod.get_instance(char.equipped_item_id)
                 if char.equipped_item_id else None)
        _tmpl = items_mod.get_template(_inst.template_id) if _inst else None
        skill_name = items_mod.firearm_skill_key(weapon, _tmpl)
        base_skill = keeper._skill_target(char, skill_name)
    for c in sorted(others,
                    key=lambda x: keeper.combat.calc_distance(char, x)):
        dist = keeper.combat.calc_distance(char, c)
        reach = ("in striking reach" if dist <= 3
                 else "out of melee reach")
        unit = "yard" if round(dist) == 1 else "yards"
        line = (f"    {c.name} — {c.position}, ~{dist:.0f} {unit} — "
                f"{reach}")
        if skill_name is not None:
            band = weapon.get_range_band(dist, char.DEX)
            eff = weapon.get_skill_target(base_skill, band)
            pretty = skill_name.replace("_", " ")
            if band == "point_blank":
                line += (f"; {weapon.name}: point blank "
                         f"(full skill {eff}%) — bonus die")
            elif band == "regular":
                line += (f"; {weapon.name}: regular range "
                         f"(full skill {eff}%)")
            elif band == "long":
                line += (f"; {weapon.name}: long range "
                         f"(half skill {eff}%)")
            elif band == "extreme":
                line += (f"; {weapon.name}: extreme range "
                         f"(fifth skill {eff}%)")
            else:
                line += f"; {weapon.name}: out of range"
        print(line)

# v2.8.1.x Phase 2: stance aliases -> engine values. None = engine policy.
_STANCE_ALIASES = {
    "dodge": "dodge",
    "fight back": "fight_back", "fightback": "fight_back",
    "fight-back": "fight_back", "fight": "fight_back",
    "none": "none", "no defense": "none", "drop guard": "none",
    "auto": None, "clear": None, "default": None,
}
_STANCE_DISPLAY = {
    "dodge": "Dodge",
    "fight_back": "Fight Back",
    "none": "no defense",
}

def _cmd_stance(keeper, char: Character, text: str):
    """Player melee-defense stance (v2.8.1.x Phase 2): engine-owned state,
    free local command — no LLM, no turn. Opposed melee consumes it via
    CombatEngine.defender_stance."""
    arg = text.strip()[len("stance"):].strip().lower()
    if not arg:
        current = char.stance
        if current is None:
            policy = keeper.combat.defender_stance(char)
            print(f"  [{char.name}'s stance: auto — the engine picks "
                  f"({_STANCE_DISPLAY.get(policy, policy)} by skill). "
                  f"'stance dodge' / 'stance fight back' / 'stance none' "
                  f"to choose.]")
        else:
            print(f"  [{char.name}'s stance: "
                  f"{_STANCE_DISPLAY[current]}. "
                  f"'stance auto' returns to the engine policy.]")
        return
    if arg not in _STANCE_ALIASES:
        print("  [Stance must be: dodge, fight back, none, or auto.]")
        return
    char.stance = _STANCE_ALIASES[arg]
    if char.stance is None:
        print(f"  [{char.name} returns to an automatic defense — "
              f"the engine picks Dodge or Fight Back by skill.]")
    elif char.stance == "dodge":
        print(f"  [{char.name} will Dodge when attacked in melee.]")
    elif char.stance == "fight_back":
        print(f"  [{char.name} will Fight Back when attacked in melee.]")
    else:
        print(f"  [{char.name} drops their guard — no melee defense.]")

def _exit_list(keeper, char: Character) -> str:
    exits = room_view.visible_exits(keeper.locations, char.location,
                                    keeper.world_objects)
    if not exits:
        return "none that you can see"
    return "; ".join(
        e["name"] + (f" [{e['state']}]" if e["state"] != "open" else "")
        for e in exits)

def _iname(keeper, iid: str) -> str:
    inst = keeper.item_instances.get(iid)
    return inst.name if inst is not None else iid

def _find_carried_item(keeper, char: Character, arg: str) -> Optional[items_mod.ItemInstance]:
    low = _ARTICLE.sub("", arg.lower().strip())
    # equipped first, then inventory
    for iid in ([char.equipped_item_id] if char.equipped_item_id else []) + list(char.inventory):
        if not iid:
            continue
        inst = keeper.item_instances.get(iid)
        if inst is None:
            continue
        if inst.name.lower() == low or low in inst.name.lower():
            return inst
    return None

def _find_room_item(keeper, char: Character, arg: str) -> Optional[items_mod.ItemInstance]:
    low = _ARTICLE.sub("", arg.lower().strip())
    for inst in keeper.item_instances.values():
        if inst.owner_id is None and inst.location_id == char.location:
            if inst.name.lower() == low or low in inst.name.lower():
                return inst
    return None

def _find_room_object(keeper, char: Character, arg: str) -> Optional[items_mod.WorldObject]:
    low = arg.lower()
    for obj in keeper.world_objects.values():
        if obj.location_id == char.location:
            if obj.name.lower() == low or low in obj.name.lower():
                return obj
    return None

def _find_character_in_room(keeper, char: Character, arg: str) -> Optional[Character]:
    low = arg.lower()
    for c in keeper.characters.values():
        if c.id == char.id:
            continue
        if c.location != char.location:
            continue
        if c.name.lower() == low or low in c.name.lower() or low in c.id.replace("_", " "):
            return c
    return None

def _show_item(keeper, thing) -> str:
    if thing is None:
        return "nothing"
    if isinstance(thing, items_mod.ItemInstance):
        extra = ""
        if thing.condition != "intact":
            extra += f" [{thing.condition}]"
        if thing.ammo is not None:
            extra += f" ({thing.ammo} rounds)"
        return f"{thing.name}{extra}"
    if isinstance(thing, items_mod.WorldObject):
        state = thing.state
        props = ", ".join(f"{k}={v}" for k, v in thing.properties.items())
        if props:
            return f"{thing.name} [{state}; {props}]"
        return f"{thing.name} [{state}]"
    return str(thing)

# -------------------------------------- v2.8.1.1 command normalization
def _visible_room_items(keeper, char: Character) -> list:
    return [inst for inst in keeper.item_instances.values()
            if inst.location_id == char.location and inst.owner_id is None
            and "hidden" not in inst.tags]

def _carried_items(keeper, char: Character) -> list:
    return [keeper.item_instances[iid] for iid in char.inventory
            if keeper.item_instances.get(iid) is not None]

def _openable_things(keeper, char: Character) -> list:
    things = [o for o in keeper.world_objects.values()
              if o.location_id == char.location
              and o.state not in ("open", "hidden", "broken", "destroyed")]
    things += [i for i in _visible_room_items(keeper, char)
               if i.item_type == "container" and not i.state.get("open")]
    return things

def _readable_things(keeper, char: Character) -> list:
    return [i for i in _carried_items(keeper, char) + _visible_room_items(keeper, char)
            if i.item_type in ("document", "clue")
            or "document" in i.tags or "clue" in i.tags]

def _notable_things(keeper, char: Character) -> list:
    things = list(_visible_room_items(keeper, char))
    things += [o for o in keeper.world_objects.values()
               if o.location_id == char.location and o.state != "hidden"]
    things += [c for c in keeper.characters.values()
               if c.id != char.id and c.location == char.location
               and not c.extra.get("hidden")]
    return things

def _store_menu(keeper, char: Character, kind: str, ids: list, **extra):
    # v2.8.1.7 P0-3: pending menus carry their OWNER
    # (pending_action_owner_character_id). In hotseat play anyone may
    # type the answer, but the result always applies to the owner; a
    # future remote client may only answer its own pending menus.
    # v2.8.1.x: extra payload (e.g. verb for attack-target menus).
    char.extra["_last_menu"] = {"kind": kind, "ids": list(ids),
                                "owner": char.id, **extra}

def _answer_attack_menu(keeper, owner: Character, menu: dict, n: int) -> bool:
    """Resolve a pending attack-target menu pick (v2.8.1.x field fix).

    The numbered answer replays the original attack verb against the
    CHOSEN target as a fresh engine turn — the attack is never resolved
    against a guessed target. A throw menu also carries the instrument,
    so the same knife is the one that flies. The menu is consumed
    either way."""
    pick = _menu_pick(keeper, owner, "attack", n)
    verb = (menu or {}).get("verb", "shoot")
    owner.extra.pop("_last_menu", None)
    tgt = keeper.characters.get(pick) if pick else None
    if tgt is None:
        print(f"  [No target {n} — declare the attack again.]")
        return True
    decl = f"{verb} {tgt.name}"
    inst = keeper.item_instances.get((menu or {}).get("instrument_id"))
    if inst is not None:
        decl = f"{verb} {inst.name} at {tgt.name}"
    keeper.take_turn({owner.id: decl})
    return True

def _pending_menu(keeper, char: Character):
    """The pending numbered menu an answer routes to (v2.8.1.7 P0-3).

    Owner-first. Otherwise exactly one pending menu table-wide: a
    hotseat answer from another player applies to the OWNER of the
    pending action — a different actor's input never silently hijacks
    it. Returns (owner_char, menu, routed_from_other).

    v2.8.1.x P0-2: cross-player routing is consulted ONLY by the
    explicit numeric answer forms (bare '2', 'enter 2', 'take 1', ...).
    A new non-numeric declaration or command must never answer another
    player's pending menu."""
    menu = char.extra.get("_last_menu")
    if menu:
        return char, menu, False
    owners = [(c, c.extra.get("_last_menu"))
              for c in keeper.characters.values()
              if c.char_type == "player" and c.extra.get("_last_menu")]
    if len(owners) == 1:
        return owners[0][0], owners[0][1], True
    return char, None, False

def _clear_pending_menus(keeper):
    """v2.8.1.x P0-2: pending menus are runtime-only and die on ANY new
    declaration, on turn completion, and before save (field: Jack's
    resolved 'enter' menu stayed alive and later stole Patrick's
    'enter', moving Jack back out of the Study)."""
    for c in keeper.characters.values():
        c.extra.pop("_last_menu", None)

def _menu_pick(keeper, char: Character, kind: str, n: int):
    menu = char.extra.get("_last_menu") or {}
    ids = menu.get("ids") or []
    if menu.get("kind") != kind or not (1 <= n <= len(ids)):
        return None
    return ids[n - 1]

def _resolve_menu_thing(keeper, owner: Character, kind: str, pick):
    """What a numbered menu pick points at. 'open' menus list WORLD
    OBJECTS (doors) alongside container items, so a pick must resolve
    against the openable pool — not item_instances (v2.8.1.x field fix:
    'open' -> '1' answered 'No selection' for the Range Door). Every
    other kind is an item instance."""
    if not pick:
        return None
    if kind == "open":
        return next((x for x in _openable_things(keeper, owner)
                     if x.id == pick), None)
    return keeper.item_instances.get(pick)

def _print_numbered(keeper, names: list, hint: str):
    for i, name in enumerate(names, 1):
        print(f"    {i}. {name}")
    print(f"  [{hint}]")

def _do_unequip(keeper, char: Character, arg: str = "") -> bool:
    if not char.equipped_item_id:
        print(f"  [{char.name} has nothing in hand.]")
        return True
    name = _iname(keeper, char.equipped_item_id)
    if arg and arg.lower() not in name.lower():
        print(f"  [{char.name} is holding the {name}, not a '{arg}'.]")
        return True
    char.equipped_item_id = None
    char.weapon = None
    print(f"  [{char.name} puts the {name} away.]")
    return True

def _print_read(keeper, char: Character, inst):
    print(f"  [{char.name} reads the {inst.name}.]")
    desc = inst.state.get("text", "")
    tmpl = keeper.item_templates.get(inst.template_id)
    if not desc and tmpl is not None:
        desc = tmpl.description
    if desc:
        print(f"    {desc}")

def _normalize_command(keeper, char: Character, text: str):
    """v2.8.1.1 hotfix: natural arguments for local commands.

    Bare commands list or use their one valid target, numbered selection
    ('take 1') picks from the last listing, unequip aliases resolve, and
    'use <room item>' suggests take/read/look instead of failing blind.
    Returns True when the input was consumed, None for normal dispatch.
    """
    t = " ".join(text.strip().lower().split())
    if not t:
        return None
    cmd = t.split()[0]
    arg = text.strip()[len(cmd):].strip()

    # v2.8.1.1 P0 desync: a bare number selects from the last numbered
    # menu ('go to' then '2'). Before this, bare digits leaked to the LLM
    # as declarations and the model narrated from the origin room.
    # v2.8.1.7 P0-3: the answer routes to the menu's OWNER — Patrick's
    # '2' moves Jack, never Patrick.
    if t.isdigit():
        owner, menu, routed = _pending_menu(keeper, char)
        kind = (menu or {}).get("kind")
        n = int(t)
        if routed and kind:
            menu["answered_by"] = char.id
            print(f"  [menu: {char.name} answered '{n}' for "
                  f"{owner.name}'s pending {kind}.]")
        if kind == "enter":
            pick = _menu_pick(keeper, owner, "enter", n)
            exits = room_view.visible_exits(keeper.locations, owner.location,
                                            keeper.world_objects)
            owner.extra.pop("_last_menu", None)   # answered: consumed
            if pick in {e["id"] for e in exits}:
                keeper._meta_move(owner, pick)
                return True
            print(f"  [No exit {n} — list them again with 'enter'.]")
            return True
        if kind == "attack":
            # v2.8.1.x: the attack resolves against the CHOSEN target.
            return _answer_attack_menu(keeper, owner, menu, n)
        if kind in ("take", "equip", "drop", "reload", "open", "use",
                    "give", "read"):
            pick = _menu_pick(keeper, owner, kind, n)
            thing = _resolve_menu_thing(keeper, owner, kind, pick)
            owner.extra.pop("_last_menu", None)   # answered: consumed
            if thing is not None:
                return _meta_command(keeper, owner, f"{kind} {thing.name}")
            print(f"  [No selection {n} — list them again with '{kind}'.]")
            return True
        return None

    # v2.8.1.x: '<attack verb> <n>' answers a pending attack-target menu
    # ('shoot 1') with the same ownership rules as a bare '1'.
    if arg.isdigit() and cmd in (
            "shoot", "fire", "blast", "hit", "kick", "attack", "strike",
            "punch", "stab", "swing", "smash", "slam", "tackle", "plug",
            "throw", "hurl", "toss"):
        owner, menu, routed = _pending_menu(keeper, char)
        if (menu or {}).get("kind") == "attack":
            if routed:
                menu["answered_by"] = char.id
                print(f"  [menu: {char.name} answered '{cmd} {arg}' for "
                      f"{owner.name}'s pending attack.]")
            return _answer_attack_menu(keeper, owner, menu, int(arg))
        return None   # no attack menu pending: normal declaration path

    # v2.8.1.1 P0: natural pickup aliases. An item transfer is engine
    # truth — the model must never narrate a pickup the engine skipped.
    if cmd in ("grab", "collect", "pocket", "snatch", "pickup") \
            or t.startswith("pick up"):
        if " and " in t or " then " in t or ", " in t or ";" in t:
            return None   # compound: the adjudicator sequences it
        if t.startswith("pick up"):
            parg = text.strip()[len("pick up"):].strip()
        else:
            parg = arg
        if not parg:
            return _meta_command(keeper, char, "take")
        if parg.isdigit():
            pick = _menu_pick(keeper, char, "take", int(parg))
            inst = keeper.item_instances.get(pick) if pick else None
            char.extra.pop("_last_menu", None)   # answered: consumed
            if inst is None:
                print(f"  [No selection {parg} — list them again with 'take'.]")
                return True
            return _meta_command(keeper, char, f"take {inst.name}")
        return _meta_command(keeper, char, f"take {parg}")

    # v2.8.1.1 P1: 'unlock <thing> [with <item>]' and 'use <item> on <thing>'
    if cmd == "unlock":
        m = re.match(r"(.+?)\s+with\s+.+$", arg, re.I)
        target_arg = (m.group(1) if m else arg).strip()
        if not target_arg:
            print("  [Unlock what?]")
            return True
        return _meta_command(keeper, char, f"open {target_arg}")
    if cmd == "use" and re.search(r"\s+on\s+", arg, re.I):
        parts = re.split(r"\s+on\s+", arg, maxsplit=1, flags=re.I)
        if len(parts) == 2 and parts[1].strip():
            return _meta_command(keeper, char, f"open {parts[1].strip()}")

    # unequip with optional target, plus natural aliases
    if cmd == "unequip" or t.startswith("put away") or cmd == "lower":
        if t.startswith("put away"):
            return _do_unequip(keeper, char, text.strip()[len("put away"):].strip())
        return _do_unequip(keeper, char, arg)

    # read <document> — readable items, carried or visible in the room
    if cmd == "read":
        docs = _readable_things(keeper, char)
        if arg:
            inst = None
            if arg.isdigit():
                pick = _menu_pick(keeper, char, "read", int(arg))
                inst = keeper.item_instances.get(pick) if pick else None
                char.extra.pop("_last_menu", None)   # answered: consumed
            if inst is None:
                low = _ARTICLE.sub("", arg.lower().strip())
                inst = next((d for d in docs
                             if d.name.lower() == low or low in d.name.lower()),
                            None)
            if inst is None:
                print(f"  [No '{arg}' to read here.]")
                return True
            _print_read(keeper, char, inst)
            return True
        if not docs:
            print("  [Nothing to read here.]")
            return True
        if len(docs) == 1:
            _print_read(keeper, char, docs[0])
            return True
        _store_menu(keeper, char, "read", [d.id for d in docs])
        _print_numbered(keeper, [d.name for d in docs],
                             "Read which? e.g. 'read 1'")
        return True

    # bare 'enter' / 'go' / 'go to' and numbered exit selection
    if (cmd in ("enter", "go")
            and (t in ("enter", "go", "go to") or arg.isdigit())):
        if arg.isdigit():
            # Explicit numbered form ('enter 2'): this MAY answer another
            # player's pending enter — one of the two allowed cross-player
            # routings (v2.8.1.7 P0-3, v2.8.1.x P0-2).
            owner, menu, routed = _pending_menu(keeper, char)
            if routed and (menu or {}).get("kind") == "enter":
                menu["answered_by"] = char.id
                print(f"  [menu: {char.name} answered '{cmd} {arg}' for "
                      f"{owner.name}'s pending enter.]")
            exits = room_view.visible_exits(keeper.locations, owner.location,
                                            keeper.world_objects)
            pick = _menu_pick(keeper, owner, "enter", int(arg))
            owner.extra.pop("_last_menu", None)   # answered: consumed
            if pick not in {e["id"] for e in exits}:
                print(f"  [No exit {arg} — list them again with 'enter'.]")
                return True
            keeper._meta_move(owner, pick)
            return True
        # v2.8.1.x P0-2: a BARE 'enter' is this player's own command. It
        # never answers — and never even lists — another player's pending
        # menu (field: Patrick's fresh 'enter' was eaten by Jack's stale
        # menu and moved Jack back out of the Study).
        exits = room_view.visible_exits(keeper.locations, char.location,
                                        keeper.world_objects)
        if not exits:
            print("  [No visible exits from here.]")
            return True
        if len(exits) == 1:
            keeper._meta_move(char, exits[0]["id"])
            return True
        _store_menu(keeper, char, "enter", [e["id"] for e in exits])
        _print_numbered(keeper, 
            [e["name"] + (f" [{e['state']}]" if e["state"] != "open" else "")
             for e in exits],
            "Enter which? e.g. 'enter 1'")
        return True

    # numbered selection for item commands
    if cmd in ("take", "equip", "drop", "reload", "open", "use") and arg.isdigit():
        owner, menu, routed = _pending_menu(keeper, char)
        if routed and (menu or {}).get("kind"):
            menu["answered_by"] = char.id
            print(f"  [menu: {char.name} answered '{cmd} {arg}' for "
                  f"{owner.name}'s pending {menu.get('kind')}.]")
        pick = _menu_pick(keeper, owner, cmd, int(arg))
        thing = _resolve_menu_thing(keeper, owner, cmd, pick)
        owner.extra.pop("_last_menu", None)   # answered: consumed
        if thing is None:
            print(f"  [No selection {arg} — list them again with '{cmd}'.]")
            return True
        return _meta_command(keeper, owner, f"{cmd} {thing.name}")

    # bare item commands: one target -> use it; many -> list; none -> say so
    if cmd in ("take", "equip", "drop", "reload", "open", "use") and not arg:
        if cmd == "take":
            pool, empty = _visible_room_items(keeper, char), "Nothing here to take."
        elif cmd in ("equip", "drop", "use"):
            pool, empty = _carried_items(keeper, char), \
                f"{char.name} isn't carrying anything."
        elif cmd == "reload":
            pool = [i for i in _carried_items(keeper, char)
                    if getattr(keeper.item_templates.get(i.template_id),
                               "ammo_capacity", None) is not None]
            empty = "No carried weapon takes ammunition."
        else:
            pool, empty = _openable_things(keeper, char), "Nothing here to open."
        if not pool:
            print(f"  [{empty}]")
            return True
        if len(pool) == 1:
            return _meta_command(keeper, char, f"{cmd} {pool[0].name}")
        _store_menu(keeper, char, cmd, [x.id for x in pool])
        _print_numbered(keeper, [_show_item(keeper, x) for x in pool],
                             f"{cmd.capitalize()} which? e.g. '{cmd} 1'")
        return True

    # 'use <room item>' — suggest the right verbs instead of failing blind
    if cmd == "use" and arg:
        if _find_carried_item(keeper, char, arg) is None:
            low = arg.lower()
            room_inst = next((i for i in _visible_room_items(keeper, char)
                              if i.name.lower() == low or low in i.name.lower()),
                             None)
            if room_inst is not None:
                opts = [f"take {room_inst.name}"]
                if room_inst in _readable_things(keeper, char):
                    opts.append(f"read {room_inst.name}")
                opts.append(f"look at {room_inst.name}")
                print(f"  [The {room_inst.name} is right there — try "
                      + ", ".join(f"'{o}'" for o in opts) + ".]")
                return True
        return None   # normal use dispatch

    # bare 'look at' / 'examine', and numbered picks of notable things
    look_bare = t in ("look at", "examine")
    look_pick = (cmd == "examine" and arg.isdigit()) or \
                (cmd == "look" and arg.lower().startswith("at ")
                 and arg[3:].strip().isdigit())
    if look_bare or look_pick:
        pool = _notable_things(keeper, char)
        if look_pick:
            n = int(arg) if cmd == "examine" else int(arg[3:].strip())
            pick = _menu_pick(keeper, char, "look", n)
            char.extra.pop("_last_menu", None)   # answered: consumed
            thing = next((x for x in pool if x.id == pick), None)
            if thing is None:
                print(f"  [No selection {n} — list them again with 'examine'.]")
                return True
            return _meta_command(keeper, char, f"examine {thing.name}")
        if not pool:
            print("  [Nothing particular here — try 'observe'.]")
            return True
        if len(pool) == 1:
            return _meta_command(keeper, char, f"examine {pool[0].name}")
        _store_menu(keeper, char, "look", [x.id for x in pool])
        _print_numbered(keeper, 
            [x.name for x in pool],
            "Examine which? e.g. 'examine 1'")
        return True

    # bare 'give' lists pockets; 'give 1 to <name>' selects
    if cmd == "give":
        if not arg:
            pool = _carried_items(keeper, char)
            if not pool:
                print(f"  [{char.name} isn't carrying anything to give.]")
                return True
            people = [c.name for c in keeper.characters.values()
                      if c.id != char.id and c.location == char.location]
            _store_menu(keeper, char, "give", [x.id for x in pool])
            hint = (f"Give what? e.g. 'give 1 to {people[0]}'"
                    if people else "Give what? e.g. 'give 1 to <name>'")
            _print_numbered(keeper, [x.name for x in pool], hint)
            return True
        mnum = re.match(r"(\d+)\s+(to\s+.+)", arg, re.I)
        if mnum:
            pick = _menu_pick(keeper, char, "give", int(mnum.group(1)))
            inst = keeper.item_instances.get(pick) if pick else None
            char.extra.pop("_last_menu", None)   # answered: consumed
            if inst is None:
                print(f"  [No selection {mnum.group(1)} — list them again with 'give'.]")
                return True
            return _meta_command(keeper, char, f"give {inst.name} {mnum.group(2)}")
        return None

    return None

def _meta_command(keeper, char: Character, text: str) -> bool:
    """System-channel commands typed at the declaration prompt.

    These are handled by the engine and never reach the narrative. Returns
    True when the input was consumed as a command (even a failed one),
    False when it's a plain declaration and should flow to the turn.
    """
    t = text.strip().lower()
    if not t:
        return False

    # v2.8.1.1: natural-argument normalization runs before dispatch.
    norm = _normalize_command(keeper, char, text)
    if norm is not None:
        return norm

    if t in ("inv", "inventory"):
        lines = [f"  [{char.name} — inventory]"]
        if char.equipped_item_id:
            lines.append(f"    (in hand) {_show_item(keeper, keeper.item_instances.get(char.equipped_item_id))}")
        carried = [iid for iid in char.inventory if iid != char.equipped_item_id]
        if carried:
            for iid in carried:
                lines.append(f"    {_show_item(keeper, keeper.item_instances.get(iid))}")
        else:
            lines.append("    (nothing else)")
        print("\n".join(lines))
        return True

    if t.startswith("equip"):
        arg = text.strip()[len("equip"):].strip()
        if not arg:
            print("  [Equip what? Try 'inventory'.]")
            return True
        inst = _find_carried_item(keeper, char, arg)
        if inst is None:
            print(f"  [{char.name} isn't carrying a '{arg}'.]")
            return True
        if char.equipped_item_id and char.equipped_item_id not in char.inventory:
            char.inventory.append(char.equipped_item_id)
        if inst.id not in char.inventory:
            char.inventory.append(inst.id)
        char.equipped_item_id = inst.id
        char.refresh_weapon_view()
        keeper._registry_audit(char, after="equip")
        print(f"  [{char.name} readies the {inst.name}.]")
        return True

    if t.startswith("take "):
        arg = text.strip()[len("take "):].strip()
        inst = _find_room_item(keeper, char, arg)
        if inst is None:
            print(f"  [No '{arg}' here to take.]")
            return True
        inst.owner_id = char.id
        inst.location_id = None
        if inst.id not in char.inventory:
            char.inventory.append(inst.id)
        keeper._registry_audit(char, after="take")
        print(f"  [{char.name} takes the {inst.name}.]")
        return True

    if t.startswith("drop "):
        arg = text.strip()[len("drop "):].strip()
        inst = _find_carried_item(keeper, char, arg)
        if inst is None:
            print(f"  [{char.name} isn't carrying a '{arg}'.]")
            return True
        if char.equipped_item_id == inst.id:
            print(f"  [{char.name} must unequip the {inst.name} before dropping it.]")
            return True
        if inst.id in char.inventory:
            char.inventory.remove(inst.id)
        inst.owner_id = None
        inst.location_id = char.location
        keeper._registry_audit(char, after="drop")
        print(f"  [{char.name} drops the {inst.name}.]")
        return True

    if t.startswith("give "):
        m = re.match(r"give\s+(.+?)\s+to\s+(.+)", text.strip(), re.I)
        if not m:
            print("  [Usage: give <item> to <character>]")
            return True
        item_arg, target_name = m.group(1).strip(), m.group(2).strip()
        inst = _find_carried_item(keeper, char, item_arg)
        if inst is None:
            print(f"  [{char.name} isn't carrying a '{item_arg}'.]")
            return True
        recipient = _find_character_in_room(keeper, char, target_name)
        if recipient is None:
            print(f"  [No one named '{target_name}' here.]")
            return True
        if char.equipped_item_id == inst.id:
            print(f"  [{char.name} must unequip the {inst.name} before giving it.]")
            return True
        if inst.id in char.inventory:
            char.inventory.remove(inst.id)
        inst.owner_id = recipient.id
        inst.location_id = None
        if inst.id not in recipient.inventory:
            recipient.inventory.append(inst.id)
        keeper._registry_audit(char, after="give")
        keeper._registry_audit(recipient, after="give")
        print(f"  [{char.name} gives the {inst.name} to {recipient.name}.]")
        return True

    if t.startswith("reload "):
        arg = text.strip()[len("reload "):].strip()
        inst = _find_carried_item(keeper, char, arg)
        if inst is None:
            print(f"  [{char.name} isn't carrying a '{arg}'.]")
            return True
        tmpl = keeper.item_templates.get(inst.template_id)
        if tmpl is None or tmpl.ammo_capacity is None:
            print(f"  [The {inst.name} doesn't take ammunition.]")
            return True
        ammo = next((iid for iid in char.inventory
                     if getattr(keeper.item_instances.get(iid), "item_type", None) == "ammo"), None)
        if ammo is None:
            print(f"  [{char.name} has no ammunition to reload with.]")
            return True
        ammo_inst = keeper.item_instances[ammo]
        ammo_tmpl = keeper.item_templates.get(ammo_inst.template_id)
        # v2.8.0.1: ammunition must match the weapon (generic ammo fits any firearm).
        weapon_ammo_type = getattr(tmpl, "ammo_type", None)
        ammo_ammo_type = getattr(ammo_tmpl, "ammo_type", "generic") if ammo_tmpl else "generic"
        if weapon_ammo_type and ammo_ammo_type != "generic" and ammo_ammo_type != weapon_ammo_type:
            print(f"  [The {ammo_inst.name} does not fit the {inst.name}.]")
            return True
        needed = tmpl.ammo_capacity - (inst.ammo or 0)
        if needed <= 0:
            print(f"  [The {inst.name} is already full.]")
            return True
        available = ammo_inst.quantity if (ammo_tmpl and ammo_tmpl.stackable) else 1
        load = min(needed, available)
        inst.ammo = (inst.ammo or 0) + load
        if ammo_tmpl is not None and ammo_tmpl.stackable:
            ammo_inst.quantity -= load
            if ammo_inst.quantity <= 0:
                char.inventory.remove(ammo)
                del keeper.item_instances[ammo]
        else:
            char.inventory.remove(ammo)
            del keeper.item_instances[ammo]
        if char.equipped_item_id == inst.id:
            char.refresh_weapon_view()
        print(f"  [{char.name} reloads the {inst.name}.]")
        return True

    if t.startswith("open "):
        arg = text.strip()[len("open "):].strip()
        target = _find_room_object(keeper, char, arg)
        if target is None:
            # Also allow opening container items in the room.
            target = _find_room_item(keeper, char, arg)
        if target is None:
            # v2.8.1.1: the door you just walked through is not "not
            # here" — it lives on the other side of the exit you used.
            target = keeper._find_linked_door_across_exits(char, arg)
            if target is not None and target.state == "open":
                print(f"  [The {target.name} is already open, behind you.]")
                return True
        if target is None:
            print(f"  [No '{arg}' here to open.]")
            return True
        if target.state == "open":
            print(f"  [The {target.name} is already open.]")
            return True
        if getattr(target, "properties", {}).get("locked"):
            key_id = target.properties.get("key_id")
            # v2.8.1.1 P0: never dereference a {} fallback — an unresolved
            # inventory id must skip, not crash (field: roster legacy
            # string entries made 'open door' raise AttributeError).
            has_key = any(
                getattr(keeper.item_instances.get(iid), "template_id", None) == key_id
                for iid in char.inventory)
            if not has_key:
                print(f"  [The {target.name} is locked.]")
                return True
            # v2.8.1.x: the key did its work — the object's truth must
            # not keep saying locked=True after it opens.
            target.properties["locked"] = False
        target.state = "open"
        keeper._sync_exits_for_object(target)
        keeper._registry_audit(char, after=f"open {target.name}")
        print(f"  [{char.name} opens the {target.name}.]")
        return True

    # v2.8.1: local observation — deterministic room view, never the LLM.
    if t in ("observe", "look", "look around", "examine room",
             "examine the room", "look at the room", "l"):
        _cmd_observe(keeper, char)
        return True

    # v2.8.1.x: local range readout — same free-command terms as look.
    if t in ("distance", "distances", "range"):
        _cmd_distance(keeper, char)
        return True

    # v2.8.1.x Phase 0: the status sheet — read-only engine projection,
    # no LLM, no turn (see src/status_view.py).
    if t in ("status", "st"):
        print(status_view.render_status(
            status_view.build_status(keeper, char)))
        return True

    # v2.8.1.x Phase 2: melee-defense stance — engine-owned, free command.
    if t == "stance" or t.startswith("stance "):
        _cmd_stance(keeper, char, text)
        return True

    if t.startswith("look at ") or t.startswith("examine "):
        if t.startswith("look at "):
            arg = text.strip()[len("look at "):].strip()
        else:
            arg = text.strip()[len("examine "):].strip()
        # search inventory, room items, world objects, then people
        target = _find_carried_item(keeper, char, arg)
        if target is None:
            target = _find_room_item(keeper, char, arg)
        if target is None:
            target = _find_room_object(keeper, char, arg)
        if target is None:
            target = _find_character_in_room(keeper, char, arg)
        if target is None:
            print(f"  [No '{arg}' here to examine.]")
            return True
        desc = getattr(target, "description", "") or ""
        print(f"  [{_show_item(keeper, target)}]")
        if desc:
            print(f"    {desc}")
        return True

    if t.startswith("use "):
        arg = text.strip()[len("use "):].strip()
        inst = _find_carried_item(keeper, char, arg)
        if inst is None:
            print(f"  [{char.name} isn't carrying a '{arg}'.]")
            return True
        if inst.item_type == "light_source":
            on = not inst.state.get("on", False)
            inst.state["on"] = on
            print(f"  [{char.name} turns the {inst.name} {'on' if on else 'off'}.]")
            return True
        if inst.item_type == "consumable":
            inst.quantity -= 1
            if inst.quantity <= 0:
                char.inventory.remove(inst.id)
                del keeper.item_instances[inst.id]
                print(f"  [{char.name} uses the last of the {inst.name}.]")
            else:
                print(f"  [{char.name} uses the {inst.name}. The Keeper will narrate the effect.]")
            return True
        if inst.item_type == "tool" and "lockpicking" in inst.tags:
            print(f"  [Use the {inst.name} by declaring what lock you are working on.]")
            return True
        if inst.item_type == "ammo":
            print(f"  [Use 'reload <weapon>' to load ammunition.]")
            return True
        print(f"  [{char.name} uses the {inst.name}. The Keeper will resolve the effect.]")
        return True

    if t in ("help", "list", "?"):
        print("""Available commands:
  inventory / inv            what you are carrying
  equip <item>               ready a carried weapon or tool
  unequip                    put away whatever is in your hand
  take <item>                pick up an item in the room
  drop <item>                place an item on the ground
  give <item> to <name>      hand an item to another investigator
  reload <weapon>            reload a firearm from carried ammo
  observe / look / look around   see the room again (no LLM, no turn used)
  distance / range               how far everyone is, and what that means
                             for your readied weapon (no turn used)
  status / st                    your sheet: health, gear, position (no turn used)
  stance <dodge|fight back|none|auto>   your melee defense when attacked
                             (engine-owned; no turn used)
  go to / enter <room>           move through a visible exit (no LLM when ordinary)
  leave / back / go back / return   retrace your last step ('exit' quits the game)
  enter / take / equip / open    bare forms list what you can pick; 'take 1' selects
  read <document>                read a letter, ledger, or notebook
  open <container>               open a container or door
  look at / examine <thing>      inspect an item, object, or detail
  use <item>                     use an item in a generic way
  close distance                 move within striking reach of someone here
  --- the turn contract: one declaration per investigator per turn ---
  <anything else>              your action for the turn (resolves as one party turn)
  pass / wait                    take no action this turn (blank Enter works too)
  done / resolve                 resolve the declared batch now; anyone who has
                             not declared yet is treated as passing
  end / end turn                 end the party turn early; with no declarations
                             this lets time pass locally (no LLM, no cost)
  help / list                    show this command list
  quit / exit / save             save and leave the game""")
        return True

    return False
