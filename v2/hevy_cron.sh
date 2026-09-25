#!/bin/bash
# Hevy -> trenn.db sünk cronist (iga 15 min, mitte täisminutil — Hevy palve).
# Lukk: aeglane API ei tohi lasta kahel sünkil kattuda. Logi ainult muutuste/vigade korral.
cd "$(dirname "$0")/.." || exit 1
exec flock -n data/.hevy_sync.lock venv/bin/python v2/hevy_sync.py sync --quiet >> logs/hevy_sync.log 2>&1
