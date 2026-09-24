"""Strava API v3 klient: OAuth (refresh token) + tegevuste lugemine.

Seadistus (üks kord):
  1. strava.com/settings/api -> loo rakendus, Authorization Callback Domain = localhost
  2. .env: STRAVA_CLIENT_ID=..., STRAVA_CLIENT_SECRET=...
  3. python v2/strava_sync.py auth            -> ava prinditud link, kinnita
  4. python v2/strava_sync.py auth --code XXX -> XXX = code= väärtus aadressiribalt

Token (access + refresh) elab data/strava_token.json-is (gitist väljas, 0600).
Strava vahetab refresh tokeni igal uuendusel — seepärast fail, mitte .env.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

TOKEN_PATH = ROOT / "data" / "strava_token.json"
API = "https://www.strava.com/api/v3"
AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
REDIRECT_URI = "http://localhost/exchange_token"
# read_all: ka privaatsed tegevused
SCOPE = "activity:read_all"


class StravaError(Exception):
    """API või autentimise viga (sünk katkeb, tegevust ei märgita töödelduks)."""


def _credentials() -> tuple[str, str]:
    cid, secret = os.getenv("STRAVA_CLIENT_ID"), os.getenv("STRAVA_CLIENT_SECRET")
    if not cid or not secret:
        raise StravaError("STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET puuduvad .env-ist")
    return cid, secret


def authorize_url() -> str:
    cid, _ = _credentials()
    q = urllib.parse.urlencode({
        "client_id": cid, "response_type": "code", "redirect_uri": REDIRECT_URI,
        "approval_prompt": "force", "scope": SCOPE,
    })
    return f"{AUTHORIZE_URL}?{q}"


def _post_token(params: dict) -> dict:
    cid, secret = _credentials()
    body = urllib.parse.urlencode({"client_id": cid, "client_secret": secret, **params}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=body),
                                    timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise StravaError(f"token-päring ebaõnnestus: HTTP {e.code} {e.read()[:200]!r}") from e


def _save_token(tok: dict, path: Path) -> dict:
    # athlete-profiili ei salvesta — vaja on ainult tokeneid
    keep = {k: tok[k] for k in ("access_token", "refresh_token", "expires_at") if k in tok}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(keep, f)
    return keep


def exchange_code(code: str, path: Path = TOKEN_PATH) -> dict:
    """Ühekordne: autoriseerimiskood -> access + refresh token faili."""
    tok = _post_token({"code": code, "grant_type": "authorization_code"})
    scope = tok.get("scope") or ""
    saved = _save_token(tok, path)
    if scope and "activity:read" not in scope:
        print(f"HOIATUS: antud õigused '{scope}' — tegevusi ei saa lugeda. "
              "Kinnita uuesti ja jäta 'View data about your activities' linnukesega.",
              file=sys.stderr)
    return saved


class StravaClient:
    def __init__(self, token_path: Path = TOKEN_PATH):
        self.token_path = token_path
        if not token_path.exists():
            raise StravaError(f"{token_path} puudub — käivita: python v2/strava_sync.py auth")
        self.token = json.loads(token_path.read_text())
        self.calls = 0

    def _access_token(self) -> str:
        if self.token.get("expires_at", 0) - 300 < time.time():
            tok = _post_token({"grant_type": "refresh_token",
                               "refresh_token": self.token["refresh_token"]})
            self.token = _save_token(tok, self.token_path)
        return self.token["access_token"]

    def get(self, path: str, **params):
        url = f"{API}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {self._access_token()}"})
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise StravaError("Strava päringulimiit täis (429) — proovi 15 min pärast") from e
            raise StravaError(f"GET {path}: HTTP {e.code}") from e
        except urllib.error.URLError as e:
            raise StravaError(f"GET {path}: võrguviga {e.reason}") from e

    def activities(self, after: int) -> list[dict]:
        """Kõik tegevused alates epoch-sekundist `after`, vanimast uueni."""
        out, page = [], 1
        while True:
            batch = self.get("/athlete/activities", after=after, per_page=100, page=page)
            out += batch
            if len(batch) < 100:
                return out
            page += 1

    def activity(self, activity_id: int) -> dict:
        """Detailvaade: sisaldab description ja calories (nimekirjas neid pole)."""
        return self.get(f"/activities/{activity_id}")

    def streams(self, activity_id: int, keys=("time", "heartrate")) -> dict[str, list]:
        """{'time': [...], 'heartrate': [...]}; pulsita tegevusel puuduv võti."""
        try:
            data = self.get(f"/activities/{activity_id}/streams",
                            keys=",".join(keys), key_by_type="true")
        except StravaError as e:
            if "HTTP 404" in str(e):
                return {}
            raise
        return {k: v.get("data") or [] for k, v in data.items()}
