"""Action resolver (v2.8.1.2) — executes adjudicated intent frames through
the deterministic mechanics and builds the outcome packet for the LLM.

Only frames the adjudicator marked 'roll' touch the dice. Local frames go
through the existing system-channel commands. Clarify/impossible frames are
answered locally. Passthrough frames are left for the Keeper to narrate.

v2.8.1.x P0-5: a KNOWN impossible melee attack (target beyond arm's reach)
resolves locally with zero LLM calls — the engine says 'too far', suggests
'close distance', and consumes no narrative turn.
"""
import re
from typing import List, Optional

from src import room_view
from src import items as items_mod
from src.action_intent import IntentFrame
from src import skill_graph


class ActionResolver:
    def resolve(self, keeper, char, frames: List[IntentFrame]) -> dict:
        outcome = {"components": [], "dice": None, "rolls": [],
                   "consumed": False, "clarified": False, "passthrough": False}

        # v2.8.1.3 Part 4: a clarification anywhere in the declaration pauses
        # the WHOLE thing. No rolls, no locals, no narration — the player
        # answers first. (Transcript: a clarify printed and Intimidate still
        # rolled. Never again.)
        pending = [f for f in frames if f.decision == "clarify"]
        if pending:
            # v2.8.1.x field fix: an unbindable ATTACK target becomes a
            # numbered pending menu (same ownership/routing rules as the
            # movement menu) — 'shoot guman' must never shoot somebody.
            menu_frame = next((f for f in pending if f.clarify_target_ids),
                              None)
            if menu_frame is not None:
                names = []
                for cid in menu_frame.clarify_target_ids:
                    c = keeper.characters.get(cid)
                    if c is not None:
                        names.append(f"{c.name} ({c.position})")
                verb = menu_frame.verb or "shoot"
                keeper._store_menu(char, "attack",
                                   list(menu_frame.clarify_target_ids),
                                   verb=verb,
                                   instrument_id=menu_frame.instrument_id)
                if verb in ("throw", "hurl", "toss"):
                    hint = f"{verb.capitalize()} at which? e.g. '{verb} 1'"
                else:
                    hint = f"{verb.capitalize()} which? e.g. '{verb} 1'"
                keeper._print_numbered(names, hint)
            else:
                for f in pending:
                    options = " or ".join(f.clarify_options) or f.reason
                    print(f"  [Keeper — {options.rstrip('?')}?]"
                          if not options.endswith("?")
                          else f"  [Keeper — {options}]")
            outcome["clarified"] = True
            outcome["consumed"] = True
            outcome["components"] = [{"frame": f, "clarified": True}
                                     for f in frames]
            return outcome

        opened_targets = set()   # objects/exits already broken or opened this declaration
        failed_rolls = set()     # indices of frames whose roll FAILED

        for i, frame in enumerate(frames):
            # v2.8.1.3 Part 6: a frame conditional on a FAILED earlier frame
            # is skipped when it depended on that outcome.
            if frame.conditional_on in failed_rolls and self._depends_on(
                    frame, frames[frame.conditional_on]):
                outcome["components"].append(
                    {"frame": frame, "skipped": True,
                     "note": "not possible — the earlier action failed"})
                continue
            comp = self._resolve_frame(keeper, char, frame, frames, i,
                                       opened_targets, outcome)
            if comp.get("roll") and comp["roll"].get("level") in ("Failure", "Fumble"):
                failed_rolls.add(i)
            outcome["components"].append(comp)
            if comp.get("roll"):
                outcome["rolls"].append(comp["roll"])

        if outcome["rolls"]:
            primary = dict(outcome["rolls"][0])
            if len(outcome["rolls"]) > 1:
                primary["components"] = outcome["rolls"][1:]
            outcome["dice"] = primary
        has_passthrough = any(c.get("passthrough") for c in outcome["components"])
        outcome["passthrough"] = has_passthrough
        outcome["consumed"] = bool(frames) and not outcome["rolls"] and not has_passthrough
        return outcome

    @staticmethod
    def _depends_on(frame: IntentFrame, earlier: IntentFrame) -> bool:
        """Whether a later frame relies on the earlier frame's success."""
        if frame.target_id and frame.target_id == earlier.target_id:
            return True
        dependent_types = {"npc_handling", "force_object", "object_attack",
                           "locksmith", "open_object"}
        return (frame.action_type in dependent_types
                and earlier.action_type in dependent_types
                and frame.target_type == earlier.target_type)

    # ------------------------------------------------------------- frames
    def _resolve_frame(self, keeper, char, frame, frames, index,
                       opened_targets, outcome) -> dict:
        d = frame.decision
        if d == "roll":
            # conditional: 'blast the lock off, then kick it in' — the kick
            # is only needed when the blast did not open the way.
            if frame.conditional_on is not None and self._already_open(
                    frame, frames[frame.conditional_on], opened_targets):
                return {"frame": frame, "note": "not needed — the way is already open",
                        "skipped": True}
            # v2.8.1.x P0-5: a known impossible melee attack never buys an
            # LLM narration (field: a too-far kick cost 391.7s and 3 calls).
            rf = self._range_failure(keeper, char, frame, outcome)
            if rf is not None:
                return rf
            roll = self._roll(keeper, char, frame)
            if roll is not None:
                self._track_openings(frame, roll, opened_targets)
                return {"frame": frame, "roll": roll}
            return {"frame": frame, "note": "could not resolve", "skipped": True}

        if d == "local":
            return self._local(keeper, char, frame, frames, index)

        if d == "clarify":
            options = " or ".join(frame.clarify_options) or frame.reason
            print(f"  [Keeper — {options.rstrip('?')}?]" if not options.endswith("?")
                  else f"  [Keeper — {options}]")
            outcome["clarified"] = True
            return {"frame": frame, "clarified": True}

        if d == "impossible":
            print(f"  [{frame.reason or 'That is not possible here.'}]")
            return {"frame": frame, "impossible": True}

        return {"frame": frame, "passthrough": True}

    # ------------------------------------------------------------- rolls
    def _range_failure(self, keeper, char, frame: IntentFrame,
                       outcome: dict) -> Optional[dict]:
        """Deterministic melee range check (v2.8.1.x P0-5).

        Melee needs 3 yards or less. When the target is known to be beyond
        reach there is no risky outcome to narrate: the engine prints the
        too-far result, suggests 'close distance', and the declaration is
        consumed with no LLM call and no narrative turn. Leaping/charging
        forms ('flying knee', 'leaping strike', 'charge') get a clarification
        instead — the player may mean close-and-strike. The engine never
        silently moves the attacker closer without an action outcome."""
        if frame.action_type not in ("melee_attack", "nonlethal_attack"):
            return None
        # v2.8.1.x: no target binding here — the adjudicator binds melee
        # targets confidently or opens the target menu; this check only
        # measures distance to a target that is already bound.
        if frame.target_type != "npc" or not frame.target_id:
            return None
        target = keeper.characters.get(frame.target_id)
        if target is None:
            return None
        dist = keeper.combat.calc_distance(char, target)
        if dist != float("inf") and dist <= 3:
            return None
        if dist == float("inf"):
            print(f"  [{target.name} is nowhere within reach of a strike.]")
            return {"frame": frame, "range_failure": True, "distance": "inf"}
        if re.search(r"\b(leap|leaping|flying|charge|charging|lunge|lunging|"
                     r"dive\s+at|rush|rushing)\b", frame.raw, re.I):
            print(f"  [Keeper — {target.name} is {dist:.0f}y away. Close the "
                  f"distance first? ('close distance' moves you within "
                  f"striking reach; the strike lands on your next action.)]")
            outcome["clarified"] = True
            return {"frame": frame, "clarified": True, "range_failure": True,
                    "distance": dist}
        print(f"  [Too far ({dist:.0f}y). Must close distance — "
              f"declare 'close distance' first.]")
        return {"frame": frame, "range_failure": True, "distance": dist}

    def _roll(self, keeper, char, frame: IntentFrame) -> Optional[dict]:
        at = frame.action_type
        # safety net: a non-damage person frame without a bound target takes
        # the nearest person in the scene. DAMAGE frames are absent here on
        # purpose (v2.8.1.x): the adjudicator binds them confidently or opens
        # the target menu — the engine never guesses who gets hurt.
        if not frame.target_id and at in (
                "coercion", "persuasion", "deception", "npc_handling"):
            npc = keeper.adjudicator._nearest_npc(keeper, char)
            if npc is not None:
                frame.target_id, frame.target_type = npc.id, "npc"
        if at == "npc_handling":
            # restraint/handling is a maneuver, not a strike: a clean skill
            # roll, then — on success — deterministic forced movement.
            res = self._skill_roll(keeper, char, frame)
            res["target_char"] = frame.target_id
            if frame.dest_id and res.get("level") not in ("Failure", "Fumble"):
                moved = self._apply_forced_move(keeper, char, frame)
                if moved:
                    res["forced_move"] = moved
            elif frame.dest_id:
                res["forced_move_failed"] = True
                res.setdefault("notes", []).append(
                    "the forced move does NOT happen")
            return res
        if at in ("melee_attack", "nonlethal_attack"):
            target = keeper.characters.get(frame.target_id)
            if target is None:
                return None
            res = keeper.combat.resolve_attack(
                char, target, "melee", nonlethal=("nonlethal" in frame.manner))
            res["skill"] = frame.skill or "Fighting_Brawl"
            res["target_char"] = target.id
            return res
        if at in ("ranged_attack", "coercion", "persuasion", "deception"):
            if at == "ranged_attack" and frame.target_type == "npc":
                target = keeper.characters.get(frame.target_id)
                if target is None:
                    return None
                has_gun = bool(char.weapon and char.weapon.base_range > 0)
                res = keeper.combat.resolve_attack(
                    char, target, "firearms" if has_gun else "melee",
                    others=list(keeper.characters.values()))
                if frame.skill:
                    res["skill"] = frame.skill
                elif has_gun:
                    # v2.8.1.x: the weapon in hand decides (template first).
                    _inst = (items_mod.get_instance(char.equipped_item_id)
                             if char.equipped_item_id else None)
                    _tmpl = (items_mod.get_template(_inst.template_id)
                             if _inst else None)
                    res["skill"] = items_mod.firearm_skill_key(
                        char.weapon, _tmpl)
                else:
                    res["skill"] = "Fighting_Brawl"
                res["target_char"] = target.id
                return res
            if at == "ranged_attack":
                return keeper.roll_object_attack(char, frame.target_id)
            return self._skill_roll(keeper, char, frame)
        if at in ("object_attack", "force_object", "locksmith"):
            if at == "locksmith":
                return self._skill_roll(keeper, char, frame)
            if at == "object_attack" and char.weapon and char.weapon.base_range > 0:
                return keeper.roll_object_attack(char, frame.target_id)
            return keeper.roll_object_attack(char, frame.target_id,
                                             force_skill=frame.skill)
        if at == "athletics" and frame.verb in ("throw", "hurl", "toss"):
            res = self._skill_roll(keeper, char, frame)
            self._apply_thrown_damage(keeper, char, frame, res)
            self._land_thrown_item(keeper, char, frame, res)
            return res
        return self._skill_roll(keeper, char, frame)

    def _apply_thrown_damage(self, keeper, char, frame: IntentFrame,
                             res: dict):
        """A targeted throw IS an attack (v2.8.1.x field fix — the console
        rolled 'Throw 75% — Hard' with NO damage, then narration dropped a
        man the engine never touched: Truth Firewall breach).

        On Regular+ the thrown item's template damage lands: Extreme/Crit
        impales like a melee hit (max + one roll) when the template
        impales, otherwise max only. No damage bonus on throws — RAW's
        half-DB is a documented non-goal. Failure/Fumble: no damage.
        Any targeted throw alerts the target, hit or miss (mirrors
        combat.resolve_melee). The item's landing is handled separately
        (_land_thrown_item), and body-weapon 'throws' ('throw a flying
        knee') never reach this path."""
        target = keeper.characters.get(frame.target_id) \
            if frame.target_type == "npc" else None
        if target is None:
            return
        target.alerted = True   # hit or miss
        if res.get("level") in (None, "Failure", "Fumble"):
            return
        inst = keeper.item_instances.get(frame.instrument_id)
        tmpl = keeper.item_templates.get(inst.template_id) if inst else None
        dmg_str = getattr(tmpl, "damage", None) or "1D3"
        if res["level"] in ("Extreme", "Critical"):
            impales = getattr(tmpl, "impales", None)
            if impales is None:
                impales = getattr(tmpl, "base_range", 0) > 0
            damage = keeper.combat.max_damage(dmg_str)
            if impales:
                damage += keeper.combat.roll_damage(dmg_str)
                res.setdefault("notes", []).append("Impale!")
            res.setdefault("notes", []).append(
                "Extreme success — maximum damage!")
        else:
            damage = keeper.combat.roll_damage(dmg_str)
        net = target.take_damage(damage)
        res["damage"] = net
        res.setdefault("notes", []).append(
            keeper.combat._hit_note(target, net, False))

    def _land_thrown_item(self, keeper, char, frame: IntentFrame,
                          res: Optional[dict] = None) -> Optional[str]:
        """A thrown weapon is a physical thing (v2.8.1.x field fix — the
        item registry owns physical things): hit or miss, it leaves the
        thrower's hand and lands in the room, condition and ammo intact.
        Only throwable items move (no containers); body-weapon 'throws'
        ('throw a flying knee') never reach here. Returns the note for the
        packet/console, or None when nothing throwable was in hand."""
        inst = keeper.item_instances.get(frame.instrument_id)
        if inst is None or inst.owner_id != char.id:
            return None
        if inst.item_type == "container":
            return None
        if inst.id in char.inventory:
            char.inventory.remove(inst.id)
        if char.equipped_item_id == inst.id:
            char.equipped_item_id = None
            char.refresh_weapon_view()
        inst.owner_id = None
        inst.location_id = char.location
        keeper._landed_items.append({"item": inst.id, "name": inst.name,
                                     "room": char.location})
        note = f"the {inst.name} lands somewhere in the room"
        if res is not None:
            res.setdefault("notes", []).append(note)
        return note

    def _skill_roll(self, keeper, char, frame: IntentFrame) -> dict:
        skill = frame.skill or frame.explicit_skill or "Luck"
        target = skill_graph.skill_target(char, skill)
        roll, level = keeper.dice.skill_check(target)
        res = {"skill": skill, "roll": roll, "target": target, "level": level}
        if frame.target_id:
            res["target_char"] = frame.target_id if frame.target_type == "npc" else None
        return res

    def _apply_forced_move(self, keeper, char, frame: IntentFrame) -> Optional[dict]:
        """v2.8.1.3 Part 5: deterministic forced NPC movement.

        On a successful handling roll the engine — not the narrator — moves
        the NPC (and the handler with them), updates the scene, applies the
        door clause, and records the movement event."""
        npc = keeper.characters.get(frame.target_id)
        dest = keeper.locations.get(frame.dest_id)
        if npc is None or dest is None:
            return None
        origin = npc.location
        keeper.spatial.move_occupant(npc.id, origin, frame.dest_id)
        npc.location = frame.dest_id
        with_them = char.location == origin
        if with_them:
            keeper.spatial.move_occupant(char.id, char.location, frame.dest_id)
            char.location = frame.dest_id
            keeper.mark_visited(char.id, frame.dest_id)
            keeper._engine_moved[char.id] = frame.dest_id
        keeper._update_scene_after_move()

        door_note = None
        if "shut_door" in frame.manner:
            door = self._find_linking_door(keeper, origin, frame.dest_id)
            if door is not None:
                door.state = "closed"
                keeper._sync_exits_for_object(door)
                door_note = f"the {door.name} is closed"

        event = {
            "character": npc.id,
            "origin_location": origin,
            "destination_location": frame.dest_id,
            "current_location_after_action": frame.dest_id,
            "movement_completed": True,
            "forced_by": char.id,
            "player_moved_with": with_them,
            "door_closed": bool(door_note),
        }
        keeper._movement_events.append(event)
        return {"npc": npc.id, "origin": origin, "dest": frame.dest_id,
                "with_them": with_them, "door_closed": bool(door_note)}

    @staticmethod
    def _find_linking_door(keeper, origin: str, dest: str):
        """The door object that stands between origin and dest, if any."""
        for lid, loc in ((origin, keeper.locations.get(origin)),
                         (dest, keeper.locations.get(dest))):
            if loc is None:
                continue
            for conn_dest, conn in loc.connections.items():
                if not isinstance(conn, dict):
                    continue
                if lid == origin and conn_dest != dest:
                    continue
                if lid == dest and conn_dest != origin:
                    continue
                obj = keeper.world_objects.get(conn.get("object_id"))
                if obj is not None:
                    return obj
        return None

    def _already_open(self, frame: IntentFrame, earlier: IntentFrame,
                      opened_targets: set) -> bool:
        """'then kick it in' is conditional on the lock surviving the blast."""
        if frame.action_type not in ("force_object", "object_attack", "locksmith"):
            return False
        if frame.target_id and frame.target_id in opened_targets:
            return True
        if earlier.target_id and earlier.target_id in opened_targets:
            if frame.target_type in ("object", None):
                return True
        return False

    def _track_openings(self, frame: IntentFrame, roll: dict, opened: set):
        if roll.get("object_result") and frame.target_id:
            opened.add(frame.target_id)

    # ------------------------------------------------------------- locals
    def _local(self, keeper, char, frame: IntentFrame, frames, index) -> dict:
        at = frame.action_type
        if at == "athletics" and frame.verb in ("throw", "hurl", "toss"):
            # v2.8.1.x: a targetless throw resolves locally — the item
            # leaves the hand and lands in the room; no roll, no LLM.
            note = self._land_thrown_item(keeper, char, frame)
            if note:
                print(f"  [{char.name} throws — {note}.]")
            else:
                print(f"  [{char.name} has nothing like that to throw.]")
            return {"frame": frame, "local": "throw", "landed": bool(note)}
        if at == "take_item":
            name = self._target_name(keeper, frame) or frame.raw
            keeper._meta_command(char, f"take {name}")
            return {"frame": frame, "local": "take"}
        if at == "read":
            name = self._target_name(keeper, frame) or ""
            keeper._meta_command(char, f"read {name}".strip())
            return {"frame": frame, "local": "read"}
        if at == "use_item":
            name = self._target_name(keeper, frame) or frame.raw
            keeper._meta_command(char, f"use {name}")
            return {"frame": frame, "local": "use"}
        if at == "open_object":
            name = self._target_name(keeper, frame) or frame.raw
            keeper._meta_command(char, f"open {name}")
            return {"frame": frame, "local": "open"}
        if at == "fire_setting":
            # v2.8.1.3 Part 9: fire needs a canonical ignition source.
            source = self._ignition_source(keeper, char)
            if source is None:
                print("  [You do not have anything that can start a fire.]")
                return {"frame": frame, "impossible": True}
            return {"frame": frame, "passthrough": True,
                    "note": f"ignition source: {source}"}
        if at == "close_distance":            # v2.8.1.x P0-5: deterministic close — an explicit action outcome
            # (never a silent repositioning). The mover steps adjacent to the
            # target: position is engine-owned mechanical state.
            target = keeper.characters.get(frame.target_id) \
                if frame.target_id else None
            if target is None:
                target = keeper.adjudicator._nearest_npc(keeper, char)
            if target is not None and target.location == char.location:
                char.position = target.position
                print(f"  [{char.name} closes the distance to {target.name}.]")
                return {"frame": frame, "local": "close_distance",
                        "closed_on": target.id}
            char.position = "close"
            print(f"  [{char.name} moves in close.]")
            return {"frame": frame, "local": "close_distance"}
        if at == "observation":
            keeper._cmd_observe(char)
            return {"frame": frame, "local": "observe"}
        if at == "movement":
            if frame.target_id == char.location:
                # the declaration path already escalated this very move —
                # do not move (or stage) twice
                return {"frame": frame, "note": "already there"}
            if frame.target_id and frame.target_type == "exit":
                result = room_view.try_local_move(keeper, char, frame.target_id)
                keeper._handle_move_result(char, result, frame.raw)
                return {"frame": frame, "local": "move",
                        "escalated": bool(result.get("triggers"))}
            # 'sprint across the room' — movement flavor inside a compound
            return {"frame": frame, "passthrough": True}
        return {"frame": frame, "passthrough": True}

    @staticmethod
    def _ignition_source(keeper, char) -> Optional[str]:
        """A canonical way to start a fire: carried light source, matches or
        lighter, a visible lantern/oil, or a scenario-defined fire source."""
        for iid in char.inventory:
            inst = keeper.item_instances.get(iid)
            if inst is None:
                continue
            if inst.item_type == "light_source":
                return inst.name
            if any(w in inst.name.lower() for w in ("matches", "lighter", "tinder")):
                return inst.name
        for inst in keeper.item_instances.values():
            if inst.location_id == char.location and inst.owner_id is None \
                    and "hidden" not in inst.tags:
                if inst.item_type == "light_source" or any(
                        w in inst.name.lower()
                        for w in ("lantern", "oil", "matches", "fire")):
                    return inst.name
        loc = keeper.locations.get(char.location)
        if loc is not None and ("fire_source" in loc.tags
                                or loc.lighting and "fire" in loc.lighting):
            return f"the {loc.name} itself"
        return None

    def _target_name(self, keeper, frame: IntentFrame) -> Optional[str]:
        if frame.target_type in ("item", "document") and frame.target_id:
            inst = keeper.item_instances.get(frame.target_id)
            return inst.name if inst else None
        if frame.target_type == "object" and frame.target_id:
            obj = keeper.world_objects.get(frame.target_id)
            return obj.name if obj else str(frame.target_id)
        return None
