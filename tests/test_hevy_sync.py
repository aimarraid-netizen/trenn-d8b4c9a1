import hevy_sync as hs
import pytest
from conftest import FIXTURES

import exercise_config as cfg
import parse_gymaholic_csv as pg
from validation import ValidationError


# Sünteetilised numbrid (repo on avalik) — struktuur nagu api.hevyapp.com/docs Workout
def hevy_workout(wid="w1", start="2026-09-25T12:00:00Z", end="2026-09-25T13:05:00Z",
                 title="Trenn A", updated="2026-09-25T13:06:00Z", exercises=None):
    return {
        "id": wid, "title": title, "description": "", "start_time": start, "end_time": end,
        "updated_at": updated, "created_at": updated,
        "exercises": exercises if exercises is not None else [
            {"index": 0, "title": "Romanian Deadlift", "notes": "", "exercise_template_id": "A",
             "sets": [
                 {"index": 0, "type": "warmup", "weight_kg": 20, "reps": 10},
                 {"index": 1, "type": "normal", "weight_kg": 60, "reps": 8},
                 {"index": 2, "type": "failure", "weight_kg": 60, "reps": 7},
             ]},
            {"index": 1, "title": "Face Pull (Cable)", "notes": "köis", "exercise_template_id": "B",
             "sets": [{"index": 0, "type": "normal", "weight_kg": 0, "reps": 15}]},
            {"index": 2, "title": "Plank", "exercise_template_id": "C",
             "sets": [{"index": 0, "type": "normal", "weight_kg": None, "reps": None,
                       "duration_seconds": 45}]},
            {"index": 3, "title": "Hip Thrust (Barbell)", "exercise_template_id": "D",
             "sets": [{"index": 0, "type": "normal", "weight_kg": 40, "reps": 10}]},
        ],
    }


class FakeClient:
    def __init__(self, events=()):
        self._events = list(events)
        self.calls = 0

    def events(self, since):
        self.since = since
        return iter(self._events)


def upd(w):
    return {"type": "updated", "workout": w}


# ---------- teisendus ----------

def test_from_hevy_suffix_equipment_and_unknown():
    assert cfg.from_hevy("Face Pull (Cable)") == ("Face Pull (Cable)", "cable", False)
    assert cfg.from_hevy("Romanian Deadlift") == ("Romanian Deadlift", "barbell", True)


def test_from_hevy_mapping(monkeypatch):
    monkeypatch.setitem(cfg.HEVY_NAMES, "Face Pull (Cable)", "Face Pull")
    monkeypatch.setitem(cfg.HEVY_NAMES, "Face Pull", ("Face Pull", "trx"))
    assert cfg.from_hevy("Face Pull (Cable)") == ("Face Pull", "cable", True)
    assert cfg.from_hevy("Face Pull") == ("Face Pull", "trx", True)


def test_to_parsed_skips_warmup_zero_kg_null_time(monkeypatch):
    monkeypatch.setitem(cfg.HEVY_NAMES, "Face Pull (Cable)", "Face Pull")
    parsed, unknown, warmups = hs.to_parsed(hevy_workout())
    meta = parsed["meta"]
    assert meta["name"] == "Trenn A"
    assert meta["date"].strftime("%Y-%m-%d %H:%M") == "2026-09-25 15:00"   # UTC+3
    assert meta["duration_min"] == 65
    by = {e["name"]: e for e in parsed["exercises"]}
    assert [s["weight"] for s in by["Romanian Deadlift"]["sets"]] == [60, 60]
    assert by["Face Pull"]["sets"][0] == {"reps": 15, "weight": None, "duration": None}
    assert by["Face Pull"]["equipment"] == "cable"
    assert by["Face Pull"]["notes"] == ["köis"]
    assert by["Plank"]["sets"][0]["duration"] == 45
    assert unknown == ["Hip Thrust (Barbell)"] and warmups == 1


def test_to_parsed_merges_repeated_exercise():
    ex = [{"index": i, "title": "Plank", "sets": [{"index": 0, "type": "normal",
                                                    "duration_seconds": 30 + i}]}
          for i in range(2)]
    parsed, _, _ = hs.to_parsed(hevy_workout(exercises=ex))
    assert len(parsed["exercises"]) == 1 and len(parsed["exercises"][0]["sets"]) == 2


# ---------- sünk ----------

def _sets(conn, wid):
    return conn.execute("SELECT exercise_name, reps, weight_kg, equipment, duration_sec "
                        "FROM sets WHERE workout_id=? ORDER BY id", (wid,)).fetchall()


def test_sync_imports_and_is_idempotent(conn):
    client = FakeClient([upd(hevy_workout())])
    res = hs.sync(conn, client)
    assert [r["status"] for r in res] == ["imported"]
    wid = res[0]["workout_id"]
    w = conn.execute("SELECT * FROM workouts WHERE id=?", (wid,)).fetchone()
    assert w["source"] == "hevy" and w["timestamp"] == "2026-09-25 15:00:00"
    assert w["total_volume"] == 60 * 8 + 60 * 7 + 40 * 10
    rows = _sets(conn, wid)
    assert len(rows) == 5
    plank = [r for r in rows if r["exercise_name"] == "Plank"][0]
    assert plank["reps"] is None and plank["duration_sec"] == 45
    assert "Hip Thrust (Barbell)" in res[0]["msg"]
    # sama sündmus uuesti -> muutust pole
    assert hs.sync(conn, client) == []
    assert conn.execute("SELECT count(*) FROM workouts").fetchone()[0] == 1


def test_sync_update_rewrites_and_delete_removes(conn):
    hs.sync(conn, FakeClient([upd(hevy_workout())]))
    edited = hevy_workout(title="Trenn A (muudetud)", updated="2026-09-26T08:00:00Z")
    res = hs.sync(conn, FakeClient([upd(edited)]))
    assert res[0]["status"] == "imported" and res[0]["msg"].startswith("uuendatud")
    names = [r[0] for r in conn.execute("SELECT workout_name FROM workouts")]
    assert names == ["Trenn A (muudetud)"]

    res = hs.sync(conn, FakeClient([{"type": "deleted", "id": "w1",
                                     "deleted_at": "2026-09-27T08:00:00Z"}]))
    assert res[0]["status"] == "deleted"
    assert conn.execute("SELECT count(*) FROM workouts").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM sets").fetchone()[0] == 0


def test_sync_since_uses_last_seen_with_margin(conn):
    hs.sync(conn, FakeClient([upd(hevy_workout())]))
    client = FakeClient()
    hs.sync(conn, client)
    assert client.since == "2026-09-23T13:06:00Z"


def test_dry_run_writes_nothing(conn):
    res = hs.sync(conn, FakeClient([upd(hevy_workout())]), dry_run=True)
    assert res[0]["status"] == "imported"
    assert conn.execute("SELECT count(*) FROM workouts").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM hevy_workouts").fetchone()[0] == 0


def test_duplicate_of_csv_workout(conn):
    parsed = pg.parse_csv(FIXTURES / "sample_single_workout.csv")
    csv_wid, _, _ = pg.save_to_db(parsed, conn)
    ts = conn.execute("SELECT timestamp FROM workouts WHERE id=?", (csv_wid,)).fetchone()[0]
    # sama trenn Hevys 5 min hiljem alustatud (UTC)
    from datetime import datetime, timedelta

    from db import TZ
    start = (datetime.fromisoformat(ts).replace(tzinfo=TZ) + timedelta(minutes=5))
    w = hevy_workout(start=start.astimezone().isoformat(), end=None)
    res = hs.sync(conn, FakeClient([upd(w)]))
    assert res[0]["status"] == "duplicate" and res[0]["workout_id"] == csv_wid
    assert conn.execute("SELECT count(*) FROM workouts").fetchone()[0] == 1


def test_csv_refused_when_hevy_has_it(conn):
    parsed = pg.parse_csv(FIXTURES / "sample_single_workout.csv")
    ts = parsed["meta"]["date"].replace(tzinfo=None)
    from db import TZ
    start = ts.replace(tzinfo=TZ).astimezone().isoformat()
    hs.sync(conn, FakeClient([upd(hevy_workout(start=start, end=None))]))
    with pytest.raises(ValidationError, match="Hevyst"):
        pg.save_to_db(parsed, conn)


def test_failed_on_bad_format_and_rebuild_after_mapping(conn, monkeypatch):
    bad = hevy_workout(wid="w2")
    del bad["start_time"]
    res = hs.sync(conn, FakeClient([upd(bad), upd(hevy_workout())]))
    assert {r["id"]: r["status"] for r in res} == {"w1": "imported", "w2": "failed"}

    monkeypatch.setitem(cfg.HEVY_NAMES, "Hip Thrust (Barbell)", "Romanian Deadlift")
    res = hs.rebuild(conn)
    ok = [r for r in res if r["status"] == "imported"]
    assert len(ok) == 1 and "Hip Thrust" not in ok[0]["msg"]
    names = {r[0] for r in conn.execute("SELECT exercise_name FROM sets")}
    assert "Hip Thrust (Barbell)" not in names
    assert conn.execute("SELECT count(*) FROM workouts").fetchone()[0] == 1


def test_suggest_normalizes_equipment_words():
    assert hs.suggest("Romanian Deadlift (Barbell)") == ["Romanian Deadlift"]
    assert hs.suggest("Lying Leg Curl (Machine)") == ["Lying Leg Curls"]
