"""Live Google Docs smoke test — appends one line to your chronicle doc.

Prereqs: service-account.json in config/, Docs API enabled, doc shared with
the service account email (Editor), document_id set in settings.json.
Run:  python test_docs.py
"""
import json

from googleapiclient.discovery import build
from google.oauth2 import service_account

with open("config/settings.json", encoding="utf-8") as f:
    settings = json.load(f)

SCOPES = ["https://www.googleapis.com/auth/documents"]
creds = service_account.Credentials.from_service_account_file(
    settings["google_docs"]["service_account_file"], scopes=SCOPES)
service = build("docs", "v1", credentials=creds)
doc_id = settings["google_docs"]["document_id"]

doc = service.documents().get(documentId=doc_id).execute()
end = doc["body"]["content"][-1]["endIndex"]
requests = [{
    "insertText": {
        "location": {"index": end - 1},
        "text": "\n[Test Entry] CoC7 Keeper v2.2 — Docs connection successful.\n",
    }
}]
service.documents().batchUpdate(documentId=doc_id, body={"requests": requests}).execute()
print("Google Docs test successful. Check your document.")
