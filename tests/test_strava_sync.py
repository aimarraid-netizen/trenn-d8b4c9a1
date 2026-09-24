import pytest
from conftest import FIXTURES

import parse_gymaholic_csv as pg
import strava_sync as ss
from cardio_common import find_existing_cardio

# Sünteetilised numbrid (repo on avalik) — formaat nagu Gymaholicu Strava-kirjeldus
DESC = """Traditional Strength Training

Total weight: 1935 kg
Total cardio: 0h:05m

Rowing With Rowing Ergometer
• 5:00

Romanian Deadlift
• 1x12 @ 50kg
• 2x8 @ 60kg

Single-Leg Leg Extension
• 2x10

Shoulder Press
• 3x10 @ 12.5kg

Plank
• 3x0:45
"""
DESC_TOTAL = 12 * 50 + 2 * 8 * 60 + 3 * 10 * 12.5   # 1935; kehakaal ja aeg ei loe


def strength_act(desc=DESC, start="2026-09-23T12:50:52Z", aid=111, name="Trenn A"):
    return {"id": aid, "name": name, "sport_type": "WeightTraining", "start_date": start,
            "elapsed_time": 4044, "average_heartrate": 119.4, "calories": 610,
            "description": desc}


class FakeClient:
    def __init__(self, acts=(), streams=None):
        self.acts = {a["id"]: a for a in acts}
        self._streams = streams or {}
        self.calls = 0

    def activities(self, after):
        return [{k: v for k, v in a.items() if k != "description"} for a in self.acts.values()]

    def activity(self, aid):
        return dict(self.acts[aid])

    def streams(self, aid, keys=("time", "heartrate")):
        return self._streams.get(aid, {})


# ---------- parser ----------

def test_parse_description_sets_weights_time():
    ex, total = ss.parse_description(DESC)
    by = {e["name"]: e["sets"] for e in ex}
    assert [e["name"] for e in ex] == ["Rowing With Rowing Ergometer", "Romanian Deadlift",
                                       "Single-Leg Leg Extension", "Shoulder Press", "Plank"]
    assert total == DESC_TOTAL
    assert by["Rowing With Rowing Ergometer"] == [{"reps": None, "weight": None, "duration": 300}]
    assert [(s["reps"], s["weight"]) for s in by["Romanian Deadlift"]] == \
        [(12, 50.0), (8, 60.0), (8, 60.0)]
    # kehakaal / kaal logimata -> NULL, kordused alles
    assert [(s["reps"], s["weight"]) for s in by["Single-Leg Leg Extension"]] == [(10, None)] * 2
    assert by["Shoulder Press"][0]["weight"] == 12.5
    assert [s["duration"] for s in by["Plank"]] == [45, 45, 45]


def test_parse_description_sets_are_independent_dicts():
    ex, _ = ss.parse_description(DESC)
    rdl = next(e for e in ex if e["name"] == "Romanian Deadlift")["sets"]
    rdl[1]["reps"] = 99
    assert rdl[2]["reps"] == 8


def test_checksum_mismatch_fails():
    bad = DESC.replace("2x8 @ 60kg", "2x8 @ 65kg")
    with pytest.raises(ss.DescriptionError, match="Total weight"):
        ss.parse_description(bad)


@pytest.mark.parametrize("desc, match", [
    ("Morning workout", "Total weight"),
    (None, "kirjeldus puudub"),
    ("Total weight: 100 lbs\n\nSquat\n• 1x10 @ 10lbs", "kilod"),
    ("Total weight: 0 kg\n\nSquat\n• 10 reps easy", "tundmatu"),
    ("Total weight: 0 kg\n\n• 3x10", "ilma harjutuseta"),
    ("", "kirjeldus puudub"),
    # Gymaholicu vana (enne 05.2026) kokkuvõtte-formaat
    ("Total weight: 7020 kg\nTotal cardio: 0h:05m\nExercises: 7\nAvg. repeats: 12\n"
     "Rowing With Rowing Ergometer, Face Pull", "pole seeriaid"),
])
def test_bad_descriptions_fail_loudly(desc, match):
    with pytest.raises(ss.DescriptionError, match=match):
        ss.parse_description(desc)


def test_comma_decimal_weight():
    ex, _ = ss.parse_description("Total weight: 125 kg\n\nCurl\n• 1x10 @ 12,5kg")
    assert ex[0]["sets"][0]["weight"] == 12.5


# ---------- jõutrenni import ----------

def test_import_strength_with_per_exercise_hr(conn):
    act = strength_act()
    client = FakeClient([act], {111: {"heartrate": [100, 100, 110, 120, 125, 115]}})
    status, wid, msg = ss.import_strength(conn, client, act)
    assert status == "imported"
    w = conn.execute("SELECT * FROM workouts WHERE id=?", (wid,)).fetchone()
    assert w["source"] == "strava"
    assert w["timestamp"] == "2026-09-23 15:50:52"      # UTC -> Tallinn
    assert w["workout_type"] == "jõusaal"
    assert w["duration_min"] == 67 and w["kcal"] == 610 and w["avg_hr"] == 119
    assert w["total_volume"] == DESC_TOTAL
    hr = dict(conn.execute("SELECT DISTINCT exercise_name, avg_hr FROM sets WHERE workout_id=?",
                           (wid,)).fetchall())
    assert hr["Rowing With Rowing Ergometer"] == 100 and hr["Plank"] == 115
    plank = conn.execute("SELECT reps, duration_sec FROM sets WHERE exercise_name='Plank'").fetchall()
    assert [tuple(r) for r in plank] == [(None, 45)] * 3


def test_hr_point_count_mismatch_leaves_sets_hr_null(conn):
    act = strength_act()
    client = FakeClient([act], {111: {"heartrate": [100, 110]}})
    status, wid, msg = ss.import_strength(conn, client, act)
    assert status == "imported" and "puudub" in msg
    assert conn.execute("SELECT COUNT(avg_hr) FROM sets WHERE workout_id=?", (wid,)).fetchone()[0] == 0


def test_strava_duplicate_of_csv_reports_differences(loaded_conn):
    # fixture-CSV: 21. mai 15:55; Face Pull CSV-s 3x20, siin 3x18
    desc = """Total weight: 7335 kg

Rowing With Rowing Ergometer
• 5:00

Bent Over Barbell Row
• 3x6 @ 70kg

Wide-Grip Lat Pulldown
• 3x9 @ 65kg

Seated Cable Rows
• 3x11 @ 65kg

Face Pull
• 3x18 @ 15kg

Barbell Curl
• 3x8 @ 35kg

Seated Hammer Curls
• 3x10 @ 17.5kg
"""
    year = loaded_conn.execute("SELECT substr(date,1,4) FROM workouts").fetchone()[0]
    act = strength_act(desc, start=f"{year}-05-21T12:55:20Z")
    status, wid, msg = ss.import_strength(loaded_conn, FakeClient([act]), act)
    assert status == "duplicate"
    assert "Face Pull: baas 3×20@15 | Strava 3×18@15" in msg
    assert "Barbell Curl" not in msg
    assert loaded_conn.execute("SELECT COUNT(*) FROM workouts").fetchone()[0] == 1


def test_old_summary_format_duplicate_is_not_failure(loaded_conn):
    year = loaded_conn.execute("SELECT substr(date,1,4) FROM workouts").fetchone()[0]
    act = strength_act("Total weight: 7425 kg\nExercises: 7\nAvg. repeats: 12\nFace Pull",
                       start=f"{year}-05-21T12:55:20Z")
    status, _, msg = ss.process(loaded_conn, FakeClient([act]), act)
    assert status == "duplicate" and "ei saa võrrelda" in msg


def test_csv_after_strava_replaces_and_keeps_exercise_hr(conn):
    parsed = pg.parse_csv(FIXTURES / "sample_single_workout.csv")
    year = parsed["meta"]["date"].year
    desc = "Total weight: 1260 kg\n\nBent Over Barbell Row\n• 3x6 @ 70kg\n"
    act = strength_act(desc, start=f"{year}-05-21T12:55:20Z")
    ss.import_strength(conn, FakeClient([act], {111: {"heartrate": [100, 131]}}), act)

    wid, _, _ = pg.save_to_db(parsed, conn)
    rows = conn.execute("SELECT id, source FROM workouts").fetchall()
    assert [(r["id"], r["source"]) for r in rows] == [(wid, "gymaholic_csv")]
    hr = conn.execute("SELECT DISTINCT avg_hr FROM sets WHERE exercise_name='Bent Over Barbell Row'"
                      ).fetchall()
    assert [r[0] for r in hr] == [131]
    # teised harjutused Stravas puudusid -> pulss NULL
    assert conn.execute("SELECT avg_hr FROM sets WHERE exercise_name='Face Pull'"
                        ).fetchone()[0] is None


# ---------- kardio ----------

HIKE = {"id": 222, "name": "Sunday Morning Hike", "sport_type": "Hike",
        "start_date": "2026-09-06T07:48:11Z", "elapsed_time": 600, "distance": 2500.0,
        "average_heartrate": 121.6, "max_heartrate": 150, "calories": 300,
        "total_elevation_gain": 12.0, "description": None}


def test_import_cardio_and_zones(conn):
    client = FakeClient([HIKE], {222: {"heartrate": [135] * 300 + [150] * 300}})
    status, wid, msg = ss.import_cardio(conn, client, HIKE)
    assert status == "imported"
    w = conn.execute("SELECT * FROM workouts WHERE id=?", (wid,)).fetchone()
    assert (w["workout_type"], w["source"], w["timestamp"]) == \
        ("matk", "strava", "2026-09-06 10:48:11")
    assert (w["distance_m"], w["avg_hr"], w["max_hr"], w["kcal"], w["ascent_m"]) == \
        (2500.0, 122, 150, 300, 12.0)
    assert round(sum(w[f"z{i}_min"] for i in range(1, 6)), 1) == 10.0


def test_fit_and_strava_same_hike_dedup_both_ways(conn):
    # FIT andis sama matka algushetke 1 s varem
    conn.execute("""INSERT INTO workouts (timestamp, date, workout_name, workout_type, source)
                    VALUES ('2026-09-06 10:48:10', '2026-09-06', 'Sunday_Morning_Hike', 'matk', 'fit')""")
    status, wid, _ = ss.import_cardio(conn, FakeClient([HIKE]), HIKE)
    assert status == "duplicate"
    assert find_existing_cardio(conn, "2026-09-06 10:48:11")["id"] == wid


def test_cardio_dedup_ignores_strength_at_same_time(conn):
    act = strength_act(start="2026-09-06T07:48:00Z")
    ss.import_strength(conn, FakeClient([act]), act)
    assert find_existing_cardio(conn, "2026-09-06 10:48:11") is None


# ---------- sync ----------

def test_sync_statuses_and_idempotent(conn):
    yoga = {"id": 333, "name": "Yoga", "sport_type": "Yoga", "start_date": "2026-09-07T07:00:00Z"}
    bare = strength_act(desc="", aid=444, start="2026-09-08T12:00:00Z")
    client = FakeClient([strength_act(), HIKE, yoga, bare])
    res = {r["id"]: r["status"] for r in ss.sync(conn, client)}
    assert res == {111: "imported", 222: "imported", 333: "skipped", 444: "failed"}
    logged = dict(conn.execute("SELECT activity_id, status FROM strava_activities").fetchall())
    assert logged == res

    assert ss.sync(conn, client) == []                       # kõik juba töödeldud
    retry = ss.sync(conn, client, retry_failed=True)
    assert [(r["id"], r["status"]) for r in retry] == [(444, "failed")]
    assert conn.execute("SELECT COUNT(*) FROM workouts").fetchone()[0] == 2


def test_sync_dry_run_writes_nothing(conn):
    res = ss.sync(conn, FakeClient([strength_act(), HIKE]), dry_run=True)
    assert {r["status"] for r in res} == {"imported"}
    assert conn.execute("SELECT COUNT(*) FROM workouts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM strava_activities").fetchone()[0] == 0


def test_discord_lines_only_new_and_failed():
    res = [{"status": "imported", "sport": "WeightTraining", "name": "Trenn A",
            "date": "2026-09-23", "msg": "9 harjutust"},
           {"status": "duplicate", "sport": "Hike", "name": "x", "date": "2026-09-06", "msg": ""},
           {"status": "failed", "sport": "WeightTraining", "name": "Trenn B",
            "date": "2026-09-25", "msg": "viga"}]
    assert ss._discord_lines(res) == ["🏋️ Strava → Trenn A · 23.09.2026: 9 harjutust",
                                      "⚠️ Strava → Trenn B · 25.09.2026: viga"]
