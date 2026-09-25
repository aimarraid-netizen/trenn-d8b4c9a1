"""Hevy public API v1 klient (ainult Hevy Pro): trennid, muudatuste voog, harjutused.

Seadistus: hevy.com/settings?developer -> Generate API key -> .env: HEVY_API_KEY=...
Autentimine = päis 'api-key' (OAuth-i pole). Dokumentatsioon: api.hevyapp.com/docs

Hevy hoiatab, et API on beetas ja struktuur võib muutuda — seepärast salvestab
hevy_sync iga trenni toor-JSON-i (hevy_workouts.raw_json), et saaks ilma API-ta
uuesti töödelda.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

API = "https://api.hevyapp.com/v1"
PAGE_MAX = 10             # workouts, events, routines
TEMPLATE_PAGE_MAX = 100   # exercise_templates


class HevyError(Exception):
    """API või võtme viga (sünk katkeb, trenni ei märgita töödelduks)."""


class HevyClient:
    def __init__(self, api_key: str | None = None):
        self.key = api_key or os.getenv("HEVY_API_KEY")
        if not self.key:
            raise HevyError("HEVY_API_KEY puudub .env-ist (hevy.com/settings?developer)")
        self.calls = 0

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = f"{API}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return self._send(urllib.request.Request(url), path)

    def _write(self, method: str, path: str, body: dict) -> dict:
        req = urllib.request.Request(f"{API}{path}", method=method,
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        return self._send(req, path, not_found_ok=False)

    def _send(self, req: urllib.request.Request, path: str, not_found_ok: bool = True) -> dict:
        req.add_header("api-key", self.key)
        req.add_header("Accept", "application/json")
        req.add_header("User-Agent", "trenn-hevy-sync")
        for attempt in range(3):
            self.calls += 1
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 2:
                    time.sleep(int(e.headers.get("Retry-After") or 30))
                    continue
                if e.code == 401:
                    raise HevyError("401: API-võti vale või Hevy Pro aegunud") from None
                if e.code == 404 and not_found_ok:
                    # lehekülg üle viimase annab 404 — tühi, mitte viga
                    return {}
                raise HevyError(f"{e.code} {path}: {e.read()[:200]!r}") from None
            except urllib.error.URLError as e:
                raise HevyError(f"võrguviga {path}: {e.reason}") from None
        raise HevyError(f"429 {path}: päringulimiit ei vabanenud")

    def _pages(self, path: str, key: str, page_size: int = PAGE_MAX, **params):
        page = 1
        while True:
            data = self._get(path, {"page": page, "pageSize": page_size, **params})
            yield from data.get(key) or []
            if page >= (data.get("page_count") or 0):
                return
            page += 1

    def user_info(self) -> dict:
        return self._get("/user/info").get("data") or {}

    def workout_count(self) -> int:
        return self._get("/workouts/count").get("workout_count", 0)

    def workouts(self):
        """Kõik trennid, uusimad ees."""
        return self._pages("/workouts", "workouts")

    def workout(self, workout_id: str) -> dict:
        return self._get(f"/workouts/{workout_id}")

    def events(self, since_iso: str):
        """Muudetud ({type: updated, workout}) ja kustutatud ({type: deleted, id}) trennid
        alates since_iso (UTC), uusimad ees."""
        return self._pages("/workouts/events", "events", since=since_iso)

    def exercise_templates(self):
        return self._pages("/exercise_templates", "exercise_templates", TEMPLATE_PAGE_MAX)

    def routines(self):
        return self._pages("/routines", "routines")

    def create_routine(self, routine: dict) -> dict:
        return self._write("POST", "/routines", {"routine": routine})

    def update_routine(self, routine_id: str, routine: dict) -> dict:
        return self._write("PUT", f"/routines/{routine_id}", {"routine": routine})
