"""Harjutuste metaandmed: vaikevarustus, lihasgrupp, rep-vahemikud.

Need on alglaadimiseks. CSV-st tulevad rep-vahemikud kirjutavad hiljem üle
(CSV = uusim tõde). default_equipment on Kratti lives-muudatuste lähtepunkt.
"""
import re

# Vaikevarustus harjutuse kohta.
# trx = TRX-rihmad/kehakaal, machine = masin, cable = kaabel,
# barbell = kang, dumbbell = hantlid, bodyweight = puhas kehakaal
DEFAULT_EQUIPMENT = {
    "Barbell Bench Press": "barbell",
    "Barbell Curl": "barbell",
    "Barbell Squat": "barbell",
    "Bent Over Barbell Row": "barbell",
    "Romanian Deadlift": "barbell",
    "Incline Dumbbell Press": "dumbbell",
    "One-Arm Dumbbell Row": "dumbbell",
    "Seated Hammer Curls": "dumbbell",
    "Side Lateral Raise": "dumbbell",
    "Shoulder Press": "dumbbell",
    "Seated Triceps Press": "dumbbell",
    "Lying Triceps Press": "barbell",
    "Wide-Grip Lat Pulldown": "cable",
    "Seated Cable Rows": "cable",
    "Triceps Pushdown with Rope": "cable",
    "Face Pull": "trx",          # kasutaja tegelik vaikevarustus (vahel masin)
    "Triceps Dips": "bodyweight",
    "Lying Leg Raise": "bodyweight",
    "Plank": "bodyweight",
    "Lunge": "bodyweight",
    "Single-Leg Press": "machine",
    "Leg Extensions": "machine",
    "Single-Leg Leg Extension": "machine",
    "Standing Calf Raise": "machine",
    "Lying Leg Curls": "machine",
    "Dumbbell Flyes": "dumbbell",
    "Reverse Flyes": "dumbbell",
    "Rowing With Rowing Ergometer": "cardio",
    "Walking On Treadmill": "cardio",
    "Running": "cardio",
}

# Lihasgrupp harjutuse kohta
MUSCLE_GROUP = {
    "Barbell Bench Press": "rind",
    "Incline Dumbbell Press": "rind",
    "Dumbbell Flyes": "rind",
    "Barbell Curl": "biitseps",
    "Seated Hammer Curls": "biitseps",
    "Bent Over Barbell Row": "selg",
    "One-Arm Dumbbell Row": "selg",
    "Wide-Grip Lat Pulldown": "selg",
    "Seated Cable Rows": "selg",
    "Face Pull": "õlad",
    "Side Lateral Raise": "õlad",
    "Shoulder Press": "õlad",
    "Reverse Flyes": "õlad",
    "Lying Triceps Press": "triitseps",
    "Seated Triceps Press": "triitseps",
    "Triceps Pushdown with Rope": "triitseps",
    "Triceps Dips": "triitseps",
    "Barbell Squat": "jalad",
    "Romanian Deadlift": "jalad",
    "Lunge": "jalad",
    "Single-Leg Press": "jalad",
    "Leg Extensions": "jalad",
    "Single-Leg Leg Extension": "jalad",
    "Lying Leg Curls": "jalad",
    "Standing Calf Raise": "sääred",
    "Lying Leg Raise": "kõht",
    "Plank": "kõht",
    "Rowing With Rowing Ergometer": "kardio",
    "Walking On Treadmill": "kardio",
    "Running": "kardio",
}

# Cardio/kehakaal-harjutused, kus weight_kg=0 EI ole viga (jätta NULL-iks,
# võrdlus käib korduste/aja põhjal).
NO_WEIGHT_EXPECTED = {
    "Rowing With Rowing Ergometer",
    "Walking On Treadmill",
    "Running",
    "Plank",
    "Lying Leg Raise",
    "Triceps Dips",
    "Lunge",
    "Face Pull",          # vahel TRX/kehakaal
}

CARDIO_EXERCISES = {
    "Rowing With Rowing Ergometer",
    "Walking On Treadmill",
    "Running",
}

# Aja-põhised harjutused: mõõdetakse sekundites (hoid), mitte kordustes/kaalus.
# Gymaholic ekspordib need valesti (reps=1, dur=0), seega kestus tuleb logida
# käsitsi Kratti kaudu või CSV TIME-veerust.
TIME_BASED = {
    "Plank",
}


# Meie nimi -> (Hevy nimi, Hevy exercise_template_id). Kontrollitud Hevy
# /exercise_templates vastu 25.09.2026. Sünk loeb nime, kava ülekanne (hevy_routines) ID-d.
# Ilma vasteta tekiks uus harjutus ja progressioon/PR-id katkeksid.
HEVY_TEMPLATES = {
    "Barbell Bench Press": ("Bench Press (Barbell)", "79D0BB3A"),
    "Barbell Curl": ("Bicep Curl (Barbell)", "A5AC6449"),
    "Barbell Squat": ("Squat (Barbell)", "D04AC939"),
    "Bent Over Barbell Row": ("Bent Over Row (Barbell)", "55E6546F"),
    "Face Pull": ("Face Pull", "BE640BA0"),
    "Incline Dumbbell Press": ("Incline Bench Press (Dumbbell)", "07B38369"),
    "Lunge": ("Lunge", "5E1A7777"),
    "Lying Leg Raise": ("Lying Leg Raise", "09C9F635"),
    "Lying Triceps Press": ("Skullcrusher (Barbell)", "875F585F"),
    "One-Arm Dumbbell Row": ("Dumbbell Row", "F1E57334"),
    "Plank": ("Plank", "C6C9B8A0"),
    "Romanian Deadlift": ("Romanian Deadlift (Barbell)", "2B4B7310"),
    "Rowing With Rowing Ergometer": ("Rowing Machine", "0222DB42"),
    "Seated Cable Rows": ("Seated Cable Row - V Grip (Cable)", "0393F233"),
    "Seated Hammer Curls": ("Hammer Curl (Dumbbell)", "7E3BC8B6"),
    "Seated Triceps Press": ("Triceps Extension (Dumbbell)", "3765684D"),
    "Shoulder Press": ("Shoulder Press (Dumbbell)", "878CD1D0"),
    "Side Lateral Raise": ("Lateral Raise (Dumbbell)", "422B08F1"),
    "Single-Leg Press": ("Single Leg Press (Machine)", "3FD83744"),
    "Standing Calf Raise": ("Standing Calf Raise (Machine)", "E05C2C38"),
    "Triceps Dips": ("Triceps Dip", "28BB4A95"),
    "Triceps Pushdown with Rope": ("Triceps Rope Pushdown", "94B7239B"),
    "Walking On Treadmill": ("Treadmill", "243710DE"),
    "Wide-Grip Lat Pulldown": ("Lat Pulldown (Cable)", "6A6C31A5"),
    "Dumbbell Flyes": ("Chest Fly (Dumbbell)", "12017185"),
    "Reverse Flyes": ("Rear Delt Reverse Fly (Dumbbell)", "E5988A0A"),
    "Lying Leg Curls": ("Lying Leg Curl (Machine)", "B8127AD1"),
    "Running": ("Running", "AC1BB830"),
    "Leg Extensions": ("Leg Extension (Machine)", "75A4F6C4"),
    "Single-Leg Leg Extension": ("Single Leg Extensions", "629AE73D"),
}

# Hevy harjutuse nimi -> meie nimi. Käsitsi lisa siia väljaspool HEVY_TEMPLATES
# olevad (nt teine varustus); väärtus võib olla (nimi, varustus).
HEVY_NAMES: dict[str, str | tuple[str, str]] = {
    title: ours for ours, (title, _) in HEVY_TEMPLATES.items()
}

# Hevy nime sulgudes olev varustus -> meie sõnavara (sets.equipment)
HEVY_EQUIPMENT = {
    "barbell": "barbell",
    "dumbbell": "dumbbell",
    "cable": "cable",
    "machine": "machine",
    "smith machine": "machine",
    "bodyweight": "bodyweight",
    "suspension": "trx",
    "trx": "trx",
}


def from_hevy(title: str) -> tuple[str, str | None, bool]:
    """Hevy harjutuse nimi -> (meie nimi, varustus, kas vaste on teada).

    Tundmatu nimi jääb Hevy omaks (andmed ei kao) — `hevy_sync.py rebuild`
    kirjutab need pärast HEVY_NAMES täiendamist ümber.
    """
    m = re.search(r"\(([^)]+)\)\s*$", title)
    suffix_equip = HEVY_EQUIPMENT.get(m.group(1).strip().lower()) if m else None
    mapped = HEVY_NAMES.get(title)
    if isinstance(mapped, tuple):
        return mapped[0], mapped[1], True
    if mapped:
        return mapped, suffix_equip or equipment_for(mapped), True
    if title in MUSCLE_GROUP:
        return title, suffix_equip or equipment_for(title), True
    return title, suffix_equip, False


def equipment_for(name: str) -> str | None:
    return DEFAULT_EQUIPMENT.get(name)


def muscle_for(name: str) -> str:
    return MUSCLE_GROUP.get(name, "muu")


def is_cardio(name: str) -> bool:
    return name in CARDIO_EXERCISES


def is_time_based(name: str) -> bool:
    return name in TIME_BASED


def orphan_exercises(conn) -> list[str]:
    """Harjutused, mis on baasis (sets), aga configis puuduvad.

    Puuduv config = lihasgrupp "muu" + equipment NULL -> bilanss ja
    varustusloogika on nende jaoks vaikselt katki. Kutsu impordil hoiatuseks.
    """
    rows = conn.execute("SELECT DISTINCT exercise_name FROM sets ORDER BY 1")
    return [r[0] for r in rows if r[0] not in MUSCLE_GROUP]


def sync_to_db(conn) -> int:
    """Kirjuta configi lihasgrupp/vaikevarustus exercises tabelisse.

    - muscle_group uuendatakse ainult kui NULL või "muu" (käsitsi määratut ei puututa)
    - default_equipment ainult kui NULL (Kratt `default` võib olla muutnud)
    - CSV-st tulnud rep-vahemikke EI puututa
    Tagastab muudetud/lisatud ridade arvu.
    """
    before = conn.total_changes
    for name, mg in MUSCLE_GROUP.items():
        conn.execute(
            """INSERT INTO exercises (name, default_equipment, muscle_group)
               VALUES (?,?,?)
               ON CONFLICT(name) DO UPDATE SET
                   muscle_group = CASE
                       WHEN exercises.muscle_group IS NULL OR exercises.muscle_group='muu'
                       THEN excluded.muscle_group ELSE exercises.muscle_group END,
                   default_equipment = COALESCE(exercises.default_equipment,
                                                excluded.default_equipment)
               WHERE exercises.muscle_group IS NULL OR exercises.muscle_group='muu'
                  OR exercises.default_equipment IS NULL""",
            (name, DEFAULT_EQUIPMENT.get(name), mg),
        )
    conn.commit()
    return conn.total_changes - before
