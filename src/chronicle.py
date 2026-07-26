"""Chronicle persistence — Google Docs or a local folder (v2.7.0).

v2.2: keeps the review draft's corrected insert logic, restores
get_last_paragraphs() from the setup guide (needed by the resume protocol),
and degrades gracefully when the Docs stack isn't configured — a missing
service-account file now prints a warning and disables the chronicle instead
of crashing the whole session at startup.

v2.7.0: LocalChronicle — the offline equivalent of the Google Docs save
system. Same interface (append / flush / get_last_paragraphs), zero network
calls, no google dependencies: one markdown file per scenario under a
chronicle folder. Select with config/settings.json:

    "chronicle": {"backend": "local" | "google" | "off",
                  "folder": "chronicle", "batch_size": 5}

When the "chronicle" section is absent the legacy google_docs behavior is
preserved untouched (old configs upgrade cleanly).
"""
import json
import os
from datetime import datetime, timezone


class Chronicle:
    def __init__(self, doc_id: str, service_account_file: str, batch_size: int = 5):
        from googleapiclient.discovery import build
        from google.oauth2 import service_account

        self.doc_id = doc_id
        creds = service_account.Credentials.from_service_account_file(
            service_account_file,
            scopes=["https://www.googleapis.com/auth/documents"],
        )
        self.service = build("docs", "v1", credentials=creds)
        self.buffer = []
        self.batch_size = batch_size

    def append(self, turn: int, narration: str, state_delta: dict):
        entry = f"\n\n[Turn {turn}]\n{narration}\n"
        if state_delta:
            entry += f"[State: {json.dumps(state_delta)}]\n"
        self.buffer.append(entry)
        if len(self.buffer) >= self.batch_size:
            self.flush()

    def flush(self):
        if not self.buffer:
            return
        doc = self.service.documents().get(documentId=self.doc_id).execute()
        end = doc["body"]["content"][-1]["endIndex"]
        text = "".join(self.buffer)
        self.service.documents().batchUpdate(
            documentId=self.doc_id,
            body={"requests": [{"insertText": {"location": {"index": end - 1}, "text": text}}]},
        ).execute()
        self.buffer = []

    def get_last_paragraphs(self, n: int = 3) -> str:
        """Used by the resume protocol: seed the new session's opening prompt."""
        doc = self.service.documents().get(documentId=self.doc_id).execute()
        texts = []
        for el in doc["body"]["content"]:
            if "paragraph" in el:
                t = "".join(r.get("text", "") for r in el["paragraph"].get("elements", []))
                if t.strip():
                    texts.append(t.strip())
        return "\n".join(texts[-n:])


class LocalChronicle:
    """Offline chronicle: one markdown file per scenario, same interface as
    the Google Docs Chronicle. No network, no google imports, nothing to
    authenticate — this is the 'folder as an offline Google Docs' backend.

    Writes are buffered and flushed every `batch_size` appends (mirroring
    the Docs batching behavior), plus once on session shutdown. The folder
    is only created when the first flush actually lands, so constructing a
    keeper never touches the disk.
    """

    def __init__(self, folder: str = "chronicle", batch_size: int = 5):
        self.folder = folder
        self.batch_size = batch_size
        self.scenario_id = "misc"     # keeper re-points this in load_scenario
        self.buffer = []

    def set_scenario(self, scenario_id: str):
        self.scenario_id = scenario_id or "misc"

    @property
    def path(self) -> str:
        return os.path.join(self.folder, f"{self.scenario_id}-chronicle.md")

    def append(self, turn: int, narration: str, state_delta: dict):
        # Entry format matches the Google Docs Chronicle exactly, so a
        # campaign can move between backends mid-run without the record
        # changing shape (and get_last_paragraphs sees the same paragraphs).
        entry = f"\n\n[Turn {turn}]\n{narration}\n"
        if state_delta:
            entry += f"[State: {json.dumps(state_delta)}]\n"
        self.buffer.append(entry)
        if len(self.buffer) >= self.batch_size:
            self.flush()

    def flush(self):
        if not self.buffer:
            return
        try:
            os.makedirs(self.folder, exist_ok=True)
            is_new = not os.path.exists(self.path)
            with open(self.path, "a", encoding="utf-8") as f:
                if is_new:
                    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                    f.write(f"# Chronicle — {self.scenario_id}\n\n"
                            f"_Began {stamp}. Recorded locally by the LLM Keeper._\n")
                f.write("".join(self.buffer))
            self.buffer = []
        except OSError as e:   # a full/read-only disk must not kill game night
            print(f"[Local chronicle write failed: {e}]")

    def get_last_paragraphs(self, n: int = 3) -> str:
        """Resume-protocol parity with the Docs backend: tail of the record."""
        if not os.path.exists(self.path):
            return ""
        with open(self.path, encoding="utf-8") as f:
            text = f.read()
        paras = [p.strip() for p in text.split("\n\n") if p.strip()]
        return "\n\n".join(paras[-n:])


def _build_google_chronicle(gdocs: dict):
    """The legacy Google Docs path, unchanged since v2.2."""
    if not gdocs.get("enabled", False):
        return None
    sa_file = gdocs.get("service_account_file", "")
    doc_id = gdocs.get("document_id", "")
    if not os.path.exists(sa_file):
        print(f"[Chronicle disabled: service account file not found: {sa_file}]")
        return None
    if not doc_id or doc_id == "YOUR_DOC_ID_HERE":
        print("[Chronicle disabled: set google_docs.document_id in config/settings.json]")
        return None
    try:
        return Chronicle(doc_id, sa_file, batch_size=int(gdocs.get("batch_size", 5)))
    except Exception as e:  # auth/network problems shouldn't kill a game night
        print(f"[Chronicle disabled: {e}]")
        return None


def build_chronicle(config: dict):
    """Factory: returns a chronicle backend, or None (with a printed reason).

    Precedence: an explicit "chronicle" section wins; without it, the legacy
    google_docs section drives (backwards compatible with pre-v2.7 configs).
    """
    chron = config.get("chronicle")
    if chron is not None:
        backend = str(chron.get("backend", "off")).lower()
        if backend == "local":
            return LocalChronicle(
                folder=chron.get("folder", "chronicle"),
                batch_size=int(chron.get("batch_size", 5)),
            )
        if backend == "google":
            return _build_google_chronicle(config.get("google_docs", {}))
        return None
    return _build_google_chronicle(config.get("google_docs", {}))
