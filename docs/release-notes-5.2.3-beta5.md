# Pill★Pal 5.2.3-beta5

Behebt den wirkungslosen Klick auf **Zeitplan hinzufügen** beim Zugriff auf
Home Assistant über eine lokale HTTP-Adresse.

Die bisher verwendete Browserfunktion `crypto.randomUUID()` ist auf solchen
Adressen nicht verfügbar. Die Plan-ID wird jetzt mit `crypto.getRandomValues()`
erzeugt, das auch dort verfügbar ist. Bestehende Zeitpläne und Zyklus-Snapshots
bleiben unverändert; das Datenschema bleibt bei 13.

Der Zeitplan-Browsertest prüft jetzt sämtliche Formularaktionen sowohl im
vertrauenswürdigen localhost-Kontext als auch in einem normalen HTTP-Kontext
ohne `randomUUID`, jeweils bei 360, 390, 768 und 1440 Pixeln.

**Prüfung:** 72 Python-Tests, Zeitplan- und Statistik-Browsertests sowie
Python-Kompilierung und JavaScript-Syntaxprüfung bestehen. Der ursprüngliche
Fehler wurde vor der Korrektur im normalen HTTP-Kontext reproduziert.

**Installation:** In HACS Vorabversionen einblenden, **5.2.3-beta5** installieren
und Home Assistant neu starten. Anschließend die Pill★Pal-Seite neu laden.
Frontend-Cachekennungen sind aktualisiert. Für die manuelle Installation liegt
`PillPal-5.2.3-beta5.zip` bei. Die stabile Version bleibt **5.2.2**.
