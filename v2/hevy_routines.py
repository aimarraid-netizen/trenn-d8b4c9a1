"""Gymaholicu kava (.gymdata) -> Hevy rutiinid (Hevy Pro API).

Iga kava trenn -> üks Hevy rutiin sama nimega. Olemasolev sama nimega rutiin
uuendatakse (PUT), mitte ei dubleerita.

Kavamärkustest loetakse (gymdata.py: plaanitud kaal elab märkuses, qnty=0):
  "45 kg"                  -> seeria weight_kg
  "6–10 reps"              -> rep_range {6, 10}
  "tühi kang ×10, ~60% ×5" -> kaks soojendusseeriat (20 kg ×10, 60% ×5);
                              hevy_sync jätab soojendused progressioonist välja
Märkus ise läheb harjutuse notes'i (Hevy näitab trenni ajal).

CLI (ubu terminalis, kaustas ~/projects/trenn):
  venv/bin/python v2/hevy_routines.py <kava.gymdata>          # eelvaade
  venv/bin/python v2/hevy_routines.py <kava.gymdata> --push   # Hevysse
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import gymdata
import hevy_api

import exercise_config as cfg

EMPTY_BAR_KG = 20
KG_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*kg\b", re.I)
RANGE_RE = re.compile(r"(\d+)\s*[–-]\s*(\d+)\s*reps", re.I)
WARMUP_RE = re.compile(r"tühi kang\s*[×x]\s*(\d+).*?(\d+)\s*%\s*[×x]\s*(\d+)", re.I)

GYMAHOLIC_NAMES = {v: k for k, v in gymdata.EXERCISE_IDS.items()}


def _round_plate(kg: float) -> float:
    return round(kg / 2.5) * 2.5


def to_routine(wo: dict) -> dict:
    exercises = []
    for p in wo["plans"]:
        name = GYMAHOLIC_NAMES.get(p["exercise"])
        if name not in cfg.HEVY_TEMPLATES:
            raise ValueError(f"harjutusel {name or p['exercise']} pole Hevy vastet "
                             "(exercise_config.HEVY_TEMPLATES)")
        _, template_id = cfg.HEVY_TEMPLATES[name]
        note = " ".join(p.get("notes") or [])
        sets = []
        if cfg.is_cardio(name):
            sets = [{"type": "normal", "duration_seconds": s["time"]} for s in p["sets"]]
        else:
            m = KG_RE.search(note)
            weight = float(m.group(1).replace(",", ".")) if m else None
            r = RANGE_RE.search(note)
            rep_range = {"start": int(r.group(1)), "end": int(r.group(2))} if r else None
            w = WARMUP_RE.search(note)
            if w and weight:
                sets += [
                    {"type": "warmup", "weight_kg": EMPTY_BAR_KG, "reps": int(w.group(1))},
                    {"type": "warmup", "weight_kg": _round_plate(weight * int(w.group(2)) / 100),
                     "reps": int(w.group(3))},
                ]
            sets += [{"type": "normal", "weight_kg": weight, "reps": s["reps"] or None,
                      "rep_range": rep_range} for s in p["sets"]]
        rest = max((s.get("rest") or 0) for s in p["sets"]) or None
        exercises.append({"exercise_template_id": template_id, "superset_id": None,
                          "rest_seconds": rest, "notes": note or None, "sets": sets})
    return {"title": wo["name"], "folder_id": None, "notes": wo.get("notes") or "",
            "exercises": exercises}


def _describe(routine: dict) -> str:
    by_id = {tid: title for title, tid in cfg.HEVY_TEMPLATES.values()}
    lines = [f"## {routine['title']}"]
    for ex in routine["exercises"]:
        parts = []
        for s in ex["sets"]:
            if "duration_seconds" in s:
                parts.append(f"{s['duration_seconds'] // 60} min")
                continue
            kg = f" @ {s['weight_kg']:g} kg" if s.get("weight_kg") else ""
            tag = "S " if s["type"] == "warmup" else ""
            rr = s.get("rep_range")
            reps = f"{s['reps']}" + (f" ({rr['start']}–{rr['end']})" if rr else "")
            parts.append(f"{tag}{reps}{kg}")
        rest = f" · puhkus {ex['rest_seconds']} s" if ex["rest_seconds"] else ""
        lines.append(f"  {by_id[ex['exercise_template_id']]}: {' | '.join(parts)}{rest}")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description="Gymaholicu kava -> Hevy rutiinid")
    p.add_argument("gymdata", help="kavafail (.gymdata)")
    p.add_argument("--push", action="store_true", help="loo/uuenda rutiinid Hevys")
    args = p.parse_args()

    plan = gymdata.read(args.gymdata)
    routines = [to_routine(wo) for wo in plan["wos"]]
    for r in routines:
        print(_describe(r) + "\n")
    if not args.push:
        print("(eelvaade — Hevysse saatmiseks lisa --push)")
        return
    try:
        client = hevy_api.HevyClient()
        existing = {r["title"]: r["id"] for r in client.routines()}
        for r in routines:
            if r["title"] in existing:
                client.update_routine(existing[r["title"]], r)
                print(f"↻ uuendatud: {r['title']}")
            else:
                client.create_routine(r)
                print(f"✓ loodud: {r['title']}")
    except hevy_api.HevyError as e:
        print(f"✗ {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
