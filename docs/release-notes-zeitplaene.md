# Pill★Pal 5.2.3-beta4: Zeitpläne mit Feiertagsabweichungen (Issue #19)

Auf „Zeiten“ lassen sich pro Person benannte Zeitpläne mit frei wählbaren
Wochentagen pflegen. Ein stets aktiver Standard gilt an allen Tagen ohne
passenden Zusatzplan. Zusatzpläne lassen sich deaktivieren und entfernen;
aktive Zusatzpläne dürfen sich nicht überschneiden.

Jeder Plan enthält Aufstehzeit, Morgenverzögerung, Mittagsfenster, Abend- und
Nachtzeit. Optional gelten eigene Feiertagswerte aus dem bereits zugeordneten
Feiertagskalender. Die erste Aktivierung übernimmt die normalen Werte als
Ausgangspunkt; eigene Feiertagswerte bleiben beim Ausschalten erhalten.

Aufgestanden-Helfer und gültiger nächster Smartphone-Wecker werden unabhängig
berücksichtigt. Fehlende Informationen ersetzt der ausgewählte Plan. Ein
erreichbarer Aufgestanden-Helfer im Zustand „off“ verhindert automatische
Starts. Ein Wecker löst weiterhin keinen Aufstehstart aus.

Die Auswahl richtet sich nach dem lokalen Datum des Zyklusstarts. Nachtzeiten
nach Mitternacht bleiben beim begonnenen Zyklus. Ein gespeicherter Snapshot
erhält Planname und Parameter auch nach Planänderungen, Löschung, Neustart und
Datenrundlauf. Neue Kalenderdaten und Planänderungen gelten ab dem nächsten
Zyklus; spätere Weckerinformationen können weiterhin zukünftige Einnahmen
anpassen. Bereits fällige und abgeschlossene Einnahmen bleiben geschützt.

## Migration auf Datenschema 13

- Bestehende wirksame Zeitparameter werden in genau einen Standardplan
  übernommen. Es werden keine Wochenend- oder Feiertagszeiten geraten.
- Die bisherigen festen Felder für Morgen und Mittag entfallen. Sie wurden
  bislang nicht als feste Einnahmezeiten ausgewertet; Morgen bleibt relativ
  zum tatsächlichen Aufstehen, Mittag wird innerhalb seines Fensters berechnet.
- Alte Zeitfelder werden nicht zusätzlich im normalen gespeicherten Modell
  geführt. Unterstützte alte Schreibzugriffe werden dem Standard zugeordnet;
  feste Morgen-/Mittagszugriffe erhalten eine verständliche Fehlermeldung.
- Laufende Zyklen erhalten einen kompatiblen Snapshot und behalten gespeicherte
  Fälligkeiten, Aktions-Tokens, Einnahmen, Bestände und Historie.
- Beschädigte importierte Zeitpläne werden einzeln über die bestehende
  Speicherdaten-Diagnose behandelt. Ein gültiger Standard bleibt erhalten.
- Neustarts erzeugen höchstens den fehlenden Zyklus des aktuellen Tages.
  Nicht existente Aufstehzeiten werden bei der Zeitumstellung vorwärts
  abgebildet, doppelte Uhrzeiten verwenden die erste Ausprägung.

## Prüfung

72 Python-Tests bestehen, einschließlich Migration mit abgeschlossener Einnahme,
Planwahl, Validierung, Kalenderfehlern, Kalenderwechsel, Snapshot-Rundlauf,
Helfer-/Weckerkombinationen, Schutz fälliger Einnahmen und Berliner Zeitumstellung.
Die Produktionsoberfläche wurde mit simulierten Home-Assistant-Antworten bei
360, 390, 768 und 1440 Pixeln geprüft; der bestehende Statistik-Browsertest
besteht ebenfalls. Ein Test gegen eine laufende Home-Assistant-Installation
wurde nicht durchgeführt.

## Installation

In HACS Vorabversionen für Pill★Pal einblenden, **5.2.3-beta4** auswählen
und anschließend Home Assistant neu starten. Die Frontend-Cachekennungen
wurden aktualisiert. Für die manuelle Installation enthält
`PillPal-5.2.3-beta4.zip` den Ordner `custom_components/pillpal`.

Dies ist eine Vorabversion. Die letzte stabile Version bleibt **5.2.2**.
Details zur fachlichen Grundlage: [Issue #19](https://github.com/ToPa451/PillPal/issues/19).
