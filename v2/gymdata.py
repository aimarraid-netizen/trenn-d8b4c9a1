"""Gymaholicu kavafail (.gymdata) — lugemine ja kirjutamine.

Formaat (pöördprojekteeritud 23.09.2026, round-trip baidi-identne):
    [12 B]  JSON-i pikkus kaheksandsüsteemis ASCII-na, NUL-täidisega
    [101 B] sisemine failinimi "wo.json", NUL-täidisega
    [...]   JSON — json.dumps vaikeseadetega (ensure_ascii, ", " / ": ")

JSON: {"wos": [trenn, ...], "grp": <kava nimi>, "source": "ai"}
    trenn = {"name", "notes", "plans": [plaan, ...]}
    plaan = {"id", "notes": [str], "exercise": <Gymaholicu ID>, "chained": "false",
             "sets": [{"reps", "rest" (s), "type": 0, "qnty", "time" (s)}]}

Märkused:
    - plans[].notes jõuab trenni-CSV-sse N;-ridadena. Pane ESIMESEKS rep-vahemik
      sidekriipsuga ("6-10 reps") — parse_gymaholic_csv loeb selle vahemikuks,
      ülejäänud read lähevad sets.note'i.
    - Kardio: reps=1, time=sekundid.
    - qnty = plaanitud kaal, ühik kinnitamata (history-ekspordis kg×100). Kuni
      pole kinnitatud, jäta 0 ja kirjuta kaal märkusesse.
"""
import json
import uuid
from pathlib import Path

INNER_NAME = b"wo.json"
SIZE_LEN = 12
NAME_LEN = 101

# Gymaholicu sisseehitatud harjutuste ID-d (tuletatud 23.09.2026 kavafailist)
EXERCISE_IDS = {
    "Barbell Bench Press": 1,
    "Barbell Curl": 3,
    "Triceps Pushdown with Rope": 5,
    "Bent Over Barbell Row": 8,
    "Lying Leg Curls": 14,
    "Leg Extensions": 15,
    "Reverse Flyes": 32,
    "Barbell Squat": 37,
    "Incline Dumbbell Press": 43,
    "Wide-Grip Lat Pulldown": 49,
    "Walking On Treadmill": 81,
    "Rowing With Rowing Ergometer": 89,
    "Dumbbell Flyes": 116,
    "Shoulder Press": 124,
    "Romanian Deadlift": 141,
    "Seated Hammer Curls": 382,
}


def encode(obj: dict) -> bytes:
    body = json.dumps(obj).encode("ascii")
    return (oct(len(body))[2:].encode().ljust(SIZE_LEN, b"\0")
            + INNER_NAME.ljust(NAME_LEN, b"\0") + body)


def decode(raw: bytes) -> dict:
    size = int(raw[:SIZE_LEN].rstrip(b"\0"), 8)
    start = SIZE_LEN + NAME_LEN
    return json.loads(raw[start:start + size])


def read(path) -> dict:
    return decode(Path(path).read_bytes())


def write(obj: dict, out_dir) -> Path:
    """Kirjuta uue nimega (wo_<uuid>.gymdata) faili, tagasta tee."""
    path = Path(out_dir) / f"wo_{uuid.uuid4().hex}.gymdata"
    path.write_bytes(encode(obj))
    return path


def plan(pid: int, exercise: str, sets: int = 0, reps: int = 0, rest: int = 90,
         notes=(), seconds: int = 0) -> dict:
    """Üks harjutus kavas. seconds>0 = kardio (üks 'seeria' kestusega)."""
    if seconds:
        set_rows = [{"reps": 1, "rest": 0, "type": 0, "qnty": 0, "time": seconds}]
    else:
        set_rows = [{"reps": reps, "rest": rest, "type": 0, "qnty": 0, "time": 0}
                    for _ in range(sets)]
    return {"id": pid, "notes": list(notes), "exercise": EXERCISE_IDS[exercise],
            "chained": "false", "sets": set_rows}


def workout(name: str, notes: str, plans: list[dict]) -> dict:
    return {"name": name, "notes": notes, "plans": plans}


def bundle(workouts: list[dict], grp: str, source: str = "ai") -> dict:
    return {"wos": workouts, "grp": grp, "source": source}
