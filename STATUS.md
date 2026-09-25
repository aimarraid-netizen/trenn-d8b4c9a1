# STATUS — trenn

> Pooleli töö ja järgmised sammud. Püsivad faktid ja otsused: `CLAUDE.md`.
> Uuendatud: 25.09.2026

## Seis

Hevy integratsioon on ehitatud ja päris API vastu kontrollitud (137 testi läbivad). Hevys oli 25.09 veel 0 trenni.

| Tehtud 25.09.2026 | |
|---|---|
| `HEVY_API_KEY` `.env`-is | `hevy_sync.py check` → OK |
| `TRENN_DISCORD_WEBHOOK` `.env`-is (OttBot) | testsõnum + näidissõnum kohale jõudnud |
| Harjutuste vasted `exercise_config.HEVY_TEMPLATES` | 30 harjutust, kontrollitud Hevy 451 template'i vastu |
| Hevy rutiinid "Trenn A — N1", "Trenn B — N1" | loodud `hevy_routines.py --push`, tagasi loetud |
| OttBoti persona `data/ottbot.md` | Aimar kinnitas (järgmise korra kaalud JAH, röst ainult trenni kohta, kuni 6 rida) |
| `hevy_cron.sh` | olemas ja käsitsi proovitud; **cron veel POLE sees** |

## Järgmised sammud

1. **26.09 pärast Trenn B-d (Aimar kirjutab "sünk"):**
   `cp data/trenn.db data/trenn.db.bak-$(date +%F)` → `venv/bin/python v2/hevy_sync.py sync --dry-run` →
   kontrolli harjutusnimed/kaalud/soojendused → `sync` → vaata HTML-i.
2. Kui sünk korras → cron sisse (`crontab -l > fail; lisa rida; crontab - < fail`):
   `7,22,37,52 * * * * /home/aimar/projects/trenn/v2/hevy_cron.sh`
3. **Automaatne progressioon + OttBot** (üks ahel):
   - analüüsi comeback-režiim (vt CLAUDE.md "Teadaolev viga"): võrdlus pausieelse sessiooniga, kava täitmine
   - järgmise korra kaalud topeltprogressiooniga → Hevy rutiini PUT (`hevy_api.update_routine`)
   - OttBoti sõnum: kood arvutab numbrid → Claude API sõnastab persona järgi → mallisõnum varuks
   - test: 23.09 Trenn A käsitsi näidis (Discordis "(näidis)") on võrdlusalus
4. "3 trenni nädalas" valvur (3/näd algab 05.10.2026).
