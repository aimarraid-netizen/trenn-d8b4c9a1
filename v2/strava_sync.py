"""Strava -> trenn.db automaatne sünk: Gymaholicu jõutrennid + kardio.

Gymaholic kirjutab Strava tegevuse kirjeldusse kõik seeriad:

    Traditional Strength Training

    Total weight: 1234 kg
    Total cardio: 0h:05m

    Rowing With Rowing Ergometer
    • 5:00

    Romanian Deadlift
    • 1x12 @ 60kg
    • 2x8 @ 70kg

    Single-Leg Leg Extension
    • 2x10

Kontroll: seeriatest arvutatud maht peab klappima 'Total weight'-iga, muidu on
formaat muutunud -> tegevus märgitakse failed + Discordi hoiatus (CSV jääb varuteeks).

Pulss: jõutrennil saadab Gymaholic ühe pulsipunkti iga harjutuse lõpus (+ alguspunkt)
-> sets.avg_hr, kui punktide arv klapib harjutuste arvuga. Kardiol 1 s voog -> tsoonid.

Dedup: jõutrenn ±15 min (CSV <-> Strava), kardio ±2 min (FIT/GPX <-> Strava).
Iga tegevus töödeldakse üks kord (tabel strava_activities); hiljem saadetud CSV
asendab Strava jõutrenni (parse_gymaholic_csv._take_over_strava).

CLI (ubu terminalis, kaustas ~/projects/trenn):
  venv/bin/python v2/strava_sync.py auth [--code KOOD_VÕI_URL]
  venv/bin/python v2/strava_sync.py sync [--days 14] [--dry-run] [--no-html]
                                         [--activity ID] [--retry-failed]
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import parse_gymaholic_csv as pc
import strava_api
import validation as val
from cardio_common import (
    CARDIO_INSERT_SQL,
    STRAVA_SPORT,
    cardio_insert_values,
    find_existing_cardio,
    sport_et,
)
from db import get_db, init_schema, to_local_iso
from hr_config import zone_minutes
from validation import ValidationError

STRENGTH_SPORTS = {"WeightTraining", "Workout", "Crossfit", "HighIntensityIntervalTraining"}
SUPPORTED_SPORTS = STRENGTH_SPORTS | set(STRAVA_SPORT)
MAX_SETS_PER_LINE = 20

BULLET_RE = re.compile(r"^[•·*-]\s*(.+?)$")
# "3x12 @ 22.5kg" / "2x10" (kehakaal / kaal logimata)
SETS_RE = re.compile(r"^(\d+)\s*[x×]\s*(\d+)(?:\s*@\s*(\d+(?:[.,]\d+)?)\s*([a-z]+))?$", re.I)
# "5:00" / "3x0:45" / "1:05:00"
TIME_RE = re.compile(r"^(?:(\d+)\s*[x×]\s*)?(\d+(?::\d{2}){1,2})$")
TOTAL_RE = re.compile(r"^Total weight:\s*(\d+(?:[.,]\d+)?)\s*([a-z]+)", re.I)


class DescriptionError(ValidationError):
    """Kirjeldus pole Gymaholicu formaadis või kontrollsumma ei klapi."""


# ---------- kirjelduse parser ----------

def _time_sec(raw: str) -> int:
    secs = 0
    for part in raw.split(":"):
        secs = secs * 60 + int(part)
    return secs


def _parse_bullet(txt: str) -> list[dict]:
    """'3x12 @ 22.5kg' -> 3 seeriat; '5:00' -> 1 aja-seeria. Tundmatu -> DescriptionError."""
    m = SETS_RE.match(txt)
    if m:
        n, reps = int(m.group(1)), int(m.group(2))
        unit = (m.group(4) or "kg").lower()
        if unit != "kg":
            raise DescriptionError(f"ühik '{unit}' — vali Gymaholicus kilod ({txt!r})")
        weight = float(m.group(3).replace(",", ".")) if m.group(3) else None
        sets = [{"reps": reps, "weight": weight or None, "duration": None}] * n
    else:
        m = TIME_RE.match(txt)
        if not m:
            raise DescriptionError(f"tundmatu seeriarida {txt!r}")
        n = int(m.group(1) or 1)
        sets = [{"reps": None, "weight": None, "duration": _time_sec(m.group(2)) or None}] * n
    if not 1 <= n <= MAX_SETS_PER_LINE:
        raise DescriptionError(f"ebausutav seeriate arv {n} ({txt!r})")
    return [dict(s) for s in sets]


def parse_description(text: str | None) -> tuple[list[dict], float]:
    """Gymaholicu kirjeldus -> (harjutused parse_gymaholic_csv formaadis, Total weight)."""
    exercises, total, current = [], None, None
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = TOTAL_RE.match(line)
        if m:
            if m.group(2).lower() != "kg":
                raise DescriptionError(f"ühik '{m.group(2)}' — vali Gymaholicus kilod")
            total = float(m.group(1).replace(",", "."))
            continue
        b = BULLET_RE.match(line)
        if b:
            if current is None:
                raise DescriptionError(f"seeria ilma harjutuseta: {line!r}")
            current["sets"] += _parse_bullet(b.group(1).strip())
            continue
        # uus nimi; seeriateta read ("Traditional Strength Training", "Total cardio: …")
        # on päis ja visatakse allpool välja
        current = {"name": line, "rep_range": None, "notes": [], "sets": [], "avg_hr": None}
        exercises.append(current)

    if total is None:
        raise DescriptionError("Gymaholicu kirjeldus puudub" if not (text or "").strip() else
                               "'Total weight' puudub — pole Gymaholicu kirjeldus "
                               "või formaat on muutunud")
    exercises = [e for e in exercises if e["sets"]]
    if not exercises:
        # enne 05.2026 kirjutas Gymaholic ainult kokkuvõtte ("Exercises: 7", nimekiri)
        raise DescriptionError("kirjelduses pole seeriaid (vana kokkuvõtte-formaat?)")
    vol = sum(s["weight"] * s["reps"] for e in exercises for s in e["sets"]
              if s["weight"] and s["reps"])
    if abs(vol - total) > 1:
        raise DescriptionError(f"seeriate maht {vol:g} kg ≠ Total weight {total:g} kg "
                               "— formaat muutunud?")
    return exercises, total


# ---------- võrdlus olemasoleva trenniga ----------

def _sig(reps, weight, duration) -> tuple:
    return (reps, weight or None, int(duration) if duration else None)


def _fmt_sets(sigs: list[tuple] | None) -> str:
    if not sigs:
        return "—"
    out, i = [], 0
    while i < len(sigs):
        j = i
        while j < len(sigs) and sigs[j] == sigs[i]:
            j += 1
        reps, w, dur = sigs[i]
        if dur and not reps:
            one = f"{dur}s"
        else:
            one = f"{reps or '?'}" + (f"@{w:g}" if w else "")
        out.append(f"{j - i}×{one}")
        i = j
    return " ".join(out)


def compare_sets(conn, workout_id: int, exercises: list[dict]) -> list[str]:
    """Erinevused baasis oleva trenni ja Strava kirjelduse seeriate vahel."""
    db: dict[str, list] = {}
    for r in conn.execute("SELECT exercise_name, reps, weight_kg, duration_sec FROM sets "
                          "WHERE workout_id=? ORDER BY id", (workout_id,)):
        db.setdefault(r["exercise_name"], []).append(
            _sig(r["reps"], r["weight_kg"], r["duration_sec"]))
    new: dict[str, list] = {}
    for e in exercises:
        new.setdefault(e["name"], []).extend(
            _sig(s["reps"], s["weight"], s["duration"]) for s in e["sets"])
    return [f"{name}: baas {_fmt_sets(db.get(name))} | Strava {_fmt_sets(new.get(name))}"
            for name in dict.fromkeys([*new, *db]) if db.get(name) != new.get(name)]


# ---------- import ----------

def local_ts(act: dict) -> str:
    """Strava start_date (UTC) -> baasi lokaalaeg, sama teisendus mis FIT-il."""
    return to_local_iso(datetime.strptime(act["start_date"], "%Y-%m-%dT%H:%M:%SZ"))


def _int_or_none(v, check=None):
    if v is None:
        return None
    v = int(round(v))
    if check and not check(v):
        return None
    return v


def import_strength(conn, client, act: dict, dry_run: bool = False) -> tuple[str, int | None, str]:
    ts = local_ts(act)
    twin = pc.find_existing_strength(conn, ts)
    if twin:
        # baasis olev trenn võidab; kirjeldus ainult võrdluseks (vanem formaat ilma
        # seeriateta ei tohi teha juba olemasolevast trennist viga)
        msg = f"juba baasis ({twin['source']}, id={twin['id']})"
        try:
            diffs = compare_sets(conn, twin["id"], parse_description(act.get("description"))[0])
        except DescriptionError as e:
            return "duplicate", twin["id"], f"{msg}; kirjeldust ei saa võrrelda: {e}"
        msg += ("; ERINEVUSED:\n    " + "\n    ".join(diffs)) if diffs else "; seeriad klapivad"
        return "duplicate", twin["id"], msg

    exercises, total = parse_description(act.get("description"))
    nsets = sum(len(e["sets"]) for e in exercises)

    hr = client.streams(act["id"]).get("heartrate") or []
    if len(hr) == len(exercises) + 1:
        for ex, h in zip(exercises, hr[1:]):
            ex["avg_hr"] = _int_or_none(h, val.valid_hr)
    hr_note = "harjutuste pulss ✓" if len(hr) == len(exercises) + 1 else \
        f"harjutuste pulss puudub ({len(hr)} punkti / {len(exercises)} harjutust)"

    avg_hr = _int_or_none(act.get("average_heartrate"), val.valid_hr)
    summary = (f"{len(exercises)} harjutust, {nsets} seeriat, {total:g} kg"
               + (f", ❤️ {avg_hr}" if avg_hr else "") + f" · {hr_note}")
    if dry_run:
        return "imported", None, summary
    parsed = {"meta": {
        "name": act.get("name") or "Jõutrenn",
        "date": datetime.strptime(ts, "%Y-%m-%d %H:%M:%S"),
        "duration_min": int(act["elapsed_time"] / 60) if act.get("elapsed_time") else None,
        "kcal": _int_or_none(act.get("calories"), val.valid_kcal) or None,
        "avg_hr": avg_hr,
    }, "exercises": exercises}
    wid, _, _ = pc.save_to_db(parsed, conn, source="strava")
    return "imported", wid, summary


def import_cardio(conn, client, act: dict, dry_run: bool = False) -> tuple[str, int | None, str]:
    ts = local_ts(act)
    twin = find_existing_cardio(conn, ts)
    if twin:
        return "duplicate", twin["id"], f"juba baasis ({twin['source']}, id={twin['id']})"

    hr = [int(h) for h in client.streams(act["id"]).get("heartrate") or [] if h]
    dur = act.get("elapsed_time")
    data = {
        "duration_sec": float(dur) if dur else None,
        "distance_m": act.get("distance") or None,
        "avg_hr": _int_or_none(act.get("average_heartrate")),
        "max_hr_val": max(hr) if hr else _int_or_none(act.get("max_heartrate")),
        "kcal": _int_or_none(act.get("calories")) or None,
        "ascent_m": act.get("total_elevation_gain") or None,
        "zone_min": zone_minutes(hr, dur),
    }
    for field, check in (("avg_hr", val.valid_hr), ("max_hr_val", val.valid_hr),
                         ("distance_m", val.valid_distance_m), ("kcal", val.valid_kcal)):
        if not check(data[field]):
            data[field] = None
    sport = STRAVA_SPORT[act["sport_type"]]
    km = f"{data['distance_m'] / 1000:.1f} km, " if data["distance_m"] else ""
    summary = (f"{sport_et(sport)} {km}{int(dur // 60) if dur else '?'} min"
               + (f", ❤️ {data['avg_hr']}" if data["avg_hr"] else ""))
    if dry_run:
        return "imported", None, summary
    cur = conn.execute(CARDIO_INSERT_SQL, cardio_insert_values(
        ts, act.get("name") or sport_et(sport), sport, data, "strava"))
    conn.commit()
    return "imported", cur.lastrowid, summary


def _record(conn, act: dict, status: str, wid: int | None, error: str | None) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO strava_activities
           (activity_id, start_local, sport_type, name, status, workout_id, error, synced_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (act["id"], local_ts(act), act.get("sport_type"), act.get("name"), status, wid, error,
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()


def process(conn, client, act: dict, dry_run: bool = False) -> tuple[str, int | None, str]:
    """Üks tegevus (detailvaade). Tagastab (status, workout_id, sõnum).

    StravaError (võrk, limiit) lendab läbi — tegevust ei märgita, järgmine käivitus proovib uuesti.
    """
    sport = act.get("sport_type") or act.get("type")
    try:
        if sport == "WeightTraining" or "Total weight:" in (act.get("description") or ""):
            return import_strength(conn, client, act, dry_run)
        if sport in STRAVA_SPORT:
            return import_cardio(conn, client, act, dry_run)
        return "skipped", None, f"spordiala {sport} pole toetatud"
    except ValidationError as e:
        return "failed", None, f"{e} — saada CSV Kratile"


def sync(conn, client, days: int = 14, dry_run: bool = False, activity_id: int | None = None,
         retry_failed: bool = False) -> list[dict]:
    if activity_id:
        todo = [{"id": activity_id}]
    else:
        after = int(time.time() - days * 86400)
        done = {r["activity_id"]: r["status"] for r in conn.execute(
            "SELECT activity_id, status FROM strava_activities")}
        todo = [a for a in client.activities(after)
                if a["id"] not in done or (retry_failed and done[a["id"]] == "failed")]
        # toetamata spordialad: detailpäringut pole vaja
        for a in todo:
            if a.get("sport_type") not in SUPPORTED_SPORTS:
                a["_skip"] = True
    results = []
    for a in sorted(todo, key=lambda a: a.get("start_date", "")):
        if a.get("_skip"):
            status, wid, msg = "skipped", None, f"spordiala {a.get('sport_type')} pole toetatud"
            act = a
        else:
            act = client.activity(a["id"])
            status, wid, msg = process(conn, client, act, dry_run)
        if not dry_run:
            _record(conn, act, status, wid, msg if status in ("failed", "skipped") else None)
        results.append({"id": act["id"], "status": status, "workout_id": wid, "msg": msg,
                        "name": act.get("name"), "sport": act.get("sport_type"),
                        "date": local_ts(act)[:10]})
    return results


# ---------- väljund ----------

ICON = {"imported": "✓", "duplicate": "↩", "skipped": "·", "failed": "✗"}


def _dmy(iso_date: str) -> str:
    y, m, d = iso_date.split("-")
    return f"{d}.{m}.{y}"


def notify(lines: list[str]) -> None:
    """Discordi webhook (TRENN_DISCORD_WEBHOOK .env-is); puudumisel ainult stdout."""
    url = os.getenv("TRENN_DISCORD_WEBHOOK")
    if not url or not lines:
        return
    body = json.dumps({"content": "\n".join(lines)[:1900]}).encode()
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json", "User-Agent": "trenn-strava-sync"})
    try:
        urllib.request.urlopen(req, timeout=15).close()
    except Exception as e:
        print(f"HOIATUS: Discordi teavitus ebaõnnestus: {e}", file=sys.stderr)


def _discord_lines(results: list[dict]) -> list[str]:
    out = []
    for r in results:
        head = f"{r['name']} · {_dmy(r['date'])}"
        if r["status"] == "imported":
            icon = "🏋️" if r["sport"] in STRENGTH_SPORTS else "🥾"
            out.append(f"{icon} Strava → {head}: {r['msg']}")
        elif r["status"] == "failed":
            out.append(f"⚠️ Strava → {head}: {r['msg']}")
    return out


def cmd_auth(args) -> None:
    if not args.code:
        print("1) Ava brauseris ja kinnita:\n\n   " + strava_api.authorize_url() + "\n")
        print("2) Brauser suunab lehele http://localhost/exchange_token?...&code=XXXX "
              "(leht ise ei avane — see on normaalne).")
        print("3) Kopeeri aadressiriba ja käivita:\n"
              "   venv/bin/python v2/strava_sync.py auth --code '<aadress või kood>'")
        return
    code = args.code
    if "code=" in code:
        code = urllib.parse.parse_qs(urllib.parse.urlparse(code).query)["code"][0]
    strava_api.exchange_code(code)
    print(f"✓ Token salvestatud: {strava_api.TOKEN_PATH}")


def cmd_sync(args) -> int:
    conn = get_db()
    init_schema(conn)
    client = strava_api.StravaClient()
    results = sync(conn, client, args.days, args.dry_run, args.activity, args.retry_failed)
    if args.dry_run:
        print("(DRY-RUN — baasi ei kirjutatud)")
    for r in results:
        print(f"{ICON[r['status']]} {r['status']:<9} {_dmy(r['date'])} {r['sport'] or '?':<14} "
              f"{r['name']}: {r['msg']}")
    n_new = sum(r["status"] == "imported" for r in results)
    n_fail = sum(r["status"] == "failed" for r in results)
    print(f"— {len(results)} uut tegevust: {n_new} imporditud, {n_fail} viga "
          f"({client.calls} API päringut)")
    if not args.dry_run:
        notify(_discord_lines(results))
        if n_new and not args.no_html:
            import render_html
            render_html.main()
    conn.close()
    return 1 if n_fail else 0


def main() -> None:
    p = argparse.ArgumentParser(description="Strava -> trenn.db sünk")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("auth", help="ühekordne OAuth seadistus")
    a.add_argument("--code", help="autoriseerimiskood või kogu tagasisuunamise URL")
    s = sub.add_parser("sync", help="impordi uued tegevused")
    s.add_argument("--days", type=int, default=14, help="mitu päeva tagasi vaadata (vaikimisi 14)")
    s.add_argument("--dry-run", action="store_true", help="ära kirjuta baasi")
    s.add_argument("--no-html", action="store_true", help="ära regenereeri HTML-i")
    s.add_argument("--activity", type=int, help="töötle ainult see tegevus (ka juba töödeldud)")
    s.add_argument("--retry-failed", action="store_true", help="proovi failed-tegevusi uuesti")
    args = p.parse_args()
    try:
        if args.cmd == "auth":
            cmd_auth(args)
        else:
            sys.exit(cmd_sync(args))
    except strava_api.StravaError as e:
        print(f"✗ {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
