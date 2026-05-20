# Stammbaum-Webapp

Eine lokale Webanwendung zur Anzeige und Verwaltung von Familienstammbäumen,
basierend auf ELKE-Genealogie-Datenbankdateien.

## Features

- **Interaktiver Stammbaum** — D3.js-Baum mit Zoom, Pan und Nachkommen-/Vorfahrenansicht
- **Personenliste** — Durchsuchbare Tabelle aller Personen (DataTables.js)
- **Personendetail** — Alle Felder, Familie, Quellen, Änderungshistorie
- **Herkunftskarte** — Geburts- und Sterbeorte auf OpenStreetMap (Leaflet.js + Nominatim)
- **Statistiken** — Nachnamen, Vornamen, Geburten/Sterbefälle pro Jahrzehnt (Chart.js)
- **Volltextsuche** — über Namen, Orte, Anmerkungen
- **GEDCOM-Export** — Standard-Format für Genealogie-Software
- **CSV-Export** — Alle Personen als CSV-Datei
- **ELKE-Import** — Upload und Import von `.ELK`/`.GM3`-Dateien über die Weboberfläche
- **Benutzerverwaltung** — Lesen ohne Login, Bearbeiten nur eingeloggt

## Tech-Stack

| Komponente | Technologie |
|---|---|
| Backend | Django 4.2 |
| Datenbank | PostgreSQL 16 |
| Frontend | Bootstrap 5 |
| Stammbaum | D3.js v7 |
| Karte | Leaflet.js 1.9 |
| Tabellen | DataTables.js |
| Container | Docker + Docker Compose |

## Voraussetzungen

- Docker & Docker Compose
- ELKE-Datenbankdateien (`.ELK` und `.GM3`) — werden **nicht** im Repo gespeichert

## Installation

```bash
# 1. Repo klonen
git clone https://github.com/fidelitas1894/Stammbaum.git
cd Stammbaum

# 2. Umgebungsvariablen konfigurieren
cp .env.example .env
# .env bearbeiten: SECRET_KEY, DB_PASSWORD, ALLOWED_HOSTS anpassen

# 3. Stack starten
docker compose up -d

# 4. Datenbank initialisieren
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser

# 5. Im Browser öffnen
# http://localhost/
```

## ELKE-Dateien importieren

Die ELKE-Dateien können über zwei Wege importiert werden:

**Option A — Weboberfläche (empfohlen):**
1. Als Admin einloggen
2. Navigiere zu `/import/`
3. `.ELK` und `.GM3` Dateien hochladen
4. „Import starten" klicken

**Option B — Kommandozeile:**
```bash
# Dateien ins import/-Verzeichnis kopieren
cp /pfad/zu/*.ELK import/
cp /pfad/zu/*.GM3 import/

# Import ausführen
docker compose exec web python manage.py import_elke
```

Der Import ist **idempotent** — mehrfaches Ausführen erzeugt keine Duplikate.

## Über das ELKE-Format

Die `.ELK`/`.GM3`-Dateien sind binäre Microsoft Jet/Access-Datenbanken der
Genealogie-Software [ELKE von Ostermann](https://de.wikipedia.org/wiki/ELKE_(Software)).
Der Import-Parser liest die Personenrecords direkt aus dem Binärstream per Regex.

## Datenstruktur

```
Person ──── Elternschaft ──── Person (Vater/Mutter)
       └─── Ehe          ──── Person (Partner)
       └─── Quelle
       └─── Foto
       └─── Aenderungslog
```

## Lizenz

Privates Projekt — kein offizielles Lizenzmodell.
Die ELKE-Importdateien enthalten persönliche Familiendaten und werden nicht veröffentlicht.
