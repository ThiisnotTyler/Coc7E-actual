"""Live Gemini API smoke test — spends a few hundred tokens on the cheapest model.

Only needed if llm.provider = "gemini". Fill gemini_api_key in config/api-key.json.
Run from the project root:  python test_gemini.py
Expected: a JSON object with narration / state_delta / required_actions.
"""
import json
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.gemini_client import GeminiClient

with open("config/api-key.json", encoding="utf-8") as f:
    keys = json.load(f)
api_key = keys.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY")
if not api_key:
    raise SystemExit("Set gemini_api_key in config/api-key.json first.")

with open("config/system-prompt.txt", encoding="utf-8") as f:
    system_prompt = f.read()

TEST_MODEL = "gemini-2.5-flash-lite"
client = GeminiClient(api_key=api_key, default_model=TEST_MODEL,
                      heavy_model="gemini-2.5-pro")

test_prompt = """
TURN 1
MODE: SQUAD
CURRENT SCENE: corbitt_house_exterior (Outside Corbitt House)
EXITS: {"corbitt_house_ground_floor": "Ground Floor"}
ACTIVE CHARACTERS:
- Eleanor Vance (Journalist): HP 11/11, SAN 60, Spot Hidden 45, Library Use 70
- Samuel Carter (Professor): HP 10/10, SAN 55, History 65, Occult 40
- Martha Finn (Nurse): HP 12/12, SAN 65, First Aid 60, Listen 50
PLAYER DECLARATIONS:
- Eleanor: "Approach the front door and knock"
- Samuel: "Stand back and observe the house"
- Martha: "Check if anyone is watching us from the street"
DICE RESULTS:
- Spot Hidden (Eleanor): 34, Regular
- Spot Hidden (Martha): 67, Failure
- Listen (Samuel): 23, Regular
FRONTS: {"ritual": 0}
PLOT POINTS: []
NARRATE THIS TURN.
"""

print(f"Sending to Gemini ({TEST_MODEL})...")
result = client.query(system_prompt, test_prompt, use_heavy=False)
print(json.dumps(result, indent=2))
assert "narration" in result, "Response missing 'narration' key"
print("\nLIVE GEMINI TEST PASSED")
