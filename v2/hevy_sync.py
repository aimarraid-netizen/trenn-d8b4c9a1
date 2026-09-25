"""Hevy -> trenn.db sünk (Hevy Pro API).

Voog: /workouts/events alates viimasest sünkist -> muudetud trenn kirjutatakse
uuesti (vana kirje + seeriad maha), kustutatud trenn kustutatakse ka baasist.
Iga trenni toor-JSON jääb hevy_workouts.raw_json-i.

Teisendus:
  - harjutuse nimi läbi exercise_config.from_hevy (HEVY_NAMES); tundmatu nimi
    jääb Hevy omaks + hoiatus -> täienda HEVY_NAMES ja käivita `rebuild`
  - soojendusseeriad (type=warmup) jäetakse seeriatest välja (progressioon/PR
    arvestavad ainult tööseeriaid), aga need on raw_json-is alles
  - 0 kg -> NULL (nagu CSV), duration_seconds -> duration_sec
  - pulssi ja kcal-i Hevy API ei anna

Dedup: sama jõutrenn teisest allikast (CSV/Strava) ±15 min -> 'duplicate',
varasem kirje jääb. CSV import omakorda keeldub, kui Hevy-kirje on olemas.

CLI (ubu terminalis, kaustas ~/projects/trenn):
  venv/bin/python v2/hevy_sync.py check                 # võtme test
  venv/bin/python v2/hevy_sync.py sync [--dry-run] [--no-html] [--retry-failed] [--all] [--quiet]
  v2/hevy_cron.sh                                       # cron: lukk + logi (logs/hevy_sync.log)
  venv/bin/python v2/hevy_sync.py map                   # Hevy nimed vs meie nimed
  venv/bin/python v2/hevy_sync.py rebuild [--dry-run]   # raw_json-ist uuesti (pärast HEVY_NAMES muutust)
"""
import argparse
import difflib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import hevy_api

import exercise_config as cfg
import parse_gymaholic_csv as pc
from db import TZ, get_db, init_schema, to_local_iso
from strava_sync import _dmy, notify
from validation import ValidationError

SOURCE = "hevy"
DEDUP_SOURCES = ("gymaholic_csv", "strava", "gymaholic")
EPOCH = "1970-01-01T00:00:00Z"
# events 'since' = viimane nähtud updated_at miinus varu (telefon võib hiljem üles laadida)
SINCE_MARGIN = timedelta(days=2)


def _utc(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def local_ts(w: dict) -> str:
    try:
        return to_local_iso(_utc(w["start_time"]))
    except (KeyError, TypeError, ValueError):
        return ""   # vigane trenn -> failed, logi siiski


# ---------- teisendus ----------

def to_parsed(w: dict) -> tuple[dict, list[str], int]:
    """Hevy trenn -> save_to_db struktuur. Tagastab (parsed, tundmatud nimed, soojendusi)."""
    start = _utc(w["start_time"])
    end = _utc(w["end_time"]) if w.get("end_time") else None
    meta = {
        "name": (w.get("title") or "Hevy trenn").strip(),
        "date": start.astimezone(TZ).replace(tzinfo=None),
        "duration_min": round((end - start).total_seconds() / 60) if end else None,
        "kcal": None, "avg_hr": None,
    }
    exercises, unknown, warmups = [], [], 0
    for ex in sorted(w.get("exercises") or [], key=lambda e: e.get("index", 0)):
        name, equip, known = cfg.from_hevy(ex["title"])
        if not known and ex["title"] not in unknown:
            unknown.append(ex["title"])
        sets = []
        for s in sorted(ex.get("sets") or [], key=lambda s: s.get("index", 0)):
            if s.get("type") == "warmup":
                warmups += 1
                continue
            reps = s.get("reps")
            sets.append({
                "reps": int(reps) if reps is not None else None,
                "weight": s.get("weight_kg") or None,       # 0 kg -> NULL
                "duration": s.get("duration_seconds") or None,
            })
        if not sets:
            continue
        # sama harjutus kaks korda trennis (nt superset + hiljem) -> üks plokk
        prev = next((e for e in exercises if e["name"] == name), None)
        if prev:
            prev["sets"].extend(sets)
            continue
        notes = [ex["notes"].strip()] if (ex.get("notes") or "").strip() else []
        exercises.append({"name": name, "equipment": equip, "rep_range": None,
                          "notes": notes, "sets": sets})
    return {"meta": meta, "exercises": exercises}, unknown, warmups


def _summary(parsed: dict, unknown: list[str], warmups: int) -> str:
    n_sets = sum(len(e["sets"]) for e in parsed["exercises"])
    vol = sum((s["weight"] or 0) * (s["reps"] or 0)
              for e in parsed["exercises"] for s in e["sets"])
    msg = f"{len(parsed['exercises'])} harjutust, {n_sets} seeriat, {vol:.0f} kg"
    if warmups:
        msg += f" (+{warmups} soojendust)"
    if unknown:
        msg += " · ⚠️ tundmatu: " + ", ".join(unknown)
    return msg


# ---------- baas ----------

def _row(conn, hevy_id: str):
    return conn.execute("SELECT * FROM hevy_workouts WHERE hevy_id=?", (hevy_id,)).fetchone()


def _record(conn, hevy_id: str, w: dict | None, status: str, wid: int | None,
            error: str | None, updated_at: str | None = None) -> None:
    old = _row(conn, hevy_id)
    conn.execute(
        """INSERT OR REPLACE INTO hevy_workouts
           (hevy_id, start_local, title, updated_at, status, workout_id, error, raw_json, synced_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (hevy_id,
         local_ts(w) if w else (old["start_local"] if old else None),
         w.get("title") if w else (old["title"] if old else None),
         (w or {}).get("updated_at") or updated_at,
         status, wid, error,
         json.dumps(w, ensure_ascii=False) if w else (old["raw_json"] if old else None),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()


def _drop_workout(conn, wid: int | None) -> None:
    if not wid:
        return
    conn.execute("DELETE FROM sets WHERE workout_id=?", (wid,))
    conn.execute("DELETE FROM workouts WHERE id=? AND source=?", (wid, SOURCE))


def import_workout(conn, w: dict, dry_run: bool = False) -> tuple[str, int | None, str]:
    """Üks Hevy trenn baasi. Tagastab (status, workout_id, sõnum)."""
    try:
        parsed, unknown, warmups = to_parsed(w)
        if not parsed["exercises"]:
            raise ValidationError("trennis pole ühtegi tööseeriat")
        ts = local_ts(w)
        twin = pc.find_existing_strength(conn, ts, sources=DEDUP_SOURCES)
        if twin:
            return ("duplicate", twin["id"],
                    f"juba olemas ({twin['source']}, id={twin['id']})")
        msg = _summary(parsed, unknown, warmups)
        if dry_run:
            return "imported", None, msg
        old = _row(conn, w["id"])
        if old and old["status"] == "imported":
            _drop_workout(conn, old["workout_id"])
        wid, _, _ = pc.save_to_db(parsed, conn, source=SOURCE)
        if (w.get("description") or "").strip():
            conn.execute("UPDATE workouts SET notes=? WHERE id=?", (w["description"].strip(), wid))
            conn.commit()
        return "imported", wid, msg
    except ValidationError as e:
        conn.rollback()
        return "failed", None, str(e)
    except (KeyError, TypeError, ValueError) as e:
        # Hevy API on beetas — struktuuri muutus ei tohi sünki katkestada
        conn.rollback()
        return "failed", None, f"ootamatu formaat ({type(e).__name__}: {e})"


def last_seen(conn) -> str:
    row = conn.execute("SELECT max(updated_at) FROM hevy_workouts").fetchone()[0]
    if not row:
        return EPOCH
    return (_utc(row) - SINCE_MARGIN).strftime("%Y-%m-%dT%H:%M:%SZ")


def sync(conn, client, dry_run: bool = False, retry_failed: bool = False,
         since: str | None = None) -> list[dict]:
    since = since or last_seen(conn)
    latest: dict[str, dict] = {}
    for ev in client.events(since):   # uusimad ees -> esimene esinemine on värskeim
        hid = ev["workout"]["id"] if ev.get("type") == "updated" else ev.get("id")
        if hid and hid not in latest:
            latest[hid] = ev

    results = []
    todo = sorted(latest.items(), key=lambda kv: (
        kv[1].get("workout", {}).get("start_time") or kv[1].get("deleted_at") or ""))
    for hid, ev in todo:
        old = _row(conn, hid)
        if ev.get("type") == "deleted":
            if not old or old["status"] == "deleted":
                continue
            if not dry_run:
                if old["status"] == "imported":
                    _drop_workout(conn, old["workout_id"])
                _record(conn, hid, None, "deleted", None, None, ev.get("deleted_at"))
            results.append({"id": hid, "status": "deleted", "workout_id": None,
                            "msg": "Hevys kustutatud", "name": old["title"],
                            "date": (old["start_local"] or "")[:10]})
            continue
        w = ev["workout"]
        unchanged = old and old["updated_at"] == w.get("updated_at")
        if unchanged and not (retry_failed and old["status"] == "failed"):
            continue
        status, wid, msg = import_workout(conn, w, dry_run)
        if not dry_run:
            _record(conn, hid, w, status, wid, msg if status == "failed" else None)
        if old and status == "imported":
            msg = "uuendatud: " + msg
        results.append({"id": hid, "status": status, "workout_id": wid, "msg": msg,
                        "name": w.get("title"), "date": local_ts(w)[:10]})
    return results


def rebuild(conn, dry_run: bool = False) -> list[dict]:
    """Töötle kõik salvestatud Hevy trennid raw_json-ist uuesti (API-t pole vaja)."""
    results = []
    rows = conn.execute("""SELECT * FROM hevy_workouts WHERE status IN ('imported','failed')
                           AND raw_json IS NOT NULL ORDER BY start_local""").fetchall()
    for old in rows:
        w = json.loads(old["raw_json"])
        status, wid, msg = import_workout(conn, w, dry_run)
        if not dry_run:
            _record(conn, old["hevy_id"], w, status, wid, msg if status == "failed" else None)
        results.append({"id": old["hevy_id"], "status": status, "workout_id": wid, "msg": msg,
                        "name": w.get("title"), "date": local_ts(w)[:10]})
    return results


# ---------- harjutuste vastavus ----------

def _norm(name: str) -> str:
    n = re.sub(r"\([^)]*\)", " ", name.lower())
    n = re.sub(r"\b(barbell|dumbbell|cable|machine|with|rope|seated|standing|lying)\b", " ", n)
    n = re.sub(r"(es|s)\b", "", n)            # curls/flyes/rows -> curl/fly/row
    return " ".join(re.sub(r"[^a-z ]", " ", n).split())


def suggest(title: str) -> list[str]:
    ours = list(cfg.MUSCLE_GROUP)
    by_norm = {_norm(o): o for o in ours}
    exact = by_norm.get(_norm(title))
    if exact:
        return [exact]
    close = difflib.get_close_matches(_norm(title), list(by_norm), n=3, cutoff=0.5)
    return [by_norm[c] for c in close]


def cmd_map(client) -> None:
    used: dict[str, int] = {}
    for w in client.workouts():
        for ex in w.get("exercises") or []:
            used[ex["title"]] = used.get(ex["title"], 0) + 1
    for r in client.routines():
        for ex in r.get("exercises") or []:
            used.setdefault(ex["title"], 0)
    if not used:
        print("Hevys pole veel ühtegi trenni ega kava.")
        return
    print(f"{'Hevy nimi':<42} {'trenne':>6}  vaste")
    for title in sorted(used):
        name, equip, known = cfg.from_hevy(title)
        if known:
            tag = f"✓ {name} [{equip or '-'}]"
        else:
            sug = suggest(title)
            tag = "? " + (" | ".join(sug) if sug else "(uus harjutus)")
        print(f"{title:<42} {used[title]:>6}  {tag}")


# ---------- väljund ----------

ICON = {"imported": "✓", "duplicate": "↩", "failed": "✗", "deleted": "🗑"}


def _discord_lines(results: list[dict]) -> list[str]:
    out = []
    for r in results:
        head = f"{r['name']} · {_dmy(r['date'])}"
        if r["status"] == "imported":
            out.append(f"🏋️ Hevy → {head}: {r['msg']}")
        elif r["status"] == "failed":
            out.append(f"⚠️ Hevy → {head}: {r['msg']}")
        elif r["status"] == "deleted":
            out.append(f"🗑 Hevy → {head}: kustutatud ka baasist")
    return out


def _print(results: list[dict], dry_run: bool) -> tuple[int, int]:
    if dry_run:
        print("(DRY-RUN — baasi ei kirjutatud)")
    for r in results:
        print(f"{ICON[r['status']]} {r['status']:<9} {_dmy(r['date']) if r['date'] else '?':<10} "
              f"{r['name']}: {r['msg']}")
    n_new = sum(r["status"] in ("imported", "deleted") for r in results)
    n_fail = sum(r["status"] == "failed" for r in results)
    return n_new, n_fail


def _render() -> None:
    import render_html
    render_html.main()


def main() -> None:
    p = argparse.ArgumentParser(description="Hevy -> trenn.db sünk")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="kontrolli API-võtit")
    s = sub.add_parser("sync", help="impordi uued/muudetud trennid")
    s.add_argument("--dry-run", action="store_true", help="ära kirjuta baasi")
    s.add_argument("--no-html", action="store_true", help="ära regenereeri HTML-i")
    s.add_argument("--retry-failed", action="store_true", help="proovi failed-trenne uuesti")
    s.add_argument("--all", action="store_true", help="kogu ajalugu, mitte ainult uued")
    s.add_argument("--quiet", action="store_true", help="muutusteta käivitus ei prindi midagi (cron)")
    sub.add_parser("map", help="Hevy harjutusnimed vs meie nimed")
    r = sub.add_parser("rebuild", help="töötle salvestatud trennid uuesti (pärast HEVY_NAMES muutust)")
    r.add_argument("--dry-run", action="store_true")
    r.add_argument("--no-html", action="store_true")
    args = p.parse_args()

    try:
        if args.cmd == "rebuild":
            conn = get_db()
            init_schema(conn)
            results = rebuild(conn, args.dry_run)
            n_new, n_fail = _print(results, args.dry_run)
            print(f"— {len(results)} trenni uuesti töödeldud, {n_fail} viga")
            if not args.dry_run and results and not args.no_html:
                _render()
            sys.exit(1 if n_fail else 0)

        client = hevy_api.HevyClient()
        if args.cmd == "check":
            u = client.user_info()
            print(f"✓ Võti töötab: {u.get('name') or u.get('username') or '?'} · "
                  f"{client.workout_count()} trenni Hevys")
            return
        if args.cmd == "map":
            cmd_map(client)
            return

        conn = get_db()
        init_schema(conn)
        results = sync(conn, client, args.dry_run, args.retry_failed,
                       since=EPOCH if args.all else None)
        if args.quiet and not results:
            conn.close()
            return
        n_new, n_fail = _print(results, args.dry_run)
        print(f"— {datetime.now():%d.%m.%Y %H:%M} · {len(results)} muudatust: {n_new} baasi, "
              f"{n_fail} viga ({client.calls} API päringut)")
        if not args.dry_run:
            notify(_discord_lines(results))
            if n_new and not args.no_html:
                _render()
        conn.close()
        sys.exit(1 if n_fail else 0)
    except hevy_api.HevyError as e:
        print(f"✗ {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
