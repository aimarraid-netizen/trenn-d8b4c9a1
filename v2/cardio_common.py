"""Trenn 2.0 — kardio-importerite (FIT, GPX, Strava) jagatud osad.

Üks tõeallikas spordinimedele, kardio dedup-võtmele ja failinime puhastusele.
(Esimene samm FIT/GPX ~70 % copy-paste'i koondamisel; vt plaan Faas 4.)
"""
import re

from db import find_workout_near

SPORT_MAP = {
    "walking": "kõndimine",
    "cycling": "rattasõit",
    "biking": "rattasõit",
    "swimming": "ujumine",
    "hiking": "matk",
    "running": "jooksmine",
    "generic": "kardio",
    "other": "kardio",
}

# Strava sport_type -> SPORT_MAP võti
STRAVA_SPORT = {
    "Walk": "walking",
    "Hike": "hiking",
    "Run": "running", "TrailRun": "running", "VirtualRun": "running",
    "Ride": "cycling", "VirtualRide": "cycling", "EBikeRide": "cycling",
    "MountainBikeRide": "cycling", "GravelRide": "cycling", "EMountainBikeRide": "cycling",
    "Swim": "swimming",
    "Rowing": "other", "VirtualRow": "other", "Elliptical": "other", "StairStepper": "other",
}

CARDIO_SOURCES = ("fit", "gpx", "strava")
# FIT ja Strava sama tegevuse algus erineb ~1 s; kaks eri kardiotrenni 2 min sees ei alga
CARDIO_DEDUP_SEC = 120

_ARCHIVE_PREFIX = re.compile(r"^\d{8}_\d{6}_")
_DUP_SUFFIX = re.compile(r"_\d+$")


def sport_et(sport_raw: str | None) -> str:
    s = (sport_raw or "generic").lower()
    return SPORT_MAP.get(s, s)


def clean_cardio_name(stem: str) -> str:
    """Failinimi → trenni nimi: eemalda arhiivi prefiks 'YYYYMMDD_HHMMSS_' ja '_2' sufiks.

    Nii annab sama fail enne ja pärast arhiveerimist sama nime.
    """
    s = _ARCHIVE_PREFIX.sub("", stem)
    s = _DUP_SUFFIX.sub("", s)
    return s.strip() or stem


def find_existing_cardio(conn, ts_str: str):
    """Kardio dedup-võti on ALGUSHETK ±2 min (mitte failinimi, mis arhiveerimisel muutub).

    Allikast sõltumatu: sama matk FIT-ist ja Stravast on üks trenn.
    """
    return find_workout_near(conn, ts_str, CARDIO_DEDUP_SEC, strength=False)


def cardio_insert_values(ts_str: str, workout_name: str, sport: str, data: dict,
                         source: str) -> tuple:
    """Väärtused INSERT INTO workouts (...) VALUES (...) jaoks — sama järjekord mõlemas parseris."""
    zm = data.get("zone_min") or {}
    duration_min = int(data["duration_sec"] / 60) if data.get("duration_sec") else None
    return (
        ts_str, ts_str[:10], workout_name, sport_et(sport),
        duration_min, data.get("distance_m"), data.get("avg_hr"), data.get("max_hr_val"),
        data.get("kcal"),
        zm.get("z1"), zm.get("z2"), zm.get("z3"), zm.get("z4"), zm.get("z5"),
        data.get("ascent_m"), source,
    )


CARDIO_INSERT_SQL = """INSERT INTO workouts
    (timestamp, date, workout_name, workout_type,
     duration_min, distance_m, avg_hr, max_hr, kcal,
     z1_min, z2_min, z3_min, z4_min, z5_min, ascent_m, source)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""
