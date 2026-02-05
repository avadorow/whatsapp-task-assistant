import json
from pathlib import Path
from typing import List, Dict, Any

#Google calendar imports
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request as GoogleRequest
from googleapiclient.discovery import build

#Going to start with read only access. Will expand later.
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly']

CLIENT_SECRETS_PATH = Path(__file__).parent / "secrets" / "google_client.json"

def build_flow(redirect_uri: str) -> Flow:
    # Create the OAuth2 flow using client secrets and desired scopes
    return Flow.from_client_secrets_file(
        str(CLIENT_SECRETS_PATH),
        scopes=SCOPES,
        redirect_uri=redirect_uri
    )
def creds_from_row(row) -> Credentials:
    # Build Credentials object from database row so i don't have to reuse credentials over and over
    return Credentials(
        token=row["access_token"],
        refresh_token=row.get("refresh_token"),
        token_uri=row.get("token_uri"),
        client_id=row.get("client_id"),
        client_secret=row.get("client_secret"),
        scopes=json.loads(row.get("scopes")),
    )
def ensure_fresh_creds(creds: Credentials) -> Credentials:
    # Refresh credentials if expired and gets a new one if so 
    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleRequest())
    return creds

def calendar_service(creds: Credentials):
    # Build the Google Calendar service
    return build('calendar', 'v3', credentials=creds, cache_discovery=False)

def list_next_events(service,time_min_iso: str, max_results: int = 10) -> List[Dict[str, Any]]:
    # List the next events from the user's primary calendar
    resp = (
        service.events()
        .list(
            calendarId = "primary",
            timeMin = time_min_iso,
            maxResults = max_results,
            singleEvents = True,
            orderBy = "startTime",
        )
        .execute()
    )
    return resp.get("items", [])

