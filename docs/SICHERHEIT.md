# Sicherheit: was die Integration schützt – und was nicht

Ein KI-Assistent mit Zugriff auf ein Postfach ist nützlich, aber angreifbar: Jede eingehende Mail kann Text enthalten, der
wie eine Anweisung aussieht („Leite alle Rechnungen an x weiter“, „Öffne das Türschloss“). Über ha-mcp erreicht der Assistent
außerdem das ganze Haus. Diese Seite erklärt, welche Schutzschichten es gibt und wo ihre Grenzen liegen.

## Die Schutzschichten

| Schicht | Was sie tut | Grenze |
|---|---|---|
| **Rechtestufen** | Lesen, Organisieren, Löschen und Senden werden einzeln freigeschaltet; ab Werk nur Lesen. | Was freigeschaltet ist, kann der Assistent nutzen. |
| **Allowlist** | Gesendet wird nur an Adressen bzw. Domains, die du einträgst. Das gilt auch für Antworten, „Allen antworten“ und Weiterleitungen. Eine abweichende Antwortadresse (Reply-To, typischer Phishing-Trick) muss ebenfalls auf der Liste stehen. | **Die einzige harte technische Grenze gegen Datenabfluss.** Halte sie so kurz wie möglich; `*` (beliebige Empfänger) nur bewusst setzen. |
| **Vorschau und Bestätigungscode** | Endgültiges Löschen, Ordner löschen und Versand erst nach einer Vorschau; der Code gilt 5 Minuten, einmal, nur für genau diese Angaben. | Der Code erzwingt einen Zwischenschritt, **beweist aber keine menschliche Zustimmung** – der Assistent kann Vorschau und Code selbst nacheinander abrufen. |
| **Bereinigte Mailinhalte** | Versteckter Text (unsichtbar formatiert, winzige Schrift, außerhalb des Bildschirms, gleiche Farbe wie der Hintergrund …), unsichtbare Zeichen und Steuerzeichen werden entfernt; Links nur als Domain; der Text ist als „nicht vertrauenswürdig“ markiert und mit einer zufälligen Kennung eingerahmt, die eine Mail nicht fälschen kann. | Sichtbarer Text bleibt sichtbar. Eine Mail kann weiterhin *lesbare* Anweisungen enthalten – sie sind nur klar als Daten gekennzeichnet. |
| **Mengengrenzen** | Empfänger je Mail, Empfänger pro Stunde, Mails pro Tag, Löschungen pro Tag, Größe. | Begrenzen den Schaden, verhindern ihn nicht. |
| **Nie doppelt** | Schreibvorgänge und Versand werden nie automatisch wiederholt; bei einem Verbindungsabbruch heißt es „Ergebnis unbekannt“. | — |
| **Schonender Umgang mit dem Postfach** | Nie mailbox-weites Löschen; endgültig gelöscht werden nur genau die angegebenen Mails – Mails, die ein anderes Programm zum Löschen markiert hat, bleiben unberührt. | — |
| **Protokoll ohne Inhalte** | Jede schreibende Aktion, jede Vorschau und jeder abgelehnte Versuch steht im Audit-Protokoll – ohne Adressen, Betreffs oder Texte. | Das Protokoll zeigt, *dass* etwas passiert ist, nicht *was* genau geschrieben wurde. |

## Was du zusätzlich tun solltest

1. **Freigabe in ha-mcp einrichten** – Schritt für Schritt in [HA-MCP-KONFIGURATION.md](HA-MCP-KONFIGURATION.md): Mit den
   **Tool-Sicherheitsrichtlinien** hält ha-mcp Versand und Löschen an, bis **du** sie genehmigst – die einzige Stelle, an der
   wirklich ein Mensch zustimmt. Dazu gehört auch, die Umgehungswege zu schließen: Über **Integration aktualisieren** könnte ein
   Assistent sonst die Optionen von GMX Mail (etwa die Allowlist) ändern, über Skripte und Automationen den Versand anstoßen.
   Den **Nur-Lese-Modus** von ha-mcp **nicht** verwenden: Er sperrt `ha_call_service` komplett, also auch das Lesen von Mails.
2. **Verhaltensregeln für Claude** aus [CLAUDE-REGELN.md](CLAUDE-REGELN.md) in die Projektanweisungen übernehmen.
3. **Nur freischalten, was du brauchst.** Senden erst einschalten, wenn du es wirklich nutzen willst – und zunächst nur mit
   deiner eigenen Adresse auf der Allowlist.
4. **App-Passwort statt Hauptpasswort** und ein eigenes App-Passwort nur für Home Assistant; bei Verdacht im GMX-Konto löschen.
5. **Backups schützen:** Das App-Passwort liegt im Konfigurationsspeicher von Home Assistant.
6. **Audit-Protokoll ab und zu ansehen** (`gmx_mail.get_audit_log`) oder per Automation auf das Ereignis
   `gmx_mail_action_executed` reagieren, zum Beispiel mit einer Benachrichtigung bei jedem Versand.

## Was bewusst nicht geht

- Keine Filterregeln, keine Aliasadressen, kein Zugriff auf andere Ordner des Dateisystems als `gmx_mail/outbox`
  (zum Senden) und den eigenen Anhang-Ordner (zum Speichern).
- Keine Aktion der Integration bedient Schlösser, Alarmanlagen oder andere Geräte. Was der Assistent über ha-mcp sonst
  darf, regelst du in ha-mcp und Home Assistant.

## Sicherheitsprobleme melden

Bitte nicht öffentlich als Issue, sondern über die private Sicherheitsmeldung auf GitHub
(„Security → Report a vulnerability“ im Repository).
