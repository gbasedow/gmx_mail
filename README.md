# gmx_mail

Home-Assistant-Custom-Integration für ein GMX-Postfach (IMAP/SMTP), gedacht für die Verwaltung des Postfachs
durch Claude über den ha-mcp-Server. **Status: Meilenstein M1 (Gerüst, Flows, Verbindungsverwaltung) – noch keine Aktionen.**

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
| `custom_components/gmx_mail/` | Die Integration: Config-/Options-/Reauth-/Reconfigure-Flow, `client/` (IMAP-Worker, Ordnererkennung, SMTP-Check) |
| `tests/gmx_mail/` | Tests der Integration mit `pytest-homeassistant-custom-component` (`pytest -q`) |
| `tests/probe/` | Tests des Probe-Skripts gegen einen Fake-IMAP-Server (`pytest -q -p no:homeassistant tests/probe`) |
| `pyproject.toml`, `requirements_dev.txt` | Konfiguration und Abhängigkeiten für pytest, ruff, mypy (Linux, Python 3.14) |
| `.github/workflows/` | Tests, ruff, mypy, hassfest, HACS-Validierung |

Installation zum Testen (nur Staging): Ordner `custom_components/gmx_mail` nach `config/custom_components/` kopieren, Home Assistant
neu starten, Integration „GMX Mail“ hinzufügen. Vorher im GMX-Webmailer POP3/IMAP einschalten und ein App-Passwort anlegen.
