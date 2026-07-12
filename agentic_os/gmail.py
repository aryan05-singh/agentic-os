"""Gmail integration — read-only OAuth, no client libraries.

Same philosophy as the rest of the OS: stdlib only. The OAuth dance and the
Gmail API calls are both plain HTTPS requests via urllib. The web server
(agentic_os.web) is the OAuth redirect target — no separate local server.

Flow:
  GET  /api/gmail/connect   -> 302 to Google's consent screen
  GET  /api/gmail/callback  -> exchanges the code, saves the token, 302 to /

The refresh token is requested with access_type=offline + prompt=consent so
re-connecting always yields one, even on a second authorization.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


class GmailClient:
    def __init__(self, config: dict, base_url: str):
        self.client_id = config.get("gmail_client_id")
        self.client_secret = config.get("gmail_client_secret")
        self.token_path: Path = config["workspace"] / "gmail_token.json"
        self.redirect_uri = f"{base_url}/api/gmail/callback"

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def connected(self) -> bool:
        return self.token_path.exists()

    # -- OAuth --

    def auth_url(self) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",
        }
        return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    def handle_callback(self, code: str) -> None:
        token = self._post_token(
            {
                "code": code,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "redirect_uri": self.redirect_uri,
                "grant_type": "authorization_code",
            }
        )
        token["expires_at"] = time.time() + token.get("expires_in", 3600)
        self.token_path.write_text(json.dumps(token))

    def disconnect(self) -> None:
        self.token_path.unlink(missing_ok=True)

    def _post_token(self, data: dict) -> dict:
        body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())

    def _access_token(self) -> str:
        token = json.loads(self.token_path.read_text())
        if token.get("expires_at", 0) - 60 > time.time():
            return token["access_token"]
        refreshed = self._post_token(
            {
                "refresh_token": token["refresh_token"],
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
            }
        )
        token["access_token"] = refreshed["access_token"]
        token["expires_at"] = time.time() + refreshed.get("expires_in", 3600)
        self.token_path.write_text(json.dumps(token))
        return token["access_token"]

    # -- reading mail --

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{API_BASE}{path}"
        if params:
            url += f"?{urllib.parse.urlencode(params, doseq=True)}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {self._access_token()}"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            try:
                detail = json.loads(detail)["error"]["message"]
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
            raise RuntimeError(f"Gmail API {e.code}: {detail}") from e

    def recent(self, limit: int = 8) -> list[dict]:
        listing = self._get(
            "/messages",
            {"maxResults": limit, "labelIds": "INBOX"},
        )
        messages = []
        for ref in listing.get("messages", []):
            detail = self._get(
                f"/messages/{ref['id']}",
                {"format": "metadata", "metadataHeaders": ["From", "Subject"]},
            )
            headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
            messages.append(
                {
                    "from": headers.get("From", "(unknown sender)"),
                    "subject": headers.get("Subject", "(no subject)"),
                    "snippet": detail.get("snippet", ""),
                    "date": int(detail.get("internalDate", "0")) // 1000,
                    "unread": "UNREAD" in detail.get("labelIds", []),
                }
            )
        return messages
