# gmx_mail

Home-Assistant-Custom-Integration für ein GMX-Postfach (IMAP/SMTP), gedacht für die Verwaltung des Postfachs
durch Claude über den ha-mcp-Server. **Status: Planungsphase – noch kein Code.**

| Datei | Inhalt |
|---|---|
| `CLAUDE.md` | Startinformationen und verbindliche Arbeitsregeln für Claude Code |
| `SPEC.md` | Architektur, Aktionsspezifikation, Sicherheit, Tests, Meilensteine, Risiken |
| `GMX-NOTES.md` | GMX-Anforderungen mit Quellen und Verlässlichkeit, Live-Testplan, Ergebnisfeld |
| `docs/DECISIONS.md` | Getroffene Entscheidungen |
| `docs/SOURCES.md` | Quellenverzeichnis der Recherche |
| `docs/START-PROMPT.md` | Prompt zum Start der Arbeit in Claude Code |
| `docs/probe-2026-11-20*.json` | Bereinigte Ergebnisse der M0-Live-Tests (Lauf 1–3) |
| `tools/gmx_probe.py` | Live-Prüfskript gegen ein GMX-Testkonto (gibt keine Mailinhalte aus) |
| `custom_components/gmx_mail/` | Platz für die Integration (noch leer) |
| `tests/` | Tests; bisher nur Hilfsfunktionen des Probe-Skripts (`pytest -q tests`) |

Reihenfolge: zuerst `tools/gmx_probe.py` gegen ein Testkonto laufen lassen (siehe `GMX-NOTES.md`, Abschnitt 3),
Ergebnisse eintragen, dann mit Meilenstein M1 beginnen.
