#!/home/aimar/trenn/venv/bin/python3
"""
Valmista ette failid Claude Projects jaoks.
Genereerib Markdown formaadis kokkuvõtte treeningute ajaloost.
"""

import csv as csv_module
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / 'v2'))
from db import get_db  # noqa: E402

# Andmete asukoht
DATA_DIR = Path(__file__).parent / 'data'
RANGES_FILE = Path(__file__).parent / 'harjutuste_vahemikud.csv'
OUTPUT_DIR = Path(__file__).parent / 'claude_project'
OUTPUT_FILE = OUTPUT_DIR / 'fitness_knowledge.md'

# DB hoiab kardio tüüpe eesti keeles, vana raport inglise keeles
TYPE_EN = {"kõndimine": "walking", "jooksmine": "running", "rattasõit": "cycling",
           "matk": "hiking", "ujumine": "swimming"}


def load_exercise_ranges():
    """Lae harjutuste vahemikud CSV-st: {name: {sets, reps_min, reps_max, category}}"""
    ranges = {}
    if not RANGES_FILE.exists():
        return ranges
    with open(RANGES_FILE, 'r', encoding='utf-8') as f:
        reader = csv_module.DictReader(f)
        for row in reader:
            ranges[row['Harjutus']] = {
                'sets': int(row['Setid']),
                'reps_min': int(row['Kordused_min']),
                'reps_max': int(row['Kordused_max']),
                'category': row['Kategooria'],
            }
    return ranges


def compute_exercise_baselines(gym_workouts):
    """Esimene salvestatud töökaal iga harjutuse kohta (vanuse järgi)."""
    baselines = {}
    sorted_workouts = sorted(gym_workouts, key=lambda x: x.get('timestamp', ''))
    for w in sorted_workouts:
        date = w.get('timestamp', '')[:10]
        for ex in w.get('exercises', []):
            name = ex.get('name', '')
            weight = ex.get('weight_kg', 0)
            if not name or weight <= 0:
                continue
            if name in baselines:
                continue
            baselines[name] = {
                'weight': weight,
                'sets': ex.get('sets', 0),
                'reps': ex.get('reps', 0),
                'date': date,
            }
    return baselines


def compute_current_top(gym_workouts):
    """Iga harjutuse viimane raskeim töökaal (uusim salvestus või kõrgeim)."""
    current = {}
    sorted_workouts = sorted(gym_workouts, key=lambda x: x.get('timestamp', ''), reverse=True)
    for w in sorted_workouts:
        date = w.get('timestamp', '')[:10]
        for ex in w.get('exercises', []):
            name = ex.get('name', '')
            weight = ex.get('weight_kg', 0)
            if not name or weight <= 0:
                continue
            if name not in current:
                current[name] = {
                    'weight': weight,
                    'sets': ex.get('sets', 0),
                    'reps': ex.get('reps', 0),
                    'date': date,
                }
    return current

def load_workout_history():
    """Jõutrennid SQLite-st (säilitab vana JSON-i kuju, mida raport ootab)."""
    conn = get_db()
    out = []
    for w in conn.execute(
        "SELECT * FROM workouts WHERE source IN ('gymaholic','gymaholic_csv') "
        "ORDER BY timestamp"
    ):
        wd = dict(w)
        by_ex = {}
        for s in conn.execute(
            "SELECT * FROM sets WHERE workout_id=? ORDER BY id", (wd["id"],)
        ):
            e = by_ex.setdefault(s["exercise_name"], {
                "name": s["exercise_name"], "sets": 0, "reps": 0,
                "weight_kg": 0, "total_volume": 0.0,
            })
            e["sets"] += 1
            e["total_volume"] += s["total_volume"] or 0.0
            if (s["weight_kg"] or 0) >= e["weight_kg"]:
                e["weight_kg"] = s["weight_kg"] or 0
                e["reps"] = s["reps"] or 0
        wd["exercises"] = list(by_ex.values())
        wd["source"] = "gymaholic"
        wd["total_volume"] = wd.get("total_volume") or 0
        out.append(wd)
    conn.close()
    return out


def load_workoutdoor_data():
    """Kardiotrennid SQLite-st (säilitab vana CSV-kuju, mida raport ootab)."""
    conn = get_db()
    out = []
    for w in conn.execute(
        "SELECT * FROM workouts WHERE source IN ('fit','gpx') ORDER BY timestamp"
    ):
        wd = dict(w)
        out.append({
            'source': 'workoutdoor',
            'timestamp': wd['timestamp'],
            'date': wd['date'],
            'workout_name': wd.get('workout_name') or 'Cardio',
            'workout_type': TYPE_EN.get(wd.get('workout_type'), wd.get('workout_type') or 'cardio'),
            'duration_min': wd.get('duration_min') or 0,
            'distance': (wd.get('distance_m') or 0) / 1000,
            'avg_hr': wd.get('avg_hr') or 0,
            'max_hr': 0,
            'kcal': wd.get('kcal') or 0,
        })
    conn.close()
    return out

def generate_fitness_knowledge():
    """Genereeri Markdown fail Claude'i jaoks"""

    # Lae kõik treeningud
    gymaholic = load_workout_history()
    workoutdoor = load_workoutdoor_data()

    # Lisa source märgis Gymaholic'ule
    for w in gymaholic:
        if 'source' not in w:
            w['source'] = 'gymaholic'

    all_workouts = gymaholic + workoutdoor

    # Sorteeri kuupäeva järgi (uusimad enne)
    all_workouts.sort(
        key=lambda x: x.get('timestamp', x.get('date', '')),
        reverse=True
    )

    # Loo Markdown fail
    output = []
    output.append("# Fitness Tracker - Treeningute Ajalugu\n")
    output.append(f"**Viimati uuendatud:** {datetime.now().strftime('%d.%m.%Y %H:%M')}\n")
    output.append("---\n\n")

    # Kasutaja info
    output.append("## Kasutaja Profiil\n")
    output.append("- **Nimi:** Aimar | **Vanus:** 43 a | **Pikkus/Kaal:** 192cm, 103kg\n")
    output.append("- **Resting HR:** 62 bpm | **Max HR:** 179 bpm\n")
    output.append("- **Eesmärk:** Keha vormimine, kaalulangetus (kõhu pealt)\n")
    output.append("- **Treeningstrateegia:** Topeltprogressioon (double progression)\n")
    output.append("- **⚠️ Vigastus:** Vasak jalg nõrgem (vana vigastus) - jälgi asümmeetriat\n")
    output.append("\n### Pulssitsoonid (Karvonen)\n")
    output.append("- **Z1 (121-132):** Taastumine | **Z2 (132-144):** Rasvapõletus ⭐\n")
    output.append("- **Z3 (144-156):** Aeroobne | **Z4 (156-167):** Lävi | **Z5 (167-179):** Max\n")
    output.append("\n### Baseline Kaalud (Algus: Jaanuar 2026)\n")
    output.append("Bench 60kg | Row 55kg | RDL 50kg | Squat 55kg | Curl 30kg\n")
    output.append("\n### Topeltprogressioon\n")
    output.append("Iga harjutus on määratud setide ja korduste vahemikuga (nt 3×6-8). ")
    output.append("Alustan vahemiku alumisest piirist ja tõstan igal treeningul +1 kordus. ")
    output.append("Kui saavutan vahemiku ülemise piiri, tõstan raskust ja alustan uuesti alumisest piirist.\n\n")
    output.append("**Näide:** Bench Press 3×6-8  \n")
    output.append("- Treening 1: 3×6 @ 65kg → Treening 2: 3×7 @ 65kg → Treening 3: 3×8 @ 65kg → Treening 4: 3×6 @ 67.5kg\n")
    output.append("\n---\n\n")

    # Kokkuvõte
    gym_workouts = [w for w in all_workouts if w.get('source') == 'gymaholic']
    cardio_workouts = [w for w in all_workouts if w.get('source') == 'workoutdoor']

    total_gym_volume = sum(w.get('total_volume', 0) for w in gym_workouts)

    # Jaga kardio liikide kaupa
    walking = [w for w in cardio_workouts if w.get('workout_type') == 'walking']
    running = [w for w in cardio_workouts if w.get('workout_type') == 'running']
    cycling = [w for w in cardio_workouts if w.get('workout_type') == 'cycling']
    hiking = [w for w in cardio_workouts if w.get('workout_type') == 'hiking']
    swimming = [w for w in cardio_workouts if w.get('workout_type') == 'swimming']

    output.append("## Kokkuvõte (Kogu ajalugu)\n")
    output.append(f"- **🏋️ Jõusaal:** {len(gym_workouts)} treeningut | {total_gym_volume:.0f} kg kogumaht\n")

    if walking:
        total_walk_km = sum(w.get('distance', 0) for w in walking)
        output.append(f"- **🚶 Kõndimine:** {len(walking)} treeningut | {total_walk_km:.1f} km\n")

    if running:
        total_run_km = sum(w.get('distance', 0) for w in running)
        output.append(f"- **🏃 Jooksmine:** {len(running)} treeningut | {total_run_km:.1f} km\n")

    if cycling:
        total_cycle_km = sum(w.get('distance', 0) for w in cycling)
        output.append(f"- **🚴 Rattasõit:** {len(cycling)} treeningut | {total_cycle_km:.1f} km\n")

    if hiking:
        total_hike_km = sum(w.get('distance', 0) for w in hiking)
        output.append(f"- **⛰️ Matkamine:** {len(hiking)} treeningut | {total_hike_km:.1f} km\n")

    if swimming:
        total_swim_km = sum(w.get('distance', 0) for w in swimming)
        output.append(f"- **🏊 Ujumine:** {len(swimming)} treeningut | {total_swim_km:.1f} km\n")

    output.append(f"\n**KOKKU:** {len(all_workouts)} treeningut\n")
    output.append("\n---\n\n")

    # Treeningute sagedus ja järjepidevus
    if gym_workouts:
        first_workout = min(w.get('timestamp', '') for w in gym_workouts)[:10]
        last_workout = max(w.get('timestamp', '') for w in gym_workouts)[:10]
        first_date = datetime.fromisoformat(first_workout)
        last_date = datetime.fromisoformat(last_workout)
        weeks = (last_date - first_date).days / 7
        workouts_per_week = len(gym_workouts) / weeks if weeks > 0 else 0

        output.append("## 📅 Treeningute Sagedus ja Järjepidevus\n")
        output.append(f"- **Periood:** {first_date.strftime('%d.%m.%Y')} - {last_date.strftime('%d.%m.%Y')}\n")
        output.append(f"- **Kokku:** {len(gym_workouts)} jõutreeningut ({int(weeks)} nädalat)\n")
        output.append(f"- **Keskmine:** {workouts_per_week:.1f} treeningut nädalas\n")
        output.append("- **Rutiin:** Soojendus (5 min sõudmine) + Jõutrenn + Lõpetus (30 min kõndimine)\n")
        output.append("\n---\n\n")

    # Personal Records (PR)
    from collections import defaultdict
    exercise_prs = defaultdict(lambda: {'weight': 0, 'reps': 0, 'sets': 0, 'date': ''})

    for w in gym_workouts:
        for ex in w.get('exercises', []):
            name = ex.get('name', '')
            weight = ex.get('weight_kg', 0)
            reps = ex.get('reps', 0)
            sets = ex.get('sets', 0)
            date = w.get('timestamp', '')[:10]

            # Ignoreeri kehakaalu harjutusi ja soojendusi
            if weight == 0 or 'treadmill' in name.lower() or 'rowing' in name.lower():
                continue

            # Uus PR kui suurem kaal või sama kaal + rohkem kordusi
            if weight > exercise_prs[name]['weight'] or \
               (weight == exercise_prs[name]['weight'] and reps > exercise_prs[name]['reps']):
                exercise_prs[name] = {'weight': weight, 'reps': reps, 'sets': sets, 'date': date}

    # Arvuta baseline'id ja vahemikud
    ranges = load_exercise_ranges()
    baselines = compute_exercise_baselines(gym_workouts)

    if exercise_prs:
        sorted_prs = sorted(exercise_prs.items(), key=lambda x: x[1]['weight'], reverse=True)

        output.append("## 🏆 Personal Records (PR)\n")
        output.append(f"**Viimati uuendatud:** {datetime.now().strftime('%d.%m.%Y')}\n\n")

        # TOP 5
        output.append("### TOP 5 Raskeimad\n")
        for i, (name, pr) in enumerate(sorted_prs[:5], 1):
            pr_date = datetime.fromisoformat(pr['date']).strftime('%d.%m.%Y')
            bl = baselines.get(name)
            progress = ""
            if bl:
                diff = pr['weight'] - bl['weight']
                pct = (diff / bl['weight']) * 100 if bl['weight'] else 0
                if diff > 0:
                    progress = f" | Baseline: {bl['weight']:.1f}kg → **+{diff:.1f}kg ({pct:.0f}%)** 🔥"
                elif diff == 0:
                    progress = f" | Baseline: {bl['weight']:.1f}kg (sama)"

            output.append(f"{i}. **{name}:** {pr['sets']}×{pr['reps']} @ {pr['weight']:.1f}kg ({pr_date}){progress}\n")

        # Muud PR-id (ülejäänud)
        if len(sorted_prs) > 5:
            output.append("\n### Muud PR-id\n")
            for name, pr in sorted_prs[5:]:
                output.append(f"- **{name}:** {pr['sets']}×{pr['reps']} @ {pr['weight']:.1f}kg\n")

        output.append("\n---\n\n")

    # --- Harjutuste Baseline ja Progress (KÕIK harjutused) ---
    if baselines:
        output.append("## 📊 Harjutuste Baseline ja Progress\n")
        output.append("Baseline = esimene salvestatud töökaal ajaloos. Vahemik = topeltprogressiooni siht.\n\n")
        output.append("| Harjutus | Vahemik | Baseline | Praegune | Progress | Staatus |\n")
        output.append("|----------|---------|----------|----------|----------|--------|\n")

        current = compute_current_top(gym_workouts)
        # Sorteeri progressi järgi (kasv %)
        rows = []
        for name, bl in baselines.items():
            cur = current.get(name, bl)
            r = ranges.get(name, {})
            range_str = ""
            status = ""
            if r:
                if r['reps_min'] == r['reps_max'] == 0:
                    range_str = f"{r['sets']}×max"
                elif r['reps_min'] == r['reps_max']:
                    range_str = f"{r['sets']}×{r['reps_min']}"
                else:
                    range_str = f"{r['sets']}×{r['reps_min']}-{r['reps_max']}"
                    # Staatus: kas vahemiku ülemine piir täis?
                    if cur['reps'] >= r['reps_max'] and cur['sets'] >= r['sets']:
                        status = "⬆️ tõsta raskust"
                    elif cur['reps'] <= r['reps_min']:
                        status = "🔽 alumine piir"
                    else:
                        status = "📈 progressis"
            diff = cur['weight'] - bl['weight']
            pct = (diff / bl['weight']) * 100 if bl['weight'] else 0
            progress_str = f"+{diff:.1f}kg ({pct:.0f}%)" if diff > 0 else ("sama" if diff == 0 else f"{diff:.1f}kg")
            rows.append((pct, name, range_str, bl, cur, progress_str, status))

        rows.sort(key=lambda x: x[0], reverse=True)
        for _, name, range_str, bl, cur, progress_str, status in rows:
            output.append(
                f"| {name} | {range_str} | {bl['weight']:.1f}kg ({bl['date']}) | "
                f"{cur['sets']}×{cur['reps']}@{cur['weight']:.1f}kg | {progress_str} | {status} |\n"
            )
        output.append("\n---\n\n")

    # --- Trendid: viimased 4 nädalat vs eelmised 4 nädalat ---
    now = datetime.now()
    cutoff_recent = now - timedelta(days=28)
    cutoff_prev = now - timedelta(days=56)

    def in_range(w, start, end):
        try:
            d = datetime.fromisoformat(w.get('timestamp', w.get('date', ''))[:19].replace(' ', 'T'))
            return start <= d < end
        except Exception:
            return False

    recent_gym = [w for w in gym_workouts if in_range(w, cutoff_recent, now)]
    prev_gym = [w for w in gym_workouts if in_range(w, cutoff_prev, cutoff_recent)]
    recent_cardio = [w for w in cardio_workouts if in_range(w, cutoff_recent, now)]
    prev_cardio = [w for w in cardio_workouts if in_range(w, cutoff_prev, cutoff_recent)]

    def vol(ws): return sum(w.get('total_volume', 0) for w in ws)
    def avg_hr(ws):
        hrs = [w.get('avg_hr', 0) for w in ws if w.get('avg_hr', 0) > 0]
        return sum(hrs) / len(hrs) if hrs else 0

    output.append("## 📈 Trendid (viimased 4 näd vs eelmised 4 näd)\n\n")
    output.append("| Näitaja | Eelmised 4 näd | Viimased 4 näd | Muutus |\n")
    output.append("|---------|----------------|----------------|--------|\n")

    def row(label, a, b, unit='', fmt='.0f'):
        if a == 0 and b == 0:
            return
        diff = b - a
        pct = (diff / a * 100) if a else 0
        arrow = '📈' if diff > 0 else ('📉' if diff < 0 else '➡️')
        diff_str = f"{arrow} {diff:+{fmt}}{unit} ({pct:+.0f}%)" if a else "uus"
        output.append(f"| {label} | {a:{fmt}}{unit} | {b:{fmt}}{unit} | {diff_str} |\n")

    row("Jõutreeninguid", len(prev_gym), len(recent_gym))
    row("Jõu kogumaht", vol(prev_gym), vol(recent_gym), 'kg')
    row("Kardio treeninguid", len(prev_cardio), len(recent_cardio))
    row("Kardio keskmine HR", avg_hr(prev_cardio), avg_hr(recent_cardio), ' bpm')
    row("Kardio distants", sum(w.get('distance', 0) for w in prev_cardio),
        sum(w.get('distance', 0) for w in recent_cardio), ' km', '.1f')
    output.append("\n---\n\n")

    # --- Kardio HR-tsoonide kokkuvõte (Z2 aeg = rasvapõletus) ---
    if cardio_workouts:
        zone_totals = {'z1': 0, 'z2': 0, 'z3': 0, 'z4': 0, 'z5': 0}
        zone_totals_recent = {'z1': 0, 'z2': 0, 'z3': 0, 'z4': 0, 'z5': 0}
        for w in cardio_workouts:
            zones = w.get('hr_zones', {}) or {}
            for z, m in zones.items():
                if z in zone_totals:
                    zone_totals[z] += m
                    if in_range(w, cutoff_recent, now):
                        zone_totals_recent[z] += m

        total_all = sum(zone_totals.values())
        if total_all > 0:
            output.append("## ❤️ Kardio HR-tsoonide Jaotus\n")
            output.append("**Z2 (132-144) on kaalulangetuse kuldtsoon.**\n\n")
            output.append("| Tsoon | Kogu aeg | % | Viimased 4 näd |\n")
            output.append("|-------|----------|---|----------------|\n")
            labels = {'z1': 'Z1 taastumine', 'z2': 'Z2 rasvapõletus ⭐',
                      'z3': 'Z3 aeroobne', 'z4': 'Z4 lävi', 'z5': 'Z5 max'}
            for z in ['z1', 'z2', 'z3', 'z4', 'z5']:
                m = zone_totals[z]
                pct = (m / total_all * 100) if total_all else 0
                m_r = zone_totals_recent[z]
                output.append(f"| {labels[z]} | {m:.0f} min | {pct:.0f}% | {m_r:.0f} min |\n")
            output.append("\n---\n\n")

    # --- Vigastuse jälgimine: asümmeetria (vasak jalg) ---
    asymmetry_exercises = ['Single-Leg Press', 'Lunge', 'One-Arm Dumbbell Row']
    asym_data = []
    for name in asymmetry_exercises:
        occurrences = []
        for w in sorted(gym_workouts, key=lambda x: x.get('timestamp', '')):
            for ex in w.get('exercises', []):
                if ex.get('name') == name and ex.get('weight_kg', 0) > 0:
                    occurrences.append({
                        'date': w.get('timestamp', '')[:10],
                        'sets': ex.get('sets', 0),
                        'reps': ex.get('reps', 0),
                        'weight': ex.get('weight_kg', 0),
                    })
        if occurrences:
            asym_data.append((name, occurrences))

    if asym_data:
        output.append("## ⚠️ Ühepoolsete Harjutuste Jälgimine (vasak jalg nõrgem)\n")
        output.append("Aimari vasak jalg on vana vigastuse tõttu nõrgem. Jälgi, et ühepoolsed harjutused saaksid sama hoogu kui kahepoolsed.\n\n")
        for name, occs in asym_data:
            first = occs[0]
            last = occs[-1]
            diff = last['weight'] - first['weight']
            pct = (diff / first['weight'] * 100) if first['weight'] else 0
            output.append(f"- **{name}:** {first['sets']}×{first['reps']}@{first['weight']}kg ({first['date']}) → "
                          f"{last['sets']}×{last['reps']}@{last['weight']}kg ({last['date']}) | "
                          f"**{'+' if diff >= 0 else ''}{diff:.1f}kg ({pct:+.0f}%)** | Korduste arv: {len(occs)}\n")
        output.append("\n---\n\n")

    # Kõik treeningud detailselt (kõik aastad)
    output.append(f"## Treeningute Ajalugu ({len(all_workouts)} treeningut)\n\n")

    for w in all_workouts:  # KÕIK treeningud
        try:
            date_str = datetime.fromisoformat(w.get('timestamp', w.get('date', '')).replace(' ', 'T')).strftime('%d.%m.%Y')
        except (ValueError, TypeError):
            date_str = w.get('date', '')[:10]

        name = w.get('workout_name', 'Treening')
        workout_type = w.get('workout_type', 'N/A')
        duration = w.get('duration_min', w.get('duration', 0) // 60)
        hr = w.get('avg_hr', 0)

        # Vali emoji vastavalt tüübile
        if w.get('source') == 'gymaholic':
            emoji = "🏋️"
        else:
            activity_type = w.get('workout_type', 'cardio')
            emoji_map = {
                'walking': '🚶',
                'running': '🏃',
                'cycling': '🚴',
                'hiking': '⛰️',
                'swimming': '🏊'
            }
            emoji = emoji_map.get(activity_type, '🏃')

        output.append(f"### {emoji} {date_str} - {name}\n")
        output.append(f"- **Tüüp:** {workout_type}\n")
        output.append(f"- **Kestus:** {duration} min\n")
        output.append(f"- **Keskmine HR:** {hr} bpm\n")

        if w.get('max_hr'):
            output.append(f"- **Max HR:** {w.get('max_hr')} bpm\n")

        if w.get('kcal'):
            output.append(f"- **Kalorid:** {w.get('kcal')} kcal\n")

        if w.get('distance', 0) > 0:
            output.append(f"- **Distants:** {w.get('distance'):.2f} km\n")

        if w.get('total_volume', 0) > 0:
            output.append(f"- **Kogumaht:** {w.get('total_volume'):.0f} kg\n")

        # Workoutdoor spetsiifilised
        if w.get('steps', 0) > 0:
            output.append(f"- **Samme:** {w.get('steps')}\n")

        # HR tsoonid (Workoutdoor)
        if w.get('hr_zones'):
            zones = w.get('hr_zones')
            total_zone_time = sum(zones.values())
            if total_zone_time > 0:
                output.append("- **HR tsoonid:**\n")
                for zone, minutes in zones.items():
                    if minutes > 0:
                        percentage = (minutes / total_zone_time) * 100
                        output.append(f"  - {zone.upper()}: {minutes:.1f} min ({percentage:.0f}%)\n")

        # Lisa harjutuste detailid (Gymaholic)
        if w.get('exercises') and len(w.get('exercises', [])) > 0:
            output.append("\n**Harjutused:**\n")
            for ex in w.get('exercises', []):
                ex_name = ex.get('name', 'Harjutus')
                sets = ex.get('sets', 0)
                reps = ex.get('reps', 0)
                weight = ex.get('weight_kg', 0)
                volume = ex.get('total_volume', 0)
                duration_sec = ex.get('duration_sec', 0)
                max_hr = ex.get('max_hr', 0)
                avg_hr = ex.get('avg_hr', 0)
                kcal = ex.get('kcal', 0)

                # Põhiinfo
                if volume > 0:
                    line = f"- {ex_name}: {sets} setti × {reps} kordust @ {weight}kg = **{volume:.0f}kg maht**"
                else:
                    line = f"- {ex_name}: {sets} setti × {reps} kordust"

                # Lisa kestus kui olemas
                if duration_sec > 0:
                    if duration_sec >= 60:
                        line += f" | {duration_sec/60:.1f} min"
                    else:
                        line += f" | {duration_sec:.0f}s"

                # Lisa HR kui olemas
                if avg_hr > 0:
                    line += f" | HR: {avg_hr}"
                    if max_hr > 0:
                        line += f"-{max_hr}"
                    line += " bpm"

                # Lisa kalorid kui olemas
                if kcal > 0:
                    line += f" | {kcal} kcal"

                output.append(line + "\n")

        # Märkmed (Gymaholic)
        if w.get('notes') and w.get('notes').strip():
            output.append(f"\n**Märkmed:** {w.get('notes')}\n")

        output.append("\n")

    # Salvesta fail
    OUTPUT_DIR.mkdir(exist_ok=True)
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        f.write(''.join(output))

    print(f"✅ Genereeritud: {OUTPUT_FILE}")
    print(f"📊 Kokku: {len(all_workouts)} treeningut")
    print(f"📄 Faili suurus: {OUTPUT_FILE.stat().st_size / 1024:.1f} KB")

    return OUTPUT_FILE

if __name__ == '__main__':
    generate_fitness_knowledge()
