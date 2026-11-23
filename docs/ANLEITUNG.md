# Anleitung: GMX Mail für Home Assistant

Diese Anleitung führt durch Einrichtung und Betrieb. Sie richtet sich an Anwender, nicht an Entwickler.

**Inhalt**
1. [GMX vorbereiten](#1-gmx-vorbereiten)
2. [Installieren und einrichten](#2-installieren-und-einrichten)
3. [Optionen](#3-optionen)
4. [Sensoren und Ereignisse](#4-sensoren-und-ereignisse)
5. [Aktionen](#5-aktionen)
6. [Zwei-Schritt-Bestätigung](#6-zwei-schritt-bestätigung)
7. [Anhänge senden und speichern](#7-anhänge-senden-und-speichern)
8. [Hinweise unter „Reparaturen“](#8-hinweise-unter-reparaturen)
9. [Fehlerbehebung](#9-fehlerbehebung)
10. [Datenschutz](#10-datenschutz)
11. [Grenzen](#11-grenzen)

---

## 1. GMX vorbereiten

1. **POP3/IMAP einschalten:** Im GMX-Webmailer unter **E-Mail → Einstellungen → POP3 & IMAP** den Zugriff erlauben und
   speichern. GMX verlangt dabei eine Sicherheitsbestätigung (zum Beispiel einen Code per SMS). Ohne diese Bestätigung bleibt
   der Zugriff aus.
2. **App-Passwort anlegen:** Unter **Account → Login & Sicherheit → App-Passwörter** ein Passwort nur für Home Assistant
   anlegen. Bei eingeschalteter Zwei-Faktor-Anmeldung ist das Pflicht, sonst dringend empfohlen. Das App-Passwort wird nur
   einmal angezeigt – direkt in Home Assistant eintragen.
3. **Benutzername** ist die vollständige GMX-Adresse. Ein Tippfehler darin führt zur selben Fehlermeldung wie ein falsches Passwort.

> **Wichtig:** GMX schaltet POP3/IMAP nach „längerer“ Nichtnutzung automatisch wieder ab (eine Frist nennt GMX nicht). Die
> Integration meldet sich regelmäßig an; ob das GMX als Nutzung zählt, ist noch nicht belegt. Siehe [Reparaturen](#8-hinweise-unter-reparaturen).

## 2. Installieren und einrichten

1. **HACS → ⋮ → Benutzerdefinierte Repositories**, `https://github.com/gbasedow/gmx_mail` als **Integration** hinzufügen.
2. **GMX Mail** herunterladen. Vorabversionen erscheinen nur mit „Beta-Versionen anzeigen“.
3. Home Assistant neu starten.
4. **Einstellungen → Geräte & Dienste → Integration hinzufügen → GMX Mail**, E-Mail-Adresse und App-Passwort eingeben.
   Die Servereinstellungen (`imap.gmx.net:993` mit SSL, `mail.gmx.net:587` mit STARTTLS) sind voreingestellt und müssen
   normalerweise nicht geändert werden.

Die Integration prüft dabei die IMAP- und die SMTP-Anmeldung und erkennt die Spezialordner (Gesendet, Entwürfe, Gelöscht,
Spamverdacht) automatisch. Ab Werk ist nur **Lesen** erlaubt.

Mehrere GMX-Konten sind möglich; jedes wird als eigener Eintrag hinzugefügt. Alle Aktionen brauchen deshalb die
Angabe des Kontos (`config_entry_id`).

## 3. Optionen

Unter **Einstellungen → Geräte & Dienste → GMX Mail → Konfigurieren**. Änderungen gelten sofort; die Integration lädt dabei kurz neu.

### Rechte

| Option | Voreinstellung | Bedeutung |
|---|---|---|
| Erlaubte Aktionsstufen | nur Lesen | **Lesen** ist immer nötig. **Organisieren**: Markierungen, Verschieben, Ordner, Entwürfe. **Löschen**: Papierkorb, leere Ordner löschen. **Senden**: Senden, Antworten, Weiterleiten. |
| Endgültiges Löschen erlauben | aus | Endgültig löschen – nur aus Papierkorb oder Spam-Ordner, immer mit Bestätigungscode. Braucht zusätzlich die Stufe Löschen. |
| HTML-Ausgabe erlauben | aus | Nachrichten auch als bereinigtes HTML abrufbar (mit vollständigen Linkadressen). Aus: nur Text, Links nur als Domain. |

### Versand

| Option | Voreinstellung | Bedeutung |
|---|---|---|
| Versand-Modus | Aus | **Aus**: kein Versand (Entwürfe gehen trotzdem). **Nur Allowlist**: nur an eingetragene Empfänger. **Beliebige Empfänger**: nur, wenn zusätzlich `*` in der Liste steht. |
| Erlaubte Empfänger | leer | Exakte Adressen (`anna@example.org`) oder ganze Domains (`*@firma.de`, ohne Unterdomains). Leere Liste sperrt den Versand. |
| Antworten an den ursprünglichen Absender erlauben | aus | Antworten an den **Absender** einer Mail auch ohne Allowlist-Eintrag. Gilt nicht für eine abweichende Antwortadresse (Reply-To) und nicht für weitere Empfänger bei „Allen antworten“. |
| Absendername | leer | Name neben der Absenderadresse. Die Adresse selbst ist immer die Kontoadresse. |
| Kopie im Ordner Gesendet | Immer eine Kopie ablegen | GMX legt beim Versand über SMTP keine Kopie an; die Integration legt sie selbst ab. **Nur ablegen, wenn sie fehlt** prüft vorher; **Keine Kopie** schaltet das ab. |

### Grenzen

Alle Werte sind frei wählbar (mindestens 1). GMX veröffentlicht seine Versandgrenzen nicht; zu hohe Werte können zu einer
vorübergehenden Sperre des Kontos führen.

| Option | Voreinstellung |
|---|---|
| Höchstzahl Empfänger je Nachricht | 10 |
| Maximale Empfänger pro Stunde | 10 (laut einer unbestätigten Quelle erlaubt GMX Mailprogrammen etwa 11) |
| Höchstzahl gesendeter Nachrichten pro Tag | 20 |
| Maximale Nachrichtengröße | 20 MB (GMX FreeMail; ProMail 50 MB, TopMail 100 MB) |
| Höchstzahl Löschungen pro Tag | 200 (Papierkorb **und** endgültiges Löschen zählen) |

Fest eingebaut: höchstens 100 Nachrichten je Organisieren-Aufruf, 50 je Lösch-Aufruf.

### Verbindung

| Option | Voreinstellung | Bedeutung |
|---|---|---|
| Abfrageintervall | 300 s | Wie oft die Zähler auch ohne Push aktualisiert werden. |
| IMAP IDLE (Push) verwenden | an | Neue Mails werden innerhalb von Sekunden gemeldet (gemessen: etwa 10 s). |
| IDLE erneuern nach | 24 min | GMX trennt ruhende Verbindungen nach etwa 30 Minuten; höchstens 25 Minuten einstellen. |

### Spezialordner

Nur nötig, wenn die automatische Erkennung danebenliegt (zum Beispiel ein alter Ordner `Sent` neben `Gesendet`). Leer lassen = automatisch.

### Datenschutz und Audit

| Option | Voreinstellung | Bedeutung |
|---|---|---|
| Absender und Betreff in Ereignisse aufnehmen | aus | Ereignisdaten landen im Logbuch und können einen KI-Assistenten erreichen. |
| Audit-Protokoll aufbewahren für | 90 Tage | Protokoll aller schreibenden Aktionen, ohne Inhalte. |
| Gespeicherte Anhänge löschen nach | 7 Tage | 0 = behalten. Gilt nur für den eigenen Ordner der Integration. |

## 4. Sensoren und Ereignisse

Je Konto entsteht ein Gerät mit diesen Entitäten:

| Entität | Inhalt |
|---|---|
| Ungelesen im Posteingang | Anzahl ungelesener Mails |
| Spam-Nachrichten | Anzahl Mails im Spam-Ordner |
| Letzte Synchronisation | Zeitpunkt der letzten erfolgreichen Abfrage |
| Aktionen heute | Erfolgreiche Aufrufe des Tages (Vorschau und Versand zählen je einmal, abgelehnte Aufrufe nicht), Attribut „Nach Rechtestufe“ |
| Verbindung | an/aus; Attribut „Push (IDLE)“ zeigt den Push-Zustand |

**Ereignisse für Automationen:**

- `gmx_mail_new_message` – je neuer Mail im Posteingang (höchstens 20 je Abfrage). Daten: `entry_id`, `folder`, `ref`;
  Absender und Betreff nur mit der Datenschutz-Option.
- `gmx_mail_action_executed` – je schreibender Aktion. Daten: Aktion, Stufe, Ergebnis, Anzahl, Ordner, bei Versand die
  **Domains** der Empfänger – nie Adressen oder Inhalte.

Beispiel-Automation (Benachrichtigung bei neuer Mail):

```yaml
triggers:
  - trigger: event
    event_type: gmx_mail_new_message
actions:
  - action: notify.notify
    data:
      message: "Neue Mail im GMX-Postfach"
```

## 5. Aktionen

Alle Aktionen heißen `gmx_mail.<name>` und brauchen das Feld `config_entry_id` (das Konto). Ergebnisse kommen als
Antwortdaten zurück (in Skripten mit `response_variable`, über ha-mcp mit `return_response`). Nachrichten werden über eine
**Referenz** `{folder, uid, uidvalidity}` angesprochen, die `search` liefert.

| Aktion | Stufe | Kurzbeschreibung |
|---|---|---|
| `list_folders` | Lesen | Ordner mit Rolle, optional mit Anzahlen |
| `search` | Lesen | Suche nach Absender, Empfänger, Betreff, Text, Datum, Markierungen, Größe; seitenweise. Neue Mails stehen erst nach etwa 30 s im GMX-Suchindex – für jüngste Mails gibt es eine Ersatzsuche. |
| `get_message` | Lesen | Kopfzeilen, bereinigter Text, Anhangsliste. Markiert **nicht** als gelesen. |
| `get_attachment` | Lesen | Anhang speichern (Medienordner) oder kleine Anhänge als base64 |
| `get_status` | Lesen | Serverfähigkeiten, Spezialordner, Zähler, aktive Einstellungen, verbrauchte Limits |
| `get_audit_log` | Lesen | Letzte schreibende Aktionen ohne Inhalte |
| `set_flags` | Organisieren | gelesen, Fähnchen, beantwortet (`seen`, `flagged`, `answered` oder `\Seen` usw.), eigene Schlüsselwörter (z. B. `$Steuer`) |
| `move` / `copy` | Organisieren | Verschieben/Kopieren, liefert neue Referenzen |
| `mark_spam` / `mark_not_spam` | Organisieren | In den Spam-Ordner bzw. zurück in den Posteingang |
| `create_folder` / `rename_folder` | Organisieren | Ordner anlegen/umbenennen; Spezialordner sind geschützt |
| `save_draft` | Organisieren | Entwurf speichern – sendet nichts |
| `trash` | Löschen | In den Papierkorb (umkehrbar mit `move`) |
| `delete_permanently` | Löschen + Option | Endgültig löschen, nur aus Papierkorb/Spam, mit Bestätigungscode |
| `delete_folder` | Löschen | Leeren Ordner ohne Unterordner löschen, mit Bestätigungscode |
| `send_message` | Senden | Neue Mail, mit Bestätigungscode |
| `reply` | Senden | Antworten (optional an alle, mit Zitat), mit Bestätigungscode |
| `forward` | Senden | Weiterleiten mit Anhängen, mit Bestätigungscode |
| `send_draft` | Senden | Entwurf senden und danach aus „Entwürfe“ entfernen, mit Bestätigungscode |

Alle Felder sind in Home Assistant unter **Entwicklerwerkzeuge → Aktionen** beschrieben.

### Weitere Kopfzeilen und Abmeldelinks (`get_message`)

- Mit `headers` (höchstens 10 Namen) liefert `get_message` weitere Kopfzeilen unter `data.headers`, Namen in Kleinbuchstaben,
  je Vorkommen ein Eintrag. Die Werte sind **entfaltet** (Zeilenumbrüche gefalteter Kopfzeilen entfernt), sonst unverändert:
  nichts wird dekodiert (`%0A` bleibt `%0A`), nur unsichtbare Zeichen und Steuerzeichen werden entfernt.
- **Grenzen:** 8000 Zeichen je Kopfzeile und 20 000 für alle angeforderten Kopfzeilen zusammen; `List-Unsubscribe` und
  `List-Unsubscribe-Post` bis 16 000 Zeichen (zählen nicht zur Gesamtgrenze). Übliche Abmeldelinks sind viel kürzer.
- **Längere Werte** werden nie still gekürzt. Statt des Textes kommt ein Objekt
  `{"value": "…Teil…", "truncated": true, "total_chars": 12000, "offset": 0, "next_offset": 8000}`. Den Rest liefert ein
  weiterer Aufruf mit denselben `headers` und `header_offset: 8000`.
- **`list_unsubscribe`** erscheint, wenn die Mail eine `List-Unsubscribe`-Kopfzeile hat:
  - immer: `domains` (nur die Domains) und `one_click` (`true`, wenn `List-Unsubscribe-Post: List-Unsubscribe=One-Click` gesetzt ist);
  - nur wenn `List-Unsubscribe` in `headers` angefordert wird zusätzlich die vollständigen Adressen: `urls` (http/https) und
    `mailto`. Ohne Anforderung bleiben Links wie im Text auf die Domain beschränkt, denn Abmeldelinks enthalten persönliche Kennungen.
  - Die Integration ruft diese Adressen **nie** auf und prüft sie nicht; sie sind Daten aus einer fremden Mail.

Beispiel:

```yaml
action: gmx_mail.get_message
data:
  config_entry_id: <Konto-ID>
  ref: {folder: INBOX, uid: 123, uidvalidity: 456}
  headers: [List-Unsubscribe, List-Unsubscribe-Post]
  max_chars: 200
```

## 6. Zwei-Schritt-Bestätigung

Endgültiges Löschen, Ordner löschen und jeder Versand laufen in zwei Schritten:

1. **Ohne** `confirm_token` aufrufen: Es passiert nichts. Zurück kommen eine **Vorschau** (z. B. Empfänger, Betreff, Größe,
   Textanfang oder die zu löschenden Mails) und ein **Bestätigungscode**.
2. **Mit** genau diesem Code und denselben Angaben erneut aufrufen: Erst jetzt wird gelöscht bzw. gesendet.

Der Code gilt **5 Minuten**, nur **einmal** und nur für **genau** diese Angaben (ändert sich ein Zeichen im Text oder eine
Datei, ist er ungültig). Nach einem Neustart oder Neuladen der Integration ist eine neue Vorschau nötig.

> Der Code sorgt für einen Zwischenschritt, in dem der Assistent dich fragen soll. Er beweist aber **nicht**, dass du
> zugestimmt hast. Eine echte Freigabe bietet die Bestätigungsabfrage in ha-mcp – siehe [Sicherheit](SICHERHEIT.md).

## 7. Anhänge senden und speichern

- **Senden:** Dateien in den Ordner `gmx_mail/outbox` im Medienordner von Home Assistant legen (bei Home Assistant OS:
  `/media/gmx_mail/outbox`). In `send_message` nur den Dateinamen angeben, ohne Pfad. Andere Orte sind gesperrt.
- **Speichern:** `get_attachment` legt Anhänge unter `/media/gmx_mail/<Konto-ID>/` ab. Sie werden nach der eingestellten
  Frist automatisch gelöscht.

## 8. Hinweise unter „Reparaturen“

| Hinweis | Bedeutung und Abhilfe |
|---|---|
| GMX-Anmeldung abgelehnt | Passwort falsch **oder** GMX hat POP3/IMAP abgeschaltet (GMX meldet beides gleich). POP3/IMAP im Webmailer wieder einschalten, ggf. neues App-Passwort, dann die Integration neu anmelden. |
| SMTP-Anmeldung abgelehnt | Beim Senden abgelehnt; meist App-Passwort gelöscht oder geändert. Neu anmelden. Es wurde nichts gesendet. |
| Spezialordner mehrdeutig | Mehrere Ordner kommen in Frage; in den Optionen unter „Spezialordner“ den richtigen wählen. |
| Ordner auf dem Server neu aufgebaut | Alte Referenzen sind ungültig; neu suchen. |
| GMX-Filterregeln nicht verwaltbar | Nur zur Information; kann ignoriert werden. |

## 9. Fehlerbehebung

| Problem | Lösung |
|---|---|
| Anmeldung beim Einrichten abgelehnt | Adresse genau prüfen (Tippfehler!), App-Passwort neu kopieren, POP3/IMAP im Webmailer eingeschaltet und gespeichert? Gegenprobe mit einem Mailprogramm. |
| „Versand ist ausgeschaltet“ | Versand-Modus steht auf „Aus“ oder die Allowlist ist leer. |
| „Empfänger nicht erlaubt“ | Empfänger nicht auf der Allowlist. Die Meldung nennt nur Anzahl und Domains. |
| „Bestätigungscode ungültig“ | Abgelaufen, schon benutzt, Angaben geändert oder Integration neu geladen – neue Vorschau anfordern. |
| „Ergebnis unbekannt“ | Die Verbindung brach während des Vorgangs ab. Die Aktion wird **nicht** wiederholt; im Postfach (bzw. bei Versand im Ordner Gesendet und beim Empfänger) prüfen. |
| Neue Mail per Suche nicht gefunden | GMX nimmt neue Mails erst nach etwa 30 s in den Suchindex auf; kurz warten. |
| Push-Status „Nicht unterstützt“ | IDLE braucht implizites TLS (Port 993). Mit STARTTLS fragt die Integration nur im Intervall ab. |

**Diagnose:** Unter Geräte & Dienste → GMX Mail → ⋮ → **Diagnose herunterladen**. Die Datei enthält keine Adressen,
Passwörter oder Mailinhalte.

## 10. Datenschutz

- Mailinhalte werden nur auf Anfrage abgerufen und nicht gespeichert (außer Anhängen, die du ausdrücklich speicherst).
- Das Audit-Protokoll, die Ereignisse und die Diagnose enthalten keine Adressen, Betreffs oder Texte.
- Das App-Passwort liegt – wie bei allen Integrationen – unverschlüsselt im Konfigurationsspeicher von Home Assistant.
  **Backups schützen.**
- **Debug-Logging** der Integration zeigt Mailinhalte, die über die Leitung gehen (Passwörter werden geschwärzt). Nur
  kurzzeitig zur Fehlersuche einschalten und Logs nicht weitergeben.

## 11. Grenzen

- **Filterregeln** des GMX-Webmailers lassen sich nicht verwalten (GMX bietet dafür keine Schnittstelle).
- **Kein Speicherplatz-Füllstand** (GMX bietet kein IMAP-QUOTA).
- **Absender** ist immer die Kontoadresse; Aliasadressen werden nicht unterstützt.
- Die **automatische Abschaltung** von POP3/IMAP durch GMX lässt sich nicht verhindern; die Integration meldet sie, das
  Wiedereinschalten geht nur im Webmailer.
