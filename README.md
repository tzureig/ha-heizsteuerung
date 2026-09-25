# Heizsteuerung für Home Assistant

Raumweise Soll-Regelung für Heizkörper- und Fußbodenthermostate (getestet mit Homematic IP und DeviReg).
Pro Raum gibt es genau ein Thermostat `climate.heizung_<raum>`. Es ist für HomeKit gedacht: Dort stellst du nur den **Soll** ein, die Integration rechnet im Hintergrund aus, wie das echte Thermostat stehen muss.

## Funktionen

- **Soll statt Ventil:** Weit unter dem Soll wird das Thermostat höher gestellt (schnell aufheizen), kurz vor dem Ziel wird gebremst (kein Überschwingen). Ein PI-Regler gleicht aus, dass der Thermostat-Fühler direkt am Heizkörper misst.
- **Soll 5 °C = aus**, und zwar dauerhaft, bis der Soll wieder hochgestellt wird.
- **Fenster/Türen:** beliebig viele Kontakte pro Raum. Ist einer offen, wird der Raum auf 5 °C gestellt (mit Verzögerung).
- **Jahreszeiten** aus Wetterstation (gedämpft, ≈ 24 h) und Online-Vorhersage (12 h):
  - *Winter* (unter „Winter unter“, Standard 5 °C): nachts höchstens 2 K Absenkung, früheres Vorheizen
  - *Übergang*: normale Sparlogik
  - *Sommer* (ab „Heizgrenze aus ab“, Standard 16 °C; wieder an unter 14 °C): alles aus, nur Auskühlschutz
- **Nacht** (Standard 00:00–05:00) mit gelerntem Vorheizen: Um „Nacht Ende“ ist jeder Raum warm.
- **Anwesenheit:** Personen pro Raum. Ist keine davon zuhause, wird auf die Abwesenheitstemperatur abgesenkt. Die Rückkehr wirkt sofort.
- **Auskühlschutz:** Kein Raum fällt unter 16 °C (außer bei Soll 5 °C oder offenem Fenster).
- **Elektrische Fußbodenheizung:** trägere Regelung, PV-Überschuss-Boost (schont den Hausakku), Sonnenbremse.
- **Homematic-IP-Cloud-schonend:** Normale Anpassungen werden höchstens alle 10 min gesendet, Fenster/Aus/Soll-Änderungen sofort.

## Installation (HACS)

1. HACS → ⋮ → *Benutzerdefinierte Repositories* → `https://github.com/tzureig/ha-heizsteuerung`, Typ *Integration*
2. „Heizsteuerung“ installieren, Home Assistant neu starten
3. *Einstellungen → Geräte & Dienste → Integration hinzufügen → Heizsteuerung*
4. In der Integration über **„Raum hinzufügen“** jeden Raum anlegen
5. In der HomeKit-Bridge nur die `climate.heizung_*`-Entitäten freigeben

## Entitäten

| Entität | Zweck |
|---|---|
| `climate.heizung_<raum>` | Soll/Ist für HomeKit |
| `sensor.heizung_<raum>_grund` | Warum gerade welches Ziel gilt |
| `sensor.heizung_<raum>_effektives_ziel` | Tatsächliches Raumziel |
| `sensor.heizsteuerung_jahreszeit` | Winter / Übergang / Sommer |
| `switch.heizsteuerung_steuerung_aktiv` | Alles pausieren (Hände weg) |
| `number.heizsteuerung_*`, `time.heizsteuerung_*` | Heizgrenzen, Auskühlschutz, Nachtzeiten |
