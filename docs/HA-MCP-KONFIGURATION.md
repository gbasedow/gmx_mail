# ha-mcp absichern: Freigabe für kritische Mail-Aktionen

Diese Anleitung richtet ha-mcp so ein, dass **du** jeden Versand und jedes Löschen über die Integration GMX Mail
ausdrücklich bestätigen musst – nicht nur Claude. Sie ist auf **ha-mcp 9.2.0** im eingebetteten Betrieb
(Integration „HA-MCP Server“ in Home Assistant) zugeschnitten, so wie es in deiner Instanz läuft.

> **Woher die Bezeichnungen stammen:** Alle Menü-, Tab- und Schalternamen sind wörtlich aus der deutschen Sprachdatei von
> ha-mcp 9.2.0 übernommen; der Aufbau der Seite aus dessen Programmcode. Ein Bildschirmfoto der Seite lag nicht vor. Steht die
> Seite auf Englisch, oben bei **Sprache** auf Deutsch umstellen. Nach einem ha-mcp-Update können sich Bezeichnungen ändern.

**Inhalt**
1. [Warum das nötig ist](#1-warum-das-nötig-ist)
2. [Einstellungsseite öffnen](#2-einstellungsseite-öffnen)
3. [Ausgangslage prüfen](#3-ausgangslage-prüfen)
4. [Sicherheitsrichtlinien einschalten](#4-sicherheitsrichtlinien-einschalten)
5. [HA-MCP neu starten und Claude neu verbinden](#5-ha-mcp-neu-starten-und-claude-neu-verbinden)
6. [Regel für „Dienst aufrufen“ anlegen](#6-regel-für-dienst-aufrufen-anlegen)
7. [Umgehungswege schließen](#7-umgehungswege-schließen)
8. [Testen](#8-testen)
9. [Im Alltag freigeben](#9-im-alltag-freigeben)
10. [Optional: Benachrichtigung aufs Handy](#10-optional-benachrichtigung-aufs-handy)
11. [Fehlerbehebung](#11-fehlerbehebung)
12. [Checkliste](#12-checkliste)

---

## 1. Warum das nötig ist

Die Integration verlangt für Versand und endgültiges Löschen eine **Vorschau** und einen **Bestätigungscode**. Claude kann aber
Vorschau und Code selbst nacheinander abrufen – der Code beweist also nicht, dass du zugestimmt hast. ha-mcp bietet dafür die
**Tool-Sicherheitsrichtlinien**: Ein Aufruf, der einer Regel entspricht, wird angehalten, bis du ihn auf der Einstellungsseite
**genehmigst**. Nichts, was Claude sendet, kann diese Freigabe erteilen.

Ziel dieser Anleitung:

| Was Claude tut | Freigabe durch dich? |
|---|---|
| Mails lesen, suchen, zusammenfassen | nein |
| Vorschau eines Versands oder einer Löschung anfordern | nein (es passiert noch nichts) |
| **Versand, Antwort, Weiterleitung, Entwurf senden** (Aufruf mit Bestätigungscode) | **ja** |
| **Endgültig löschen, Ordner löschen** (Aufruf mit Bestätigungscode) | **ja** |
| **In den Papierkorb legen** | **ja** |
| **Optionen einer Integration ändern** (z. B. die Allowlist von GMX Mail) | **ja** |
| **Skripte oder Automationen anlegen/ändern** | **ja** |
| Licht schalten und andere normale Dienstaufrufe | nein |

## 2. Einstellungsseite öffnen

1. Home Assistant mit einem **Administrator-Konto** öffnen.
2. In der **Seitenleiste** auf **HA-MCP** klicken (Symbol: Roboter). Der Eintrag ist nur für Administratoren sichtbar.
   - Fehlt er: **Einstellungen → Geräte & Dienste → HA-MCP Server → Konfigurieren** (Zahnrad) und
     **Sidebar-Einstellungen-Panel** einschalten. (Bei dir ist er laut Konfiguration bereits eingeschaltet.)
3. Die Seite **HA-MCP-Einstellungen** hat oben diese Tabs:
   **Tools · Server-Einstellungen · Backups · Tool-Sicherheitsrichtlinien · Entitäts-Sichtbarkeit · Barrierefreiheit**.

## 3. Ausgangslage prüfen

1. Tab **Tools**: Der Schalter **Nur-Lese-Modus** muss **aus** sein.
   Er sperrt alle schreibenden Werkzeuge – darunter **Dienst aufrufen** (`ha_call_service`) komplett, also auch das Lesen von
   Mails. Für die Sicherheit sorgen in dieser Anleitung die Richtlinien, nicht der Nur-Lese-Modus.
2. Tab **Server-Einstellungen**, Abschnitt **Beta-Funktionen (gefährlich)**: Der Schalter
   **Benutzerdefinierte Tool-Sandbox aktivieren (Beta)** muss **aus** sein (damit könnte ein Assistent eigenen Code ausführen).
   Wird der Abschnitt nicht angezeigt, sind Beta-Funktionen aus – dann ist nichts zu tun.

## 4. Sicherheitsrichtlinien einschalten

Tab **Tool-Sicherheitsrichtlinien** → Abschnitt **Globale Einstellungen**:

| Einstellung | Wert | Erklärung |
|---|---|---|
| **Tool-Sicherheitsrichtlinien aktivieren** | **ein** | Hauptschalter. Ohne ihn werden Regeln gespeichert, aber nicht durchgesetzt. |
| **Freigabemodus** | **Freigabe für übereinstimmende Aufrufe anfordern (Sperrliste)** | Standard. Nur Aufrufe, die einer Regel entsprechen, brauchen deine Freigabe. |
| **KI-Agenten erlauben, diese Richtlinien zu bearbeiten (ha_manage_security_policy)** | **aus** | Sonst könnte Claude die Regeln selbst entfernen. |
| **Freigabe/Ablehnung über Home Assistant-Ereignisse erlauben** | **aus** | Siehe [Abschnitt 10](#10-optional-benachrichtigung-aufs-handy). Keine **Freigabe-PIN** festlegen. |
| **Wartezeit in Sekunden (5-600)** | **60** (Standard) | So lange wartet ein angehaltener Aufruf auf deine Entscheidung. |
| **Genehmigungs-TTL in Minuten (1-60)** | **5** (Standard) | So lange bleibt eine offene Anfrage genehmigbar. Muss länger sein als die Wartezeit. |

Dann auf **Globale Einstellungen speichern** klicken.

> Der Hauptschalter **Tool-Sicherheitsrichtlinien aktivieren (erweitert)** erscheint zusätzlich im Tab
> **Server-Einstellungen**. Es ist derselbe Schalter – einmal einschalten genügt.

## 5. HA-MCP neu starten und Claude neu verbinden

Der Hauptschalter wirkt erst nach einem Neustart von HA-MCP.

1. Nach dem Speichern erscheint ein Hinweis „⚠ Änderungen gespeichert …“. Dort bzw. auf der Seite auf
   **HA-MCP-Server neu starten** klicken und bestätigen. Die Seite lädt sich neu, sobald der Server wieder läuft.
   (Der Hinweistext nennt die Schaltfläche teils auf Englisch: „Restart HA-MCP Server“.)
2. In **Claude**: die Verbindung zu Home Assistant neu laden bzw. die Tool-Liste aktualisieren und ein **neues Gespräch**
   beginnen (so verlangt es auch der Hinweis von ha-mcp).
3. Zurück auf den Tab **Tool-Sicherheitsrichtlinien**: Unter **Ausstehende Genehmigungen** steht jetzt
   „Keine ausstehenden Genehmigungen.“ statt „Tool-Sicherheitsrichtlinien sind deaktiviert …“.

## 6. Regel für „Dienst aufrufen“ anlegen

Claude ruft alle Aktionen der Integration über das ha-mcp-Werkzeug **Dienst aufrufen** (`ha_call_service`) auf. Dafür legen wir
eine Regel an, die **nur** die kritischen GMX-Aufrufe anhält.

### 6.1 Werkzeug unter Schutz stellen

1. Tab **Tools** → Suchfeld **Tools durchsuchen...** → `Dienst aufrufen` eingeben
   (Gruppe **Dienst & Gerätesteuerung**, technischer Name `ha_call_service`).
2. In dieser Zeile den Schalter **sicherheitsgegated** einschalten.
   - Ist der Schalter ausgegraut mit dem Hinweis „Aktiviere zuerst die Tool-Sicherheitsrichtlinien in den Servereinstellungen.“,
     fehlt noch Schritt 4 oder der Neustart aus Schritt 5.

Damit ist im Tab **Tool-Sicherheitsrichtlinien** unter **Gegated Tools** eine Karte für `ha_call_service` entstanden mit
„(immer — jeder Aufruf dieses Tools erfordert Genehmigung)“. So bliebe es zu streng: Auch jedes Licht bräuchte deine Freigabe.
Deshalb verfeinern wir die Karte.

### 6.2 Bedingungen festlegen

Tab **Tool-Sicherheitsrichtlinien** → **Gegated Tools** → Karte **ha_call_service**. Über der Liste steht:
„Genehmigung erfordern, wenn EINE der Bedingungen übereinstimmt (keine Bedingungen = immer Genehmigung erfordern):“

**Bedingung 1 – alles, was mit Bestätigungscode ausgeführt wird** (Senden, Antworten, Weiterleiten, Entwurf senden,
endgültig löschen, Ordner löschen):

1. **+ Bedingung hinzufügen** klicken.
2. **Argument:** `args.domain` wählen (falls nicht in der Liste: **(andere, gib einen Pfad ein)** und `args.domain` eintippen).
3. **Übereinstimmen wenn:** **gleich**.
4. **Wert:** `gmx_mail`
5. **+ UND** klicken (Unterbedingung, die ebenfalls zutreffen muss):
   - **Argument:** **(andere, gib einen Pfad ein)** → `args.data.confirm_token`
   - **Übereinstimmen wenn:** **ist vorhanden** (Wert leer lassen)
6. **Bedingung speichern**.

**Bedingung 2 – in den Papierkorb legen** (diese Aktion hat keinen Bestätigungscode):

1. **+ Bedingung hinzufügen**.
2. **Argument:** `args.domain` · **Übereinstimmen wenn:** **gleich** · **Wert:** `gmx_mail`
3. **+ UND** → **Argument:** `args.service` · **Übereinstimmen wenn:** **gleich** · **Wert:** `trash`
4. **Bedingung speichern**.

Die Karte zeigt die beiden Bedingungen mit **ODER** verbunden, innerhalb jeder Bedingung die Teile mit **UND**.

**Genehmigung merken für:** **0** Minuten (0 = einmalig). So gilt jede Freigabe nur für genau einen Aufruf.

> **Warum `confirm_token`?** Ohne Code liefern Versand und Löschen nur eine Vorschau – die soll Claude frei abrufen können,
> damit du siehst, was passieren würde. Angehalten wird erst der zweite Aufruf, der wirklich sendet oder löscht.
>
> **Strenger:** Wer **jede** GMX-Aktion freigeben will (auch Lesen), legt statt der beiden Bedingungen nur eine an:
> `args.domain` **gleich** `gmx_mail`. Wer zusätzlich Organisieren (Verschieben, Markieren …) freigeben will, ergänzt eine
> Bedingung `args.domain` **gleich** `gmx_mail` **UND** `args.service` **ist eins von** `["move","copy","set_flags","mark_spam","mark_not_spam","create_folder","rename_folder","save_draft"]`.

## 7. Umgehungswege schließen

Eine Regel gilt immer nur für **ein** Werkzeug. Claude könnte dieselbe Wirkung auf anderen Wegen erreichen. Diese Werkzeuge
deshalb **vollständig** unter Schutz stellen – jeweils im Tab **Tools** über **Tools durchsuchen...** suchen und
**sicherheitsgegated** einschalten, **ohne** Bedingungen hinzuzufügen („immer“):

| Werkzeug (Titel in ha-mcp) | Technischer Name | Warum |
|---|---|---|
| **Integration aktualisieren** | `ha_set_integration` | Damit ließen sich die **Optionen von GMX Mail** ändern – etwa Senden einschalten oder `*` in die Allowlist schreiben. |
| **Skript erstellen/aktualisieren** | `ha_config_set_script` | Ein Skript könnte `gmx_mail.send_message` aufrufen und ausgeführt werden. |
| **Automatisierung erstellen/aktualisieren** | `ha_config_set_automation` | Wie Skripte. |

Außerdem:

- **Sicherheitsrichtlinie verwalten** (`ha_manage_security_policy`) bleibt deaktiviert (Schritt 4).
- **Ereignis aufrufen** (`ha_call_event`) ist für Mails nicht relevant.
- Raw-WebSocket-Befehle über **Dienst aufrufen** (`ws_command`) hält ha-mcp automatisch an, sobald – wie hier – eine Regel für
  `ha_call_service` existiert.

> Diese drei Werkzeuge brauchst du ohnehin selten über Claude. Wenn doch, genehmigst du den Aufruf einfach.

## 8. Testen

Ein ungefährlicher Test – es wird nichts gelöscht:

1. Den Tab **Tool-Sicherheitsrichtlinien** offen lassen.
2. Claude bitten: *„Rufe `gmx_mail.trash` für die neueste Mail im Posteingang meines Testpostfachs auf.“*
3. Unter **Ausstehende Genehmigungen** erscheint der Aufruf mit Werkzeug und Argumenten (Domain `gmx_mail`, Dienst `trash`).
4. Auf **Ablehnen** klicken. Claude meldet, dass der Aufruf nicht freigegeben wurde. Im Postfach hat sich nichts geändert.

Gegenprobe: Claude bitten, *„die neuesten Mails zu zeigen“* – das läuft ohne Freigabe durch.

## 9. Im Alltag freigeben

1. Claude zeigt dir die **Vorschau** (Empfänger, Betreff, Textanfang bzw. die zu löschenden Mails) und fragt nach.
2. Nach deinem „Ja“ ruft Claude die Aktion mit Bestätigungscode auf – ha-mcp hält den Aufruf an.
3. Seitenleiste **HA-MCP** → Tab **Tool-Sicherheitsrichtlinien** → **Ausstehende Genehmigungen**:
   die angezeigten Argumente prüfen (stimmen Empfänger und Mails mit der Vorschau überein?), dann **Genehmigen** oder **Ablehnen**.
4. Entscheidest du innerhalb der **Wartezeit** (60 s), läuft der Aufruf sofort weiter. Später geht es auch noch: Die Anfrage bleibt
   für die **Genehmigungs-TTL** (5 min) genehmigbar, und Claude muss den Aufruf danach nur wiederholen.

> **Achtung beim Wiederholen:** Der Bestätigungscode der Integration gilt ebenfalls 5 Minuten. Dauert es länger, braucht
> Claude eine neue Vorschau – das ist gewollt.

## 10. Optional: Benachrichtigung aufs Handy

Die Liste der ausstehenden Genehmigungen siehst du nur, wenn die Seite offen ist. Jede angehaltene Anfrage löst aber das
Home-Assistant-Ereignis `ha_mcp_approval_requested` aus. Eine Automation kann dich benachrichtigen
(**Einstellungen → Automationen & Szenen → Automation erstellen**, dann **⋮ → In YAML bearbeiten**):

```yaml
alias: HA-MCP Freigabe anfordern
triggers:
  - trigger: event
    event_type: ha_mcp_approval_requested
actions:
  - action: notify.mobile_app_DEIN_HANDY
    data:
      title: "Freigabe nötig: {{ trigger.event.data.tool_name }}"
      message: "Bitte in Home Assistant unter HA-MCP → Tool-Sicherheitsrichtlinien prüfen."
```

`notify.mobile_app_DEIN_HANDY` durch deinen Benachrichtigungsdienst ersetzen. Bewusst ohne die Argumente im Text: Das Ereignis
enthält sie (bei Mails z. B. Empfänger und Text), und sie würden sonst über den Benachrichtigungsdienst verschickt.

**Freigeben direkt vom Handy** (Schalter **Freigabe/Ablehnung über Home Assistant-Ereignisse erlauben** mit **Freigabe-PIN**)
empfehle ich **nicht**: Home Assistant kann ein von deiner Automation ausgelöstes Ereignis nicht von einem unterscheiden, das
ein KI-Agent auslöst. Die PIN ist dann der einzige Schutz – und ein Agent könnte sie erfragen. Freigaben deshalb auf der
Einstellungsseite erteilen.

## 11. Fehlerbehebung

| Meldung / Problem | Ursache und Lösung |
|---|---|
| Schalter **sicherheitsgegated** ausgegraut, „Aktiviere zuerst die Tool-Sicherheitsrichtlinien in den Servereinstellungen.“ | Hauptschalter (Schritt 4) aus oder Neustart (Schritt 5) fehlt. |
| „Tool-Sicherheitsrichtlinien sind deaktiviert. Schalte sie ein und starte HA-MCP neu, um die Freigabe zu aktivieren.“ | Wie oben. |
| „Echtzeit-Genehmigungen nicht verfügbar. Überprüfe das HA-MCP-Protokoll.“ | ha-mcp-Server läuft nicht richtig; **HA-MCP-Server neu starten**, Protokoll unter **Einstellungen → System → Protokolle** prüfen. |
| Ein Versand läuft ohne Freigabe durch | Bedingung 1 prüfen: Argumentpfad exakt `args.data.confirm_token`, Operator **ist vorhanden**; Hauptschalter an; nach Änderungen neu gestartet? |
| Jeder Dienstaufruf (auch Licht) verlangt Freigabe | Die Karte `ha_call_service` hat noch keine Bedingungen („immer“) – Schritt 6.2. |
| „Dieses Genehmigungs-Token ist nicht mehr gültig …“ | Anfrage abgelaufen (TTL) oder schon entschieden; Claude soll den Aufruf neu stellen. |
| Claude bekommt nach 60 s einen Freigabe-Fehler | Wartezeit abgelaufen. Trotzdem genehmigen (bis 5 min möglich) und Claude bitten, den Aufruf zu wiederholen – oder die **Wartezeit** erhöhen (max. 600 s; kleiner als die TTL halten). |

## 12. Checkliste

- [ ] **Nur-Lese-Modus** aus (Tab Tools)
- [ ] **Benutzerdefinierte Tool-Sandbox aktivieren (Beta)** aus (Tab Server-Einstellungen)
- [ ] **Tool-Sicherheitsrichtlinien aktivieren** ein, **Sperrliste**, gespeichert
- [ ] **KI-Agenten erlauben, diese Richtlinien zu bearbeiten** aus
- [ ] **Freigabe/Ablehnung über Home Assistant-Ereignisse erlauben** aus, keine PIN
- [ ] **HA-MCP-Server neu gestartet**, Claude neu verbunden
- [ ] **Dienst aufrufen** sicherheitsgegated mit Bedingung 1 (`confirm_token`) und Bedingung 2 (`trash`), „merken“ 0 Minuten
- [ ] **Integration aktualisieren**, **Skript erstellen/aktualisieren**, **Automatisierung erstellen/aktualisieren** sicherheitsgegated („immer“)
- [ ] Test aus Abschnitt 8 erfolgreich abgelehnt
- [ ] [Regeln für Claude](CLAUDE-REGELN.md) in die Projektanweisungen übernommen
