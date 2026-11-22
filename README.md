# GMX Mail für Home Assistant

<img src="custom_components/gmx_mail/brand/icon.png" alt="Logo" width="96" align="right">

Eine Home-Assistant-Integration, die ein **GMX-Postfach** über IMAP und SMTP anbindet. Sie ist dafür gebaut, dass ein
KI-Assistent wie **Claude** das Postfach über den [ha-mcp](https://github.com/homeassistant-ai/ha-mcp)-Server verwalten kann:
Mails suchen und lesen, sortieren, löschen und – wenn ausdrücklich erlaubt – senden. Alles in kontrollierten Stufen, mit
Bestätigungscodes für riskante Schritte und einem Protokoll ohne Mailinhalte.

> **Kein offizielles GMX-Produkt.** GMX ist eine Marke der 1&1 Mail & Media GmbH; dieses Projekt steht in keiner Verbindung dazu.

## Was die Integration kann

| Stufe | Aktionen | Ab Werk |
|---|---|---|
| **Lesen** | Ordner auflisten, suchen, Nachrichten und Anhänge abrufen, Status, Protokoll | an |
| **Organisieren** | Markierungen, verschieben, kopieren, Spam/kein Spam, Ordner anlegen und umbenennen, Entwürfe speichern | aus |
| **Löschen** | in den Papierkorb, endgültig löschen (zusätzliche Option, nur aus Papierkorb/Spam), leere Ordner löschen | aus |
| **Senden** | senden, antworten, weiterleiten, Entwurf senden – nur an erlaubte Empfänger | aus |

Dazu: Sensoren (ungelesene Mails, Spam, letzte Synchronisation, Aktionen heute, Verbindung), sofortige Benachrichtigung bei neuen
Mails (IMAP IDLE), Ereignisse für Automationen, Diagnose und Reparatur-Hinweise.

**Sicherheit in Kürze:** Mailinhalte werden bereinigt und als „nicht vertrauenswürdig“ markiert übergeben (versteckter Text,
unsichtbare Zeichen und Tracking-Links werden entfernt). Endgültiges Löschen und jeder Versand brauchen eine Vorschau und einen
Bestätigungscode. Gesendet wird nur an Adressen auf deiner **Allowlist**. Details: [Sicherheit](docs/SICHERHEIT.md).

## Voraussetzungen

- Home Assistant **2026.11** oder neuer (Logo ab 2026.3).
- Ein GMX-Konto mit eingeschaltetem **POP3/IMAP-Zugriff** und einem **App-Passwort** – siehe [Anleitung, Schritt 1](docs/ANLEITUNG.md#1-gmx-vorbereiten).
- Für die Nutzung mit Claude: der ha-mcp-Server.

## Installation über HACS

1. HACS öffnen → oben rechts **⋮** → **Benutzerdefinierte Repositories**.
2. Repository `https://github.com/gbasedow/gmx_mail`, Typ **Integration**, hinzufügen.
3. **GMX Mail** suchen und herunterladen. Für Vorabversionen (z. B. 0.9.0) in HACS bei der Integration „Beta-Versionen anzeigen“ einschalten.
4. Home Assistant neu starten.
5. **Einstellungen → Geräte & Dienste → Integration hinzufügen → GMX Mail**.

Die ausführliche Anleitung mit allen Optionen, Aktionen und der Fehlerbehebung steht in **[docs/ANLEITUNG.md](docs/ANLEITUNG.md)**.

## Weitere Dokumente

| Dokument | Inhalt |
|---|---|
| [docs/ANLEITUNG.md](docs/ANLEITUNG.md) | Einrichtung, Optionen, Aktionen, Sensoren, Ereignisse, Fehlerbehebung |
| [docs/SICHERHEIT.md](docs/SICHERHEIT.md) | Was die Schutzmechanismen leisten – und was nicht |
| [docs/HA-MCP-KONFIGURATION.md](docs/HA-MCP-KONFIGURATION.md) | ha-mcp so einrichten, dass Versand und Löschen deine Freigabe brauchen |
| [docs/CLAUDE-REGELN.md](docs/CLAUDE-REGELN.md) | Verhaltensregeln für Claude zum Einfügen in die Projektanweisungen |
| [CHANGELOG.md](CHANGELOG.md) | Änderungen je Version |

## Für Entwickler

| Datei | Inhalt |
|---|---|
| `CLAUDE.md` | Startinformationen und Arbeitsregeln für Claude Code |
| `SPEC.md` | Architektur, Aktionsspezifikation, Sicherheit, Tests, Meilensteine, Risiken |
| `GMX-NOTES.md` | GMX-Eigenheiten mit Quellen und Live-Messungen |
| `docs/DECISIONS.md` | Getroffene Entscheidungen |
| `tools/gmx_probe.py`, `tools/gmx_live_check.py` | Manuelle Prüfungen gegen ein GMX-Testkonto (nur Metadaten in der Ausgabe) |
| `tests/` | Tests: Integration (`pytest -q`), Testserver (`-m integration`), echtes Home Assistant und ha-mcp (`-m staging`) |
| `.github/workflows/` | Tests, ruff, mypy, hassfest, HACS-Validierung |

Lizenz: MIT (siehe `LICENSE`).
