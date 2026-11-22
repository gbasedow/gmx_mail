# Änderungen

Alle nennenswerten Änderungen je Version. Format angelehnt an „Keep a Changelog“, Versionen nach SemVer.

## 0.9.0 – 22.11.2026 (Vorabversion)

Erste veröffentlichte Version. Enthält alle Meilensteine M1–M7.

### Neu
- **Logo** (Briefumschlag in GMX-Blau), sichtbar in Home Assistant ab 2026.3.
- **Deutsche Anleitungen:** [Anleitung](docs/ANLEITUNG.md), [Sicherheit](docs/SICHERHEIT.md), [ha-mcp absichern](docs/HA-MCP-KONFIGURATION.md),
  [Regeln für Claude](docs/CLAUDE-REGELN.md).

### Verbessert
- **Erkennung versteckter Inhalte erweitert:** Outlook-Verstecken (`mso-hide`), winzige Schrift, transparente oder
  hintergrundfarbene Schrift, Höhe 0 mit verstecktem Überlauf, Verschieben aus dem sichtbaren Bereich, Einrückung,
  Beschneiden und `aria-hidden`.
- **Betreff** muss einzeilig sein (klare Fehlermeldung statt „ungültige Adresse“).

### Behoben
- Ungewöhnliche Adressen wie `name@domain.` führten zu einem internen Fehler statt „ungültige Adresse“ (gefunden durch Zufallstests).
- Eine leere oder kaputte Mailstruktur vom Server konnte den Mail-Zerleger abstürzen lassen (gefunden durch Zufallstests).

### Geprüft
- Sammlung von Angriffsmustern (versteckte Anweisungen, unsichtbare Zeichen, getarnte Links) als feste Tests.
- Zufallstests (Hypothesis) für Mail-Zerleger, Bereinigung, Adressprüfung und die 24-KB-Antwortgrenze.
- Ende-zu-Ende über einen echten ha-mcp-Server; jede Aktion im Test unter 20 Sekunden.

## 0.6.0 – 22.11.2026 (M6, nicht veröffentlicht)
- Senden, Antworten, Weiterleiten, Entwurf senden – mit Versand-Modus, Allowlist, Vorschau und Bestätigungscode.
- Gesendet-Kopie (GMX legt keine an), Grenzen je Mail, Stunde, Tag und Größe, Absendername.

## 0.5.0 – 21.11.2026 (M5, nicht veröffentlicht)
- Papierkorb, endgültiges Löschen (nur aus Papierkorb/Spam, mit Bestätigungscode), leere Ordner löschen, Tageslimit.

## 0.4.0 – 21.11.2026 (M4, nicht veröffentlicht)
- Markierungen, Verschieben, Kopieren, Spam, Ordner anlegen/umbenennen, Entwürfe, Audit-Protokoll, Aufräumen gespeicherter Anhänge.

## 0.3.0 und früher – 20./21.11.2026 (M1–M3, nicht veröffentlicht)
- Einrichtung, Lesen und Suchen, bereinigte Mailinhalte, Push (IMAP IDLE), Sensoren, Ereignisse, Diagnose, Reparatur-Hinweise.
