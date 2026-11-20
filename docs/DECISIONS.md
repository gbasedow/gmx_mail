# DECISIONS.md – Entscheidungen

| Nr. | Datum | Entscheidung | Begründung |
|---|---|---|---|
| 1 | 2026-11-20 | Normale Home-Assistant-Integration mit Aktionen mit Antwortdaten, **kein** eigener MCP-Server. | ha-mcp reicht Aktionen über `ha_call_service` (`return_response=True`) an Claude durch (in der Instanz geprüft). Ein eigener MCP-Server bräuchte öffentlich erreichbaren Zugang mit OAuth, eigene Absicherung und Wartung. |
| 2 | 2026-11-20 | Eigenentwicklung statt Warten auf Core-`imap.search`. | PR #181749 wurde vom Code-Owner abgelehnt; keine Suchaktion in 2026.11. |
| 3 | 2026-11-20 | Umfang „vollständiges Management“ = alles, was IMAP und SMTP hergeben. Filterregeln nicht in v1. | Keine GMX-API, kein ManageSieve gefunden. |
| 4 | 2026-11-20 | Rechtestufen R/O/D/S, Voreinstellung nur R. | Begrenzt den Schaden durch Prompt-Injection. |
| 5 | 2026-11-20 | Versand-Modus konfigurierbar: `off` (Voreinstellung) / `allowlist` / `any`. Antwort an Absender ohne Allowlist-Eintrag nur mit eigener Option (Voreinstellung aus). | Allowlist ist die einzige harte Grenze gegen Datenabfluss; ein Token garantiert keine menschliche Freigabe. GMX-Versandlimits sind unveröffentlicht. |
| 6 | 2026-11-20 | Löschen = Papierkorb; endgültiges Löschen nur mit Option + Token + UID EXPUNGE. | Schutz vor Massen- und Fehllöschung. |
| 7 | 2026-11-20 | Core-`smtp.send_message` wird nicht genutzt; eigener SMTP-Versand. | Nur vordefinierte Empfänger, kein CC/BCC, keine Anhänge aus beliebigen Pfaden. |
| 8 | 2026-11-20 | Übergabe an Claude Code über Dateien (`CLAUDE.md`, `SPEC.md`, `GMX-NOTES.md`) statt eines langen Prompts. | Claude Code liest `CLAUDE.md` automatisch; Dateien sind versionierbar. |
| 9 | 2026-11-20 | Menschliche Freigabe über ha-mcp „Tool Security Policies (user approval)“; ha-mcp-Read-Only-Modus nicht verwenden. | Read-Only-Modus deaktiviert `ha_call_service` komplett. |
| 10 | 2026-11-20 | M0-Ergebnisse in SPEC.md übernommen: `NOOP` nach > 5 min Leerlauf, ein Reconnect mit Wiederholung nur für lesende Aktionen, IDLE-Erneuerung Default 24 min, nie `CLOSE` auf Read-Write-Mailboxen, COPYUID getaggt oder ungetaggt, Größenprüfung gegen `APPENDLIMIT`, Suchfallback für alle Textkriterien. | GMX trennt ruhende Verbindungen nach ≈ 30 min; Index ≈ 30 s verzögert (auch SUBJECT); Probe-Review fand implizites EXPUNGE durch `CLOSE`. Vom Nutzer freigegeben. |

- `sent_copy_mode` Standard `append`: GMX legt per SMTP keine Gesendet-Kopie an (M0, Lauf 3).

## Offen

- Heartbeat gegen die automatische IMAP-Abschaltung – nach M0 bewerten.
- Betrieb: Integration zuerst auf einer Staging-Instanz; Produktivfreigabe durch den Nutzer.
