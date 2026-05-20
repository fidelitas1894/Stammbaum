import os
import re
from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand

from stammbaum.models import Elternschaft, Person


class Command(BaseCommand):
    help = 'Importiert ELKE-Datenbankdateien (.ELK, .GM3) in die Django-Datenbank'

    DATUM_RE = re.compile(r'^(\d{2})(\d{2})(\d{4})$')
    # Zeile 1: Typ (7/5) + Nr + Name + VaterNr + MutterNr
    HEADER_RE = re.compile(r'^([75])\s+(\d+)(.*?)\s+(\d+)\s+(\d+)\s*$')

    def handle(self, *args, **options):
        import_path = settings.IMPORT_PATH
        if not os.path.isdir(import_path):
            self.stderr.write(f'Import-Verzeichnis nicht gefunden: {import_path}')
            return

        dateien = [
            os.path.join(import_path, f)
            for f in os.listdir(import_path)
            if f.lower().endswith(('.elk', '.gm3'))
        ]

        if not dateien:
            self.stdout.write('Keine .ELK- oder .GM3-Dateien gefunden.')
            return

        self.stdout.write(f'Gefundene Dateien: {len(dateien)}')
        for datei in sorted(dateien):
            self.stdout.write(f'  → {os.path.basename(datei)}')

        # Erst alle Personen importieren, dann Beziehungen setzen
        personen_daten = {}  # elke_nr → (person, vater_nr, mutter_nr)

        for datei in sorted(dateien):
            self.stdout.write(f'\nVerarbeite {os.path.basename(datei)} …')
            try:
                zeilen = open(datei, encoding='latin-1').readlines()
            except Exception as e:
                self.stderr.write(f'  Fehler beim Lesen: {e}')
                continue

            i = 0
            importiert = 0
            uebersprungen = 0
            while i < len(zeilen) - 1:
                zeile1 = zeilen[i].rstrip('\n\r')
                zeile2 = zeilen[i + 1].rstrip('\n\r') if i + 1 < len(zeilen) else ''

                m = self.HEADER_RE.match(zeile1)
                if m:
                    try:
                        elke_nr = int(m.group(2))
                        name_raw = m.group(3).strip()
                        vater_nr = int(m.group(4))
                        mutter_nr = int(m.group(5))

                        nachname, vornamen, titel = self._parse_name(name_raw)
                        geschlecht, geburtsdatum, sterbedatum, geburtsort, sterbeort, konfession = \
                            self._parse_datenzeile(zeile2)

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
                                'konfession': konfession,
                            },
                        )
                        personen_daten[elke_nr] = (person, vater_nr, mutter_nr)
                        importiert += 1
                        if created:
                            self.stdout.write(f'  + {nachname}, {vornamen} (Nr. {elke_nr})')
                        i += 2
                        continue
                    except Exception as e:
                        uebersprungen += 1
                i += 1

            self.stdout.write(f'  Importiert: {importiert}, Übersprungen: {uebersprungen}')

        # Beziehungen setzen
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

    def _parse_name(self, name_raw):
        """Zerlegt 'Jünger ALFRED, Karl Dr.-Ing.' in (Nachname, Vornamen, Titel)."""
        titel_pattern = re.compile(
            r'\b(Dr\.(?:-Ing\.|-Med\.|-Phil\.|-Jur\.)?|Dipl\.-(?:Ing|Kfm|Päd)\.|Prof\.|Pastor|Pfarrer|Ing\.)\b',
            re.IGNORECASE,
        )
        titel_match = titel_pattern.search(name_raw)
        titel = titel_match.group(0) if titel_match else ''
        if titel:
            name_raw = name_raw.replace(titel, '').strip()

        teile = name_raw.split(None, 1)
        nachname = teile[0].strip() if teile else ''
        vornamen_raw = teile[1].strip() if len(teile) > 1 else ''

        # Vornamen: großgeschriebene Wörter normalisieren
        vornamen_liste = [v.strip().capitalize() for v in re.split(r'[,\s]+', vornamen_raw) if v.strip()]
        vornamen = ', '.join(vornamen_liste)
        return nachname, vornamen, titel.strip()

    def _parse_datenzeile(self, zeile):
        """Parst 'M3101189831081971Magdeburg-Neustadt Augsburg Jünger 169/1898 ev'"""
        if not zeile or len(zeile) < 1:
            return 'U', None, None, '', '', ''

        geschlecht_raw = zeile[0].upper()
        geschlecht = geschlecht_raw if geschlecht_raw in ('M', 'F') else 'U'
        rest = zeile[1:]

        geburtsdatum = self._parse_datum(rest[:8])
        sterbedatum = self._parse_datum(rest[8:16]) if len(rest) >= 16 else None
        rest_felder = rest[16:].strip() if len(rest) > 16 else ''

        teile = rest_felder.split()
        geburtsort = ''
        sterbeort = ''
        konfession = ''

        # Felder heuristisch zuweisen
        ort_kandidaten = [t for t in teile if not re.match(r'^\d+/\d+$', t)]
        if len(ort_kandidaten) >= 1:
            geburtsort = ort_kandidaten[0].replace('_', '').strip()
        if len(ort_kandidaten) >= 2:
            sterbeort = ort_kandidaten[1].replace('_', '').strip()
        # Konfession ist häufig kurz (ev, rk, kath, ...)
        kuerzel = [t for t in teile if re.match(r'^(ev|rk|kath|ref|luth|dissid)\b', t, re.I)]
        if kuerzel:
            konfession = kuerzel[0].lower()

        return geschlecht, geburtsdatum, sterbedatum, geburtsort, sterbeort, konfession

    def _parse_datum(self, s):
        """Parst DDMMYYYY, gibt date oder None zurück."""
        if not s or len(s) < 8:
            return None
        s = s[:8]
        if re.match(r'^[b_0]+$', s, re.I):
            return None
        m = self.DATUM_RE.match(s)
        if not m:
            return None
        try:
            tag, monat, jahr = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if jahr < 1400 or jahr > 2100 or monat < 1 or monat > 12 or tag < 1 or tag > 31:
                return None
            return date(jahr, monat, tag)
        except ValueError:
            return None
