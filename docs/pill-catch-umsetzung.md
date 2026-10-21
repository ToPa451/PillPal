# Pill★Catch

Das Easteregg wird auf **Log & Info** durch Klick oder Tap auf die Versionsnummer geöffnet. Das Intro erklärt die Steuerung und kennzeichnet das Spiel ausdrücklich als unabhängig von Einnahmebestätigungen.

## Einbindung

- `frontend/pillpal-catch.js`: verzögert geladenes Spielmodul mit separater Planableitung, deterministisch prüfbarer Spielsimulation und Dialogoberfläche.
- `frontend/pillpal-catch.css`: eigene, an Home-Assistant-Themevariablen angepasste Darstellung, drei Spuren, transparentes Glas und mobile Ansicht.
- Die Komponente hat keine Home-Assistant-Verbindung. Nur der Rekord wird lokal gespeichert, getrennt nach Profil sowie Echtplan/Demomodus; Namen und Pläne werden nicht dauerhaft abgelegt.

Die aktuelle Datenstruktur enthält tägliche Dosierungen je grobem Tagesabschnitt. Tagesabhängige Zeitpläne ändern die Uhrzeiten, nicht die Medikamentenzuordnung. Deshalb dienen positive reguläre `doses` im aktiven Profil als Ziele, unabhängig von bereits bestätigten Einnahmen oder Beständen. Archivierte und reine Bedarfsmedikamente entfallen. Die beim Start erzeugte Momentaufnahme bleibt für weitere Spieltage bestehen; ein Neustart liest den aktuellen Plan.

Alle Medikamente erhalten für den gesamten Durchlauf eindeutige M-Kennungen. Diese unterscheiden auch gleiche Anzeigenamen, ohne zusätzliche medizinische Angaben zu erfinden. Die vollständige Liste mit Namen und Sammelstatus ist im pausierten Spiel erreichbar. Viele Ziele verlängern das Zeitbudget; acht Symbole und ein Zusatzbadge stellen den Glasinhalt dar.

Die Simulation verarbeitet Landungen chronologisch vor dem Zeitablauf, einschließlich der Deadline. Die Fangzone liegt auf der sichtbaren Glasöffnung. Nach einem vollständigen Abschnitt erfolgt der Übergang bewusst per Schaltfläche; nach einem vollständigen Tag kann derselbe Plan mit höherer Schwierigkeit weitergespielt werden.

Browser-Zurück verbirgt und pausiert die Runde. Erneutes Öffnen über die Version stellt die Pause wieder her. „Spiel verlassen“ beendet die Runde. Tabwechsel, Rotation, Größenänderungen und Detailansichten halten das Spiel ebenfalls an. Unter 480 CSS-Pixeln Höhe fordert die Pause mehr Platz beziehungsweise Hochformat an.

## Prüfung

`tests/test_pill_catch.cjs` prüft das Produktionsmodul und die Oberfläche mit simuliertem Home Assistant in einem echten Browser. Abgedeckt sind unter anderem 1, 3, 4, 8, 12 und 20 Ziele, lange und gleiche Namen, Zeitgrenzen, doppelte Fänge, vollständige Tage, Profilwechsel, Tastatur und Mausziehen, Pausen, Demomodus, gesperrter Browserspeicher und das Ausbleiben schreibender Aufrufe.

Responsive Screenshots werden optional über `PILLPAL_SCREENSHOTS` erzeugt. Ein Test auf einem echten Smartphone im Home-Assistant-Client steht noch aus; insbesondere Fingersteuerung, Displayaussparungen und 200-%-Vergrößerung sollten dort geprüft werden. Ton, Vibration und ein entspannter Spielmodus sind optionale Erweiterungen und nicht Teil dieser Umsetzung.
