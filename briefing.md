# Stammbaum-Webapp - Briefing für Claude Code

## Projektübersicht

Ziel ist eine **lokale Webanwendung** auf einem Debian-LXC-Container (Proxmox),
die einen Familienstammbaum aus importierten ELKE-Datenbankdateien anzeigt,
durchsuchbar macht und erweiterbar ist.

Der gesamte Stack läuft als **Docker-Compose-Setup**, damit die App portabel ist
und auch andere sie mit eigenen ELKE-Dateien betreiben können.

---

## Server & Infrastruktur (lokale Instanz)

- **Server-IP:** `YOUR_SERVER_IP`
- **OS:** Debian 13 (LXC-Container auf Proxmox)
- **Projektverzeichnis:** `/srv/webapp/`
- **Import-Dateien (lokal):** `/opt/import/` (ABC.ELK, ABC.GM3, ABC.ldb)

---

## Tech-Stack

| Komponente | Technologie |
|---|---|
| Backend | Django 4.x |
| Datenbank | PostgreSQL 16 |
| Frontend | Bootstrap 5 |
| Stammbaum-Visualisierung | D3.js v7 (d3-hierarchy / tree layout) |
| Karte | Leaflet.js 1.9 (OpenStreetMap, offline-fähig) |
| Tabellen | DataTables.js |
| Icons | Bootstrap Icons |
| App-Server | Gunicorn |
| Reverse Proxy | nginx |
| Containerisierung | Docker + Docker Compose |

---

## Docker-Setup

### Container-Struktur

```
┌─────────────────────────────────────────────┐
│ docker-compose stack                        │
│                                             │
│  ┌──────────┐    ┌──────────────────────┐   │
│  │  nginx   │───▶│  Django + Gunicorn   │   │
│  │  :80     │    │  :8000               │   │
│  └──────────┘    └──────────┬───────────┘   │
│                             │               │
│                  ┌──────────▼───────────┐   │
│                  │  PostgreSQL 16       │   │
│                  │  :5432               │   │
│                  └──────────────────────┘   │
│                                             │
│  Volumes: import/ (read-only), media/,      │
│           postgres_data/                    │
└─────────────────────────────────────────────┘
```

### Projektstruktur mit Docker-Dateien

```
stammbaum-app/                   ← GitHub-Repo-Root
├── .gitignore
├── .env.example                 ← Vorlage, KEIN echtes .env einchecken
├── docker-compose.yml
├── docker-compose.override.yml  ← optional, für lokale Entwicklung
├── Dockerfile
├── nginx/
│   └── default.conf
├── import/                      ← LEER im Repo (nur README.md drin)
│   └── README.md
├── manage.py
├── requirements.txt
├── stammbaum/                   ← Django-App
│   ├── models.py
│   ├── views.py
│   ├── urls.py
│   ├── forms.py
│   ├── admin.py
│   ├── management/
│   │   └── commands/
│   │       └── import_elke.py
│   └── templates/
│       └── stammbaum/
│           ├── base.html
│           ├── index.html
│           ├── stammbaum.html
│           ├── personen_liste.html
│           ├── person_detail.html
│           ├── person_form.html
│           ├── karte.html
│           └── statistiken.html
├── config/                      ← Django-Projekt-Einstellungen
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── static/
└── media/                       ← Hochgeladene Fotos (nicht im Repo)
```

### `docker-compose.yml`

```yaml
services:
  db:
    image: postgres:16-alpine
    volumes:
      - postgres_data:/var/lib/postgresql/data
    env_file: .env
    environment:
      POSTGRES_DB: ${DB_NAME}
      POSTGRES_USER: ${DB_USER}
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    restart: unless-stopped

  web:
    build: .
    command: gunicorn config.wsgi:application --bind 0.0.0.0:8000
    volumes:
      - ./media:/app/media
      - ./import:/app/import   # eigene ELKE-Dateien hier ablegen
    env_file: .env
    depends_on:
      - db
    restart: unless-stopped

  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
    volumes:
      - ./nginx/default.conf:/etc/nginx/conf.d/default.conf:ro
      - ./media:/app/media:ro
      - ./static:/app/static:ro
    depends_on:
      - web
    restart: unless-stopped

volumes:
  postgres_data:
```

### `Dockerfile`

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN python manage.py collectstatic --noinput
EXPOSE 8000
```

### `.env.example` (wird ins Repo eingecheckt)

```
# Kopiere diese Datei zu .env und fülle die Werte aus
SECRET_KEY=dein-zufaelliger-django-secret-key
DEBUG=False
ALLOWED_HOSTS=localhost,127.0.0.1,YOUR_SERVER_IP

DB_NAME=djangodb
DB_USER=djangouser
DB_PASSWORD=sicheres-passwort-hier

# Pfad zu den ELKE-Importdateien (innerhalb des Containers)
IMPORT_PATH=/app/import
```

### `nginx/default.conf`

```nginx
server {
    listen 80;
    client_max_body_size 20M;

    location /static/ {
        alias /app/static/;
    }
    location /media/ {
        alias /app/media/;
    }
    location / {
        proxy_pass http://web:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

### Installation (für alle, die das Repo nutzen)

```bash
git clone https://github.com/USER/stammbaum-app
cd stammbaum-app

# 1. Eigene ELKE-Dateien in import/ ablegen
cp /pfad/zu/deinen/dateien/*.ELK import/
cp /pfad/zu/deinen/dateien/*.GM3 import/

# 2. Umgebungsvariablen setzen
cp .env.example .env
nano .env   # SECRET_KEY und DB_PASSWORD anpassen

# 3. Starten
docker compose up -d

# 4. Datenbank initialisieren
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser

# 5. ELKE-Daten importieren
docker compose exec web python manage.py import_elke
```

---

## `.gitignore`

```gitignore
# Persönliche Daten - niemals ins Repo
import/*.ELK
import/*.GM3
import/*.elk
import/*.gm3
import/*.ldb
import/*.LDB

# Umgebung & Secrets
.env
*.env

# Django
media/
*.pyc
__pycache__/
db.sqlite3
staticfiles/

# Docker
postgres_data/

# Python
venv/
.venv/
*.egg-info/
dist/
build/

# IDE
.vscode/
.idea/
*.swp
```

### `import/README.md` (wird ins Repo eingecheckt)

```markdown
# Import-Verzeichnis

Lege hier deine ELKE-Datenbankdateien ab:

- `*.ELK` - Haupt-Datenbankdatei
- `*.GM3` - Zweite Tabellendatei
- `*.ldb` - Lock-Datei (kann ignoriert werden)

Diese Dateien werden beim Start des Containers read-only eingebunden
und über `python manage.py import_elke` eingelesen.

**Die Dateien in diesem Verzeichnis sind in `.gitignore` aufgeführt
und werden niemals ins Repository hochgeladen.**
```

---

## Quelldaten - ELKE-Format

Die Dateien `*.ELK` und `*.GM3` sind **Microsoft Jet/Access-Datenbanken**
(proprietäres ELKE-Format der Genealogie-Software „ELKE" von Ostermann).
Die `.ldb`-Datei ist eine Lock-Datei und kann ignoriert werden.

### Bekannte Datensatzstruktur (aus Reverse Engineering)

Die Personendaten sind als binäre Records in der Access-DB gespeichert.
Jeder Record enthält folgende Felder (als eingebetteter Textblock im Binärstream):

```
[7 oder 5]  [NR]  [NACHNAME VORNAMEN Titel]  [VATER-NR]  [MUTTER-NR]
[Binärtrenner ~5 Bytes][M/F][DDMMYYYY Geburt][DDMMYYYY Tod][Geburtsort][Sterbeort][Familienname-Ref][Reg-Nr][Konfession][...]
```

Beispiel (anonymisiert):

```
7       4Mustermann VORNAME, Weiterer Dr.-Ing.        8       9
[binary]M3101189831081971Musterstadt Beispielort  Mustermann 169/1898 2088/1971 ev ...
```

- Zeilen mit `7` = ELK-Datei, Zeilen mit `5` = GM3-Datei (gleiche Personen, andere Tabelle)
- Geschlecht: `M` = männlich, `F` = weiblich
- Datumsformat: `DDMMYYYY` (ohne Trennzeichen), Leerzeichen oder `________` = unbekannt
- Konfession: `ev` = evangelisch, weitere Kürzel möglich
- Vater-Nr / Mutter-Nr = Verweis auf andere Personen-Nr (0 = unbekannt)
- Encoding: **Latin-1 (ISO-8859-1)** (als binäre Datenbankdatei)

### Import-Skript

Management Command `python manage.py import_elke`, das:
1. Alle `*.ELK` und `*.GM3` Dateien aus `IMPORT_PATH` (aus `.env`) als Binärdaten einliest
2. Per Regex die Personenrecords aus dem Binärstream extrahiert
3. Personen, Beziehungen und Ereignisse in PostgreSQL schreibt
4. Idempotent ist (`update_or_create`, wiederholbarer Import ohne Duplikate)
5. Ein Log der importierten/aktualisierten Datensätze ausgibt

---

## Datenbankschema (Django Models)

### `Person`
```
id (int, PK)
elke_nr (int, unique, nullable)       # Original-Nummer aus ELKE
nachname (str)
vornamen (str)                        # Alle Vornamen, Komma-getrennt
rufname (str, nullable)               # Erster/Hauptvorname
titel (str, nullable)                 # z.B. "Dr.-Ing.", "Dipl.-Ing."
geschlecht (str)                      # 'M', 'F', 'U' (unbekannt)
geburtsdatum (date, nullable)
geburtsort (str, nullable)
sterbedatum (date, nullable)
sterbeort (str, nullable)
konfession (str, nullable)
beruf (str, nullable)
todesursache (str, nullable)
anmerkungen (text, nullable)
foto (ImageField, nullable)
erstellt_am (datetime, auto)
geaendert_am (datetime, auto)
```

### `Ehe`
```
id (int, PK)
partner1 (FK → Person)
partner2 (FK → Person)
heiratsdatum (date, nullable)
heiratsort (str, nullable)
scheidungsdatum (date, nullable)
anmerkungen (text, nullable)
```

### `Elternschaft`
```
id (int, PK)
kind (FK → Person)
vater (FK → Person, nullable)
mutter (FK → Person, nullable)
```

### `Ort` (Geocoding-Cache)
```
id (int, PK)
name (str, unique)
lat (float, nullable)
lon (float, nullable)
geocodiert_am (datetime, nullable)
```

### `Quelle`
```
id (int, PK)
person (FK → Person)
beschreibung (str)
typ (str)   # 'kirchenbuch', 'standesamt', 'ahnenpass', 'zeuge', 'sonstiges'
erstellt_am (datetime, auto)
```

### `Aenderungslog`
```
id (int, PK)
person (FK → Person)
benutzer (FK → User)
aktion (str)                  # 'erstellt', 'geaendert', 'geloescht'
felder (json)                 # {"vorher": {...}, "nachher": {...}}
zeitstempel (datetime, auto)
```

### `Foto`
```
id (int, PK)
person (FK → Person)
bild (ImageField)
beschriftung (str, nullable)
aufnahmejahr (int, nullable)
```

---

## Gewünschte Seiten & Features

### 1. Startseite `/`
- Statistik-Kacheln: Anzahl Personen, Generationen, ältester Vorfahre, häufigster Nachname
- Links zu Stammbaum, Personenliste, Karte

### 2. Interaktiver Stammbaum `/stammbaum/`
- D3.js Tree-Layout (horizontal, links nach rechts)
- Knoten zeigt: Name, Geburts-/Sterbejahr
- Klick auf Knoten → Personendetail
- Zoom & Pan (d3-zoom)
- Generationen ein-/ausblenden
- Ausgangsperson wählbar

### 3. Personenliste `/personen/`
- DataTables.js mit allen Personen
- Suchfeld, sortierbar, filterbar nach Nachname / Geburtsort
- Klick → Personendetail

### 4. Personendetail `/personen/<id>/`
- Alle Felder, Foto, Eltern/Geschwister/Kinder/Ehepartner als Links
- Quellen/Belege
- Änderungshistorie (nur eingeloggt)
- Buttons „Bearbeiten" / „Person hinzufügen" (nur eingeloggt)

### 5. Person bearbeiten `/personen/<id>/bearbeiten/`
- Formular mit allen Feldern
- Autocomplete für Eltern/Ehepartner
- Foto hochladen, Quellen verwalten
- Speichern schreibt in `Aenderungslog`

### 6. Person hinzufügen `/personen/neu/`
- Gleiches Formular, optional direkt Eltern/Partner verknüpfen

### 7. Karte `/karte/`
- Leaflet.js + OpenStreetMap
- Marker für Geburts- und Sterbeorte (Geocoding via Nominatim, gecacht)
- Klick auf Marker → Personenliste für diesen Ort
- Legende: Geburtsort (grün) / Sterbeort (rot)

### 8. Statistiken `/statistiken/`
- Häufigste Nachnamen & Vornamen (Chart.js)
- Personen pro Generation
- Geburten/Sterbefälle pro Jahrzehnt
- Herkunftsregionen

### 9. Suche `/suche/?q=...`
- Volltextsuche über Name, Ort, Anmerkungen

### 10. GEDCOM-Export `/export/gedcom/`
- GEDCOM 5.5.1, Download als `.ged`

### 11. CSV-Export `/export/csv/`
- Alle Personen als CSV, Download als `.csv`

### 12. Login `/login/` und `/logout/`
- Django Auth, lesen ohne Login, bearbeiten nur eingeloggt

---

## Authentifizierung & Rechte

| Aktion | Ohne Login | Mit Login |
|---|---|---|
| Stammbaum anzeigen | ✅ | ✅ |
| Personendetail lesen | ✅ | ✅ |
| Person bearbeiten | ❌ | ✅ |
| Person hinzufügen | ❌ | ✅ |
| Änderungshistorie sehen | ❌ | ✅ |
| GEDCOM exportieren | ✅ | ✅ |
| CSV exportieren | ✅ | ✅ |

---

## Hinweise für die Implementierung

- **Encoding:** Alle `.ELK`/`.GM3`-Dateien sind binäre Access-Datenbanken
- **Datumsparsen:** Leerzeichen, `0000`, `________` als `None` behandeln
- **Geocoding:** Nominatim-API, Ergebnisse in `Ort`-Tabelle cachen
- **Bilder:** `MEDIA_ROOT=/app/media/`, `MEDIA_URL=/media/`
- **IMPORT_PATH** aus `.env` lesen (Standard: `/app/import`)
- **Django Admin** aktivieren
- **settings.py** liest alle Werte aus Umgebungsvariablen (über `python-decouple` oder `os.environ`)

---

*Erstellt auf Basis von Dateianalyse der ELKE-Exportdateien. Personenbezogene Testdaten wurden entfernt.*
