import os
import re
from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand

from stammbaum.models import Elternschaft, Person


class Command(BaseCommand):
    help = 'Importiert ELKE-Datenbankdateien (.ELK, .GM3) in die Django-Datenbank'

    # Binäres Record-Muster: Typ(7/5) + NR + NAME + VATER + MUTTER + Trenner + Geschlecht + Datum
    RECORD_RE = re.compile(
        rb'[75]\s{2,10}(\d+)(.{10,80}?)\s{3,10}(\d+)\s{3,10}(\d+)'
        rb'[\x00-\xff]{0,8}'
        rb'([MFmf])'
        rb'(\d{8})'
        rb'([\d_ ]{8})',
        re.DOTALL,
    )

    KONFESSION_RE = re.compile(r'\b(ev(?:ang(?:elisch)?)?|rk|r\.k\.|kath(?:olisch)?|ref(?:ormiert)?|luth(?:erisch)?|dissid(?:ent)?)\b', re.IGNORECASE)

    def handle(self, *args, **options):
        import_path = settings.IMPORT_PATH
        if not os.path.isdir(import_path):
            self.stderr.write(f'Import-Verzeichnis nicht gefunden: {import_path}')
            return

        dateien = sorted([
            os.path.join(import_path, f)
            for f in os.listdir(import_path)
            if f.lower().endswith(('.elk', '.gm3'))
        ])

        if not dateien:
            self.stdout.write('Keine .ELK- oder .GM3-Dateien gefunden.')
            return

        self.stdout.write(f'Gefundene Dateien: {len(dateien)}')

        personen_daten = {}  # elke_nr → (person, vater_nr, mutter_nr)

        for datei in dateien:
            self.stdout.write(f'\nVerarbeite {os.path.basename(datei)} …')
            try:
                raw = open(datei, 'rb').read()
            except Exception as e:
                self.stderr.write(f'  Fehler beim Lesen: {e}')
                continue

            matches = list(self.RECORD_RE.finditer(raw))
            self.stdout.write(f'  Gefundene Records: {len(matches)}')

            importiert = 0
            fehler = 0

            for i, m in enumerate(matches):
                try:
                    elke_nr = int(m.group(1))
                    name_raw = m.group(2).decode('latin-1').strip()
                    vater_nr = int(m.group(3))
                    mutter_nr = int(m.group(4))
                    geschlecht_raw = m.group(5).decode('latin-1').upper()
                    geb_raw = m.group(6).decode('latin-1')
                    tod_raw = m.group(7).decode('latin-1')

                    # Rest-Text bis zum nächsten Record oder Ende
                    rest_start = m.end()
                    rest_end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
                    rest_bytes = raw[rest_start:rest_end]
                    rest_text = self._extract_text(rest_bytes)

                    nachname, vornamen, titel = self._parse_name(name_raw)
                    geschlecht = geschlecht_raw if geschlecht_raw in ('M', 'F') else 'U'
                    geburtsdatum = self._parse_datum(geb_raw)
                    sterbedatum = self._parse_datum(tod_raw)
                    geburtsort, sterbeort, geburtsname, konfession = self._parse_rest(rest_text)

                    person, created = Person.objects.update_or_create(
                        elke_nr=elke_nr,
                        defaults={
                            'nachname': nachname,
                            'vornamen': vornamen,
                            'rufname': vornamen.split(',')[0].strip() if vornamen else '',
                            'titel': titel,
                            'geschlecht': geschlecht,
                            'geburtsdatum': geburtsdatum,
                            'geburtsort': geburtsort,
                            'sterbedatum': sterbedatum,
                            'sterbeort': sterbeort,
                            'geburtsname': geburtsname,
                            'konfession': konfession,
                        },
                    )
                    personen_daten[elke_nr] = (person, vater_nr, mutter_nr)
                    importiert += 1
                    status = 'NEU' if created else 'AKT'
                    self.stdout.write(f'  [{status}] {nachname}, {vornamen} (Nr. {elke_nr})')

                except Exception as e:
                    fehler += 1
                    self.stderr.write(f'  Fehler bei Record: {e}')

            self.stdout.write(f'  Importiert: {importiert}, Fehler: {fehler}')

        # Eltern-Kind-Beziehungen
        self.stdout.write('\nSetze Eltern-Kind-Beziehungen …')
        beziehungen = 0
        for elke_nr, (person, vater_nr, mutter_nr) in personen_daten.items():
            vater = personen_daten.get(vater_nr, (None,))[0] if vater_nr else None
            mutter = personen_daten.get(mutter_nr, (None,))[0] if mutter_nr else None
            if vater or mutter:
                Elternschaft.objects.update_or_create(
                    kind=person,
                    defaults={'vater': vater, 'mutter': mutter},
                )
                beziehungen += 1

        self.stdout.write(f'Beziehungen gesetzt: {beziehungen}')
        self.stdout.write(self.style.SUCCESS(
            f'\nImport abgeschlossen. {Person.objects.count()} Personen in der Datenbank.'
        ))

    def _extract_text(self, raw_bytes):
        """Extrahiert lesbaren Text aus einem Binärblock (latin-1, bis zu viele Nullbytes)."""
        text = ''
        null_count = 0
        for b in raw_bytes:
            if b == 0x00:
                null_count += 1
                if null_count >= 3:
                    break
                text += ' '
            else:
                null_count = 0
                if b >= 0x20 or b == 0x09:
                    text += chr(b)
                elif b >= 0x80:
                    try:
                        text += bytes([b]).decode('latin-1')
                    except Exception:
                        pass
        return text.strip()

    def _parse_name(self, name_raw):
        """Zerlegt z.B. 'Jünger ALFRED, Karl Dr.-Ing.' → (Nachname, Vornamen, Titel)"""
        titel_re = re.compile(
            r'\s+(Dr\.(?:-Ing\.|-Med\.|-Phil\.|-Jur\.|-rer\.nat\.)?'
            r'|Dipl\.-(?:Ing|Kfm|Päd|Biol|Chem|Phys)\.'
            r'|Prof\.|Pastor|Pfarrer|Ing\.|Dipl\.-Ing\.\(FH\))\s*$',
            re.IGNORECASE,
        )
        titel = ''
        m = titel_re.search(name_raw)
        if m:
            titel = m.group(1).strip()
            name_raw = name_raw[:m.start()].strip()

        teile = name_raw.split(None, 1)
        nachname = teile[0].strip() if teile else name_raw
        vornamen_raw = teile[1].strip() if len(teile) > 1 else ''

        vornamen_liste = [v.strip().capitalize() for v in re.split(r'[,\s]+', vornamen_raw) if v.strip()]
        vornamen = ', '.join(vornamen_liste)

        return nachname, vornamen, titel

    def _parse_datum(self, s):
        """Parst DDMMYYYY → date oder None."""
        s = s.strip().replace(' ', '').replace('_', '')
        if not s or re.fullmatch(r'[b0_]+', s, re.I):
            return None
        m = re.fullmatch(r'(\d{2})(\d{2})(\d{4})', s)
        if not m:
            return None
        try:
            tag, monat, jahr = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if not (1400 <= jahr <= 2100 and 1 <= monat <= 12 and 1 <= tag <= 31):
                return None
            return date(jahr, monat, tag)
        except ValueError:
            return None

    # Präpositionen die auf mehrteilige Ortsnamen hinweisen
    _PREP = {'am', 'im', 'auf', 'zu', 'bei', 'an', 'in', 'von', 'a.', 'b.', 'ob', 'near'}

    def _parse_rest(self, text):
        """Parst den Resttext nach den Datumsfeldern.

        ELKE-Format: Geburtsort Sterbeort  Geburtsname  Konfession ...
        Geburtsort und Sterbeort sind durch einfaches Leerzeichen getrennt,
        Geburtsname folgt nach doppeltem Leerzeichen.
        """
        konfession = ''
        km = self.KONFESSION_RE.search(text)
        if km:
            konfession = km.group(1).lower()

        vor_konfession = text[:km.start()] if km else text
        vor_konfession = re.sub(r'\d+/\d+', '', vor_konfession)
        vor_konfession = re.sub(r'_{3,}', ' ', vor_konfession)

        teile = re.split(r'\s{2,}', vor_konfession.strip())
        teile = [t.strip() for t in teile if t.strip() and len(t.strip()) > 1]

        geburtsort = ''
        sterbeort = ''
        geburtsname = ''

        if teile:
            # Erstes Segment: enthält Geburtsort und optional Sterbeort (durch Leerzeichen getrennt)
            woerter = teile[0].split()
            if (len(woerter) >= 2
                    and re.match(r'^[A-ZÄÖÜa-zäöüß\-\/]+$', woerter[-1])
                    and woerter[-2].lower() not in self._PREP
                    and not woerter[-2].startswith('(')):
                # Letztes Wort = Sterbeort, Rest = Geburtsort
                sterbeort = woerter[-1]
                geburtsort = ' '.join(woerter[:-1])
            else:
                geburtsort = teile[0]

            # Zweites Segment (nach doppeltem Leerzeichen) = Geburtsname
            if len(teile) > 1:
                name_m = re.match(r'^([A-ZÄÖÜ][a-zäöüßA-ZÄÖÜ]+)', teile[1].strip())
                if name_m:
                    geburtsname = name_m.group(1)

        # Bereinigung: reine Platzhalter/Zahlen als leer behandeln
        if re.fullmatch(r'[\d/\s\?\*]+', geburtsort):
            geburtsort = ''
        if re.fullmatch(r'[\d/\s\?\*]+', sterbeort):
            sterbeort = ''

        return geburtsort[:200], sterbeort[:200], geburtsname[:200], konfession
