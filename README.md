# Pill★Pal R5.1.0 Entwicklungsstand 1

Pill★Pal R5 ist eine echte Home-Assistant-Custom-Integration. Sie ersetzt die
globale Pyscript-/Helfer-Umschaltung aus R4.1 durch dauerhaft getrennte
Personenprofile, eindeutige Entitäten und explizit personenbezogene Aktionen.

## Voraussetzungen

- Home Assistant 2026.8.0 oder neuer
- mindestens eine unter **Einstellungen → Personen** angelegte Person
- HACS nur für die komfortable Installation/Aktualisierung; eine zusätzliche
  Lovelace-Ressource ist nicht erforderlich

## Installation zum Testen

1. Den Ordner `custom_components/pillpal` nach
   `/config/custom_components/pillpal` kopieren. Bei einem manuellen Update
   den vorhandenen Ordner vollständig ersetzen und nicht nur mit dem neuen
   Inhalt zusammenführen. Dadurch bleiben insbesondere keine alten Dateien
   aus `__pycache__` zurück.
2. Home Assistant vollständig neu starten.
3. **Einstellungen → Geräte & Dienste → Integration hinzufügen → Pill★Pal**
   öffnen.
4. Die aufzunehmenden Personen auswählen und festlegen, ob ein inaktives
   Beispielmedikament angelegt oder leer begonnen wird. Bei Personen mit eigenem Login kann
   optional die Assistenz durch Administratoren erlaubt werden. Personen ohne
   Login sind automatisch assistiert.
5. Das persönliche Dashboard **Pill★Pal** beziehungsweise als Administrator
   **Pill★Pal Assistenz** öffnen. Ein Home-Assistant-Neustart ist nach dem
   Assistenten nicht erforderlich; bei einem bereits geöffneten Browser kann
   einmaliges Neuladen mit `Strg+F5` nötig sein.

Alternativ kann der Inhalt dieses Versionsordners als eigenes HACS-Repository
des Typs **Integration** veröffentlicht und hinzugefügt werden.

## Dashboard und Navigation

Die persönlichen und administrativen Dashboards werden von der Integration
selbst registriert; eine Lovelace-Ressource oder eine zusätzliche Dashboard-YAML
ist nicht erforderlich. Auf schmalen Bildschirmen öffnet die Menüschaltfläche in
der Pill★Pal-Kopfzeile die Home-Assistant-Seitenleiste.

Horizontales Wischen im freien Seitenbereich wechselt nativ zwischen den
Pill★Pal-Seiten. Gesten, die in Dropdowns, Eingabefeldern, Schaltflächen,
Tabellen, Logs oder der Navigationsleiste beginnen, werden nicht als
Seitenwechsel interpretiert. Die Erweiterung `hass-swipe-navigation` ist daher
für Pill★Pal nicht erforderlich.

Geänderte Medikamenten- und Einstellungsformulare können mit **Änderungen
verwerfen** vollständig auf den dauerhaft gespeicherten Stand zurückgesetzt
werden. Beim Seiten- oder Medikamentenwechsel fragt Pill★Pal vor einem Verlust
ungespeicherter Eingaben nach. Rückmeldungen zu Speichern, Auffüllen und
Archivieren stehen unmittelbar bei der ausgelösten Aktion; eine Ablehnung
bleibt dort zusammen mit den noch korrigierbaren Eingaben sichtbar.

Im Assistenz-Dashboard wird jede Aktion unveränderlich an die beim Klick
ausgewählte Person gebunden. Ein schneller Personenwechsel kann weder die
laufende Aktion noch deren Aktualisierung auf das neue Profil umlenken.
Fällige Einnahmen stehen in der mobilen Übersicht vor Status und Historie; im
Log ist die Systeminformation vor der längeren Ereignisliste angeordnet.

## Daten- und Zugriffsmodell

- Es gibt genau einen Integrations-Haupteintrag und je aufgenommener Person
  einen Untereintrag samt logischem Gerät.
- Jeder schreibende Backend-Aufruf verlangt eine `person_id`. Es existiert kein
  global ausgewähltes Profil.
- Ein angemeldeter Benutzer sieht ausschließlich seine verknüpfte Person.
- Das Admin-Dashboard listet nur Personen mit Admin-Assistenz und nie das
  eigene Profil.
- Wird eine HA-Person entfernt, bleiben Profil und Historie erhalten; ihre
  Medikamente werden archiviert.
- Eine später neu angelegte Person wird über **Eintrag hinzufügen** im
  Pill★Pal-Integrationseintrag aufgenommen.

## Datensicherheit und Reparatur

Pill★Pal validiert Einstellungen und gespeicherte Profil-, Medikamenten-,
Zyklus- und Slotdaten vor ihrer Verwendung. Wird beim Start ein beschädigter
oder nur teilweise migrierter Store erkannt, speichert die Integration dessen
unveränderten Inhalt zuerst separat unter einer Quarantäne-ID. Nur wenn dieses
Backup gelingt, wird ein kontrolliert reparierter Stand als Live-Store
gespeichert. Dashboard und personenbezogener Log weisen anschließend auf die
Quarantäne hin. Unterstützte ältere Schemas erhalten dabei einen dokumentierten
Migrationszeitpunkt. Ein neueres, von dieser Version nicht unterstütztes Schema
wird zwar quarantänisiert, aber weder heruntergestuft noch überschrieben.
Unbekannte Store-, Profil-, Medikamenten- und Laufzeitfelder werden nicht still
übernommen.

Das frühere `dashboard_path` und die alten Ausgangshelfer für Fälligkeit und
Tagesabschluss sind keine aktive Konfiguration mehr. Beim Upgrade auf
Datenschema 9 werden vorhandene Altwerte kontrolliert entfernt, ohne deshalb
eine Quarantäne oder einen Konfigurationsfehler auszulösen. Benachrichtigungen
verwenden den von der Integration registrierten internen Dashboardpfad.

Ein vorübergehend fehlender oder unvollständiger Home-Assistant-Personen-State
löscht weder Profil noch Benutzerlink; vollständige Folgeevents aktualisieren
Name und Verknüpfung. Listener und Hintergrundaufgaben sind an den jeweiligen
Ladezyklus gebunden, sodass alte Callbacks nach Reload oder Shutdown keine
Änderungen mehr ausführen.

Der Diagnoseexport enthält grundsätzlich nur Struktur-, Anzahl-,
Status- und Konfiguriert-ja/nein-Angaben: Personen-, Medikamenten-, Entitäts-,
Nachrichten-, Log-, Token- und Quarantäneprofilinhalte werden nicht ausgegeben.

Schreibvorgänge werden revisionsbasiert in fester Reihenfolge ausgeführt. Eine
Dashboard- oder Dienstaktion zeigt deshalb erst nach erfolgreichem dauerhaftem
Speichern Erfolg an. Scheitert der Commit, erscheint eine Fehlermeldung und die
noch nicht bestätigte Änderung wird im Arbeitsspeicher zurückgerollt.

### Backup, Wiederherstellung und vollständiges Entfernen

Die maßgebliche Sicherung ist ein vollständiges Home-Assistant-Backup. Vor
Upgrade, Rückkehr zu einer älteren Pill★Pal-Version oder dem Entfernen der
Integration sollte ein solches Backup erstellt und dessen Wiederherstellbarkeit
geprüft werden. Ein Reload oder vorübergehendes Deaktivieren der Integration
behält alle Fachdaten. Das bestätigte **Entfernen des gesamten Pill★Pal-
Integrationseintrags** löscht dagegen dessen Live-Store und
Quarantänespeicher dauerhaft; eine Wiederherstellung ist danach nur aus einem
vorherigen Home-Assistant-Backup möglich. Das Entfernen nur einer
Personen-Subentry archiviert deren Fachdaten weiterhin und ist keine
Vollöschung. Vor der Archivierung beziehungsweise Vollöschung bereinigt
Pill★Pal alle bekannten profilbezogenen Handyhinweise. Ist ein gespeicherter
Notify-Dienst beim Entfernen einer Person nicht verfügbar, bleibt der exakte
Löschauftrag erhalten und wird nach Rückkehr des Dienstes erneut versucht.

## Automationsentitäten und Aktionen

Je Person werden unter anderem Fälligkeit, nächste Einnahme, Einnahmetreue,
Nachbestellungen sowie Buttons zum Bestätigen, Zurückstellen und Überspringen
angelegt. Zusätzlich stehen Dienste unter `pillpal.*` bereit. Dienste erwarten
immer eine `person_id`; damit bleiben auch Automationen eindeutig.

`pillpal.adjust_stock` korrigiert den Bestand eines Medikaments relativ. Das
Delta muss ungleich null, höchstens ±10000 und ein Vielfaches der hinterlegten
kleinsten Teilung sein. Eine zu große negative Korrektur wird nachvollziehbar
auf Bestand 0 begrenzt. Ereignis, Log und Action-Ergebnis enthalten angeforderte
und tatsächlich angewandte Änderung; Erfolg wird erst nach dauerhaftem Speichern
zurückgegeben.

## Medikamentenpflege und Taster

Die kleinste Teilung gilt serverseitig für Bestand, Packungsgröße, reguläre
Dosen, Höchstdosen, Auffüllung, Bedarfsbuchung, Bestandskorrektur und Menge je
Tastendruck. Der Bedarfsdialog verwendet ausschließlich Plus-/Minus-Schritte;
„Halb“ mit 0,5 je Tastendruck bleibt ausdrücklich unterstützt.

Ein MHD kann vom Vorjahr bis fünf Jahre nach dem aktuellen Jahr eingetragen
werden. Das Datumsfeld ist nur bei aktivierter MHD-Prüfung sichtbar. Archivierte
Medikamente erscheinen in der Pflegeauswahl ausschließlich bei aktivem
Archivfilter; ein zukünftiger offener Slot wird nach Reaktivierung wieder
hergestellt, während bereits vergangene Dosen weiterhin nicht nachträglich
erzeugt werden.

Ein medikamentenspezifischer Input-Button bestätigt bei einem regulär und bei
Bedarf verwendbaren Medikament zuerst den passenden regulären Slot. Nur reine
Bedarfsmedikamente werden damit als Bedarf gebucht. Attributänderungen und
doppelt zugestellte identische Button-Ereignisse lösen keine Buchung aus. Eine
abgelehnte Betätigung wird geloggt und kurz mit Grund sowie der nächsten
regulären Einnahme am Handy angezeigt.

## Benachrichtigungen

Ein aktuell registrierter Dienst `notify.mobile_app_…` kann unmittelbar als
Notify-Ziel ausgewählt werden. Pill★Pal aktualisiert diese Auswahl bei späterer
Registrierung oder Entfernung eines Dienstes. Ein gültiger Notify-Dienst
**oder** die aktive native personenbezogene Entität **Einnahme fällig** reicht
als Erinnerungsweg aus. Eine Warnung erscheint nur, wenn bei regelmäßiger
Medikation beide Wege fehlen; Dashboard, Entität und Log verwenden dafür
dieselbe Entscheidung.

Die kritische Erinnerung verwendet wieder die Alarmstandardwerte von R4.0.17
und listet jedes Medikament mit Bullet Point in eigener Zeile. Erfolgreiche
Helfer-/Buttonbuchungen erhalten zehn Sekunden lang ein nicht alarmierendes
Handyfeedback. Ergebnis- und Ablehnungsmeldungen nach einer Companion-Action
haben keine erzwungene kurze Laufzeit. Eine Buchung direkt im persönlichen oder
administrativen Dashboard bereinigt zwar den Alarm, erzeugt aber bewusst keine
zusätzliche Handybestätigung.

Nach `TAKE` oder `SKIP` über eine Companion-Action nennt die Rückmeldung den
nächsten offenen Einnahmeslot oder den vollständigen Tages-Zyklus. Ist ein
Folgeslot schon fällig, stehen dessen neu gebundene Aktionen direkt in dieser
Rückmeldung bereit. `SKIP` bestätigt zusätzlich, dass der Bestand unverändert
blieb. Beim automatischen Übergang zu „Verpasst“ beendet Pill★Pal die
Wiederholung und löscht die exakte alte Slotbenachrichtigung.

Kann dieses Feedback nach dem bereits erfolgreichen Fachcommit vorübergehend
nicht zugestellt werden, bleibt die Action dennoch erfolgreich. Pill★Pal
speichert ausschließlich den fehlenden Feedback-Seiteneffekt und versucht ihn
später erneut, auch über einen Zykluswechsel hinweg. Die Einnahmeaktion selbst
wird dabei nie wiederholt. Versandzeit, Wiederholungsanker und sichtbarer
Erfolgslog einer Erinnerung werden erst nach bestätigtem Notify-Aufruf gesetzt.

Die Aktionskennung ist nicht nur an Person, Zyklus und Slot, sondern mit einem
opaken Einmaltoken auch an das konkrete Notify-Gerät gebunden. Nach einem
Zielwechsel werden Actions aus alten oder kopierten Benachrichtigungen
verständlich abgelehnt, ohne Einnahmestatus oder Bestand zu verändern.
Nach SNOOZE bleiben TAKE, SNOOZE und SKIP in der dauerhaften, nicht alarmierenden
Rückmeldung verfügbar. Erneutes SNOOZE verlängert die bereits bestehende
Endzeit und bindet alle Actions an einen neuen Einmaltoken.

Ändert sich ein noch offener Medikamenten- oder Zeitplan, entfernt Pill★Pal
eine bereits sichtbare alte Erinnerung vor dem Neuversand und rotiert deren
Action-Token. Explizites Snooze akzeptiert nur einen tatsächlich fälligen oder
bereits zurückgestellten Slot aus dem aktuellen Zyklus. Ein wiederholtes
gültiges TAKE bleibt ohne zweite Bestandsbuchung, versucht aber erneut die alte
Benachrichtigung zu bereinigen. Ein vorübergehend fehlender Notify-Dienst
verbraucht keinen Erinnerungstermin und wird nach Registrierung sofort erneut
angesprochen. Auch direkt nach Start oder Reload wird der vollständige
Benachrichtigungszustand einmal abgeglichen; ein im alten Prozess nur
reservierter Versand gilt dabei nicht fälschlich als zugestellt.

Bei einem tatsächlichen Wechsel des Notify-Ziels bereinigt Pill★Pal den alten
Endpunkt best-effort und veröffentlicht noch aktive Einnahme-, Bestands- und
MHD-Hinweise am neuen Ziel. Bestandsmeldungen werden erst nach erfolgreichem
Versand als zugestellt gespeichert und reagieren auf sämtliche sichtbaren
Detailänderungen.

Nachbestell- und MHD-Hinweise haben eigene, personenbezogene Titel. Ein einziges
gemeinsames Symbol gilt für alle Meldungsarten; technische Tags vergibt
Pill★Pal stabil intern und bietet sie deshalb nicht im Editor an. MHD-Termine
erscheinen mit deutschem Datum, eigener Zeile je Medikament und Angabe, ob das
Präparat heute, in wie vielen Tagen oder seit wie vielen Tagen abgelaufen ist.

## Nachbestellung, MHD und Praxisplanung

Für jedes aktive regelmäßige Medikament berechnet Pill★Pal aus Bestand und
Tagesdosis das voraussichtliche Leer-Datum. Daraus entstehen der normale und der
wirksame Bestelltag. Liegt der normale Termin in einem zusammenhängenden Block
aus Wochenende, Feiertag oder hinterlegter Praxisschließung, wird geprüft, ob
danach noch genügend echte Öffnungstage bis zum Leerstand verbleiben; andernfalls
wird die Erinnerung um die eingestellte Zahl offener Praxistage vorgezogen.

Das Mitbestellfenster ergänzt weitere Präparate, deren Leerstand kurz nach einem
bereits fälligen Medikament liegt. Der Bestellvorschlag enthält Packungsgrößen,
Kosten beziehungsweise Zuzahlungen, einen kopierbaren Bestelltext und einen
Hinweis, wenn Kosten nicht vollständig gepflegt sind. Dieselben Daten stehen als
maschinenlesbare Attribute der personenbezogenen Entität **Nachbestellungen**
und im Dashboard bereit.

Ein verbundener Feiertagskalender wird einmal täglich sowie unmittelbar nach
Auswahl- oder Zustandsänderungen über `calendar.get_events` vorausgelesen. Ein
vorübergehend noch nicht synchronisierter Kalender wird automatisch erneut
abgerufen, ohne dabei einen Diagnosefehler oder Fehlerhinweis zu erzeugen. Die
technischen Details erfolgreicher Abrufe und echter Fehler stehen im Log,
während die Praxis-Seite nur den kompakten Status und beliebig viele laufende
oder zukünftige Schließzeiträume zeigt; beendete Zeiträume werden nicht mehr
angezeigt oder berechnet.

## Statistik, Historie und Diagnoselog

Beim Start eines Tages-Zyklus speichert Pill★Pal für jeden tatsächlich
geplanten Slot einen fachlichen Snapshot mit Zyklus, Sollzeit, Medikament,
Menge und damaliger Einheit. Offene Statusänderungen werden bis zum Abschluss
nachgeführt; abgeschlossene historische Slots werden durch spätere Änderungen
an Plan, Name oder Einheit nicht umgeschrieben. Ältere Daten ohne solchen
Snapshot werden ausschließlich aus ihren damaligen terminalen Ereignissen
ergänzt, nie aus dem heutigen Medikamentenplan.

Dashboard, native Statistikentitäten, `pillpal.statistics` und der read-only
Statistik-WebSocket verwenden dieselbe Modellberechnung. Zeitraum,
benutzerdefiniertes Von/Bis, Medikament, Einnahmezeit und ausgewählter Tag
filtern Kennzahlen, Heatmap und Tagesliste gemeinsam. Neben geplant,
eingenommen, übersprungen und verpasst wird auch ausstehend ausgewiesen;
Bedarf zeigt Buchungszahl und Gesamtmenge separat.

Der personenbezogene Diagnoselog enthält alle Ereignisse der letzten echten 48
Stunden ohne 500-Einträge-Kappung. Änderungen an Medikamenten und Einstellungen
nennen verständlich Feld, Alt- und Neuwert. Abgelehnte Actions, technische
Fehler und ungefangene Fehler eigentümergebundener Hintergrundaufgaben werden
zusätzlich zum Home-Assistant-Systemlog beim richtigen Pill★Pal-Profil sichtbar.

## Native Entitäten und Actions

Jedes Personenprofil stellt neben Fälligkeit und nächster Einnahme vier stabile
Slot-Sensoren für morgens, mittags, abends und zur Nacht bereit. Zyklus-ID und
-datum, Fälligkeits-, Buchbarkeits-, Snooze- und Abschlusszeit sowie
Medikamente, Mengen und Einheiten stammen in allen Entitäten aus demselben
Profilzustand. Eine eigene Praxisstatus-Entität nennt Grund und nächsten
Öffnungstag. Die Einnahmetreue-Entität enthält einen 30-Tage-Verlauf mit
Heatmap und Tagesdetails; geplante, eingenommene, übersprungene, verpasste und
Bedarfseinnahmen stehen zusätzlich als getrennte Zähler bereit.

Öffentliche Actions wählen das Pill★Pal-Personenprofil als Gerät und zeigen die
vier Einnahmezeiten deutsch beschriftet an. Medikamenten-Actions akzeptieren
einen eindeutigen sichtbaren Medikamentennamen; technische IDs bleiben für
bestehende Automationen kompatibel. Jede Action kann ein maschinenlesbares
Ergebnis zurückgeben und aktualisiert zusätzlich die personenbezogene Entität
**Aktionsrückmeldung** sowie das Ereignis `pillpal_action_result` mit
`pending`, `success` oder `error`. Einmaltokens werden nie in der Entität oder
im Ereignis veröffentlicht.

`pillpal.statistics` liefert frei filterbare Zeiträume, Medikamente,
Einnahmezeiten, Heatmap und Tagesdetails als Action-Antwort.
`pillpal.recalculate` erzwingt eine personenbezogene Neuberechnung und versucht
fehlgeschlagene Einnahmekalender-Ausgaben erneut. Ist ein Einnahmekalender
gewählt, erzeugen bestätigte, übersprungene, automatisch verpasste und
Bedarfseinnahmen genau einen strukturierten Kalendereintrag mit Medikamenten in
einzelnen Bullet-Zeilen. Beim Entfernen einer Personen-Subentry werden nur deren
Entitäts- und Geräteregistrierungseinträge bereinigt; die archivierten
Pill★Pal-Fachdaten bleiben erhalten.

## R4.1-Daten

R4.1 und R5 dürfen nicht gleichzeitig Einnahmen verarbeiten. Wegen der in den
Testständen beobachteten Profilvermischungen gibt es keinen stillen Import. Der
Dienst `pillpal.import_r410` heißt sichtbar **R4.1-Medikamente kontrolliert
importieren** und verlangt eine kontrollierte JSON-Datei sowie eine explizite
Zuordnung alter Profil-IDs zu neuen Personen-IDs. Er übernimmt ausschließlich
Medikamente. Einstellungen, Schnittstellen, Tages-Zyklen, Buchungen, Statistik
und Log werden bewusst nicht importiert.

## Beta-Hinweis

Diese Fassung enthält die neue Architektur und die erste vollständige
Bedienoberfläche. Vor einem Alltagseinsatz sollte sie auf einer Testinstanz mit
realistischen Personen-, Benachrichtigungs- und Automationskonfigurationen
geprüft werden. Medikamentenentscheidungen dürfen nicht ausschließlich von
Home Assistant abhängig gemacht werden.

Quellstand und Installationspaket werden mit `tools/release_package.py`
bytegenau abgeglichen. Die vollständige automatisierte und reale Prüffolge
steht in `RELEASE_CHECKLIST.md`; bewusste Abweichungen von R4 sind in
`CHANGE_SCOPE.md` dokumentiert.

