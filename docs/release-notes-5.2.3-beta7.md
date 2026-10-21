# Pill★Pal 5.2.3-beta7

Pill★Catch erhält die buntere, verspielte Gestaltung aus den Konzept-Mockups und eine direkte Steuerung ohne separate Spurtasten.

- Farbige Tabletten und Kapseln mit Glanz, kleinen Gesichtern und eigenen, gut zuordenbaren Namensschildern. Die M-Kennungen bleiben unverändert.
- Ein höheres, leicht konisches Trinkglas mit sichtbarem Rand, Reflexen und dickem Glasboden. Gefangene Objekte behalten ihre Farbe und Form im Glas.
- Sonnenaufgang, Sonne, Sonnenuntergang oder Mond und Sterne passend zum Spiel-Tagesabschnitt; dazu passende Hintergrundfarben, dezentes Raster und beleuchtete Fangspur.
- Direkte Bedienung durch Tippen, Drücken oder Ziehen im gesamten Spielfeld. Die Links-/Mitte-/Rechts-Schaltflächen entfallen zugunsten der Spielfläche; Tastaturbedienung bleibt möglich.
- Fang-Feedback mit Punkten und eine kompakte Vorschau auf höchstens zwei noch gesuchte Medikamente.

Die Fanghöhe folgt jetzt der responsiven Glasöffnung. Bei wenig Fallstrecke wird das Anfangstempo reduziert; sehr niedrige Ansichten pausieren mit einem Hinweis, statt mit verdecktem oder schwer erreichbarem Spielfeld weiterzulaufen.

Das Spiel bleibt unabhängig von Einnahmen, Beständen und medizinischen Statistiken. Bestehende lokale Rekorde bleiben erhalten. Das Datenschema bleibt bei **13**.

**Prüfung:** Pill★Catch-Browsertests einschließlich Touch-Tipps ohne Spurtasten, Ziehen über einen Fang hinweg, Pointer-Abbruch, Ausrichtung der Fangzone, 1–20 Ziele und Höhen ab 480 Pixeln. Screenshots bei 320–1000 Pixeln Breite, in hellem und dunklem Theme sowie für alle vier Tagesabschnitte. Bestehende Zeitplan-/Statistik-Browsertests und 72 Python-Tests bestehen. Ein Praxistest auf einem echten Smartphone im Home-Assistant-Client steht noch aus.

**Installation:** In HACS Vorabversionen einblenden, **5.2.3-beta7** installieren und Home Assistant neu starten. Anschließend die Pill★Pal-Seite neu laden. Die Frontend-Cachekennungen sind aktualisiert. Das manuelle Installationspaket liegt als **PillPal-5.2.3-beta7.zip** bei. Die stabile Version bleibt **5.2.2**.
