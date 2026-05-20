import io
import json
import os
import re
from datetime import date

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.management import call_command
from django.db.models import Count, Q
from django.http import JsonResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .forms import ElternschaftForm, ElkeUploadForm, PersonForm
from .models import Aenderungslog, Ehe, Elternschaft, Foto, Ort, Person, Quelle


# ---------------------------------------------------------------------------
# Startseite
# ---------------------------------------------------------------------------

def index(request):
    personen_count = Person.objects.count()
    nachnamen_count = Person.objects.values('nachname').distinct().count()
    aelteste = Person.objects.filter(geburtsdatum__isnull=False).order_by('geburtsdatum').first()
    haeufigster_nachname = (
        Person.objects.values('nachname')
        .annotate(anzahl=Count('nachname'))
        .order_by('-anzahl')
        .first()
    )

    def generationen_zaehlen():
        wurzeln = Person.objects.filter(elternschaft_kind__vater=None, elternschaft_kind__mutter=None)
        if not wurzeln.exists():
            wurzeln = Person.objects.filter(elternschaft_kind__isnull=True)
        max_gen = 0
        visited = set()

        def tiefe(person_id, gen):
            nonlocal max_gen
            if person_id in visited:
                return
            visited.add(person_id)
            max_gen = max(max_gen, gen)
            kinder = Elternschaft.objects.filter(
                Q(vater_id=person_id) | Q(mutter_id=person_id)
            ).values_list('kind_id', flat=True)
            for kid_id in kinder:
                tiefe(kid_id, gen + 1)

        for p in wurzeln[:10]:
            tiefe(p.pk, 1)
        return max_gen

    generationen = generationen_zaehlen() if personen_count > 0 else 0

    return render(request, 'stammbaum/index.html', {
        'personen_count': personen_count,
        'nachnamen_count': nachnamen_count,
        'aelteste': aelteste,
        'haeufigster_nachname': haeufigster_nachname,
        'generationen': generationen,
    })


# ---------------------------------------------------------------------------
# Stammbaum
# ---------------------------------------------------------------------------

def stammbaum(request):
    personen = Person.objects.all().order_by('nachname', 'vornamen')
    start_id = request.GET.get('person')
    start_person = None
    if start_id:
        try:
            start_person = Person.objects.get(pk=start_id)
        except Person.DoesNotExist:
            pass
    if not start_person:
        start_person = (
            Person.objects.filter(geburtsdatum__isnull=False).order_by('geburtsdatum').first()
            or Person.objects.first()
        )
    return render(request, 'stammbaum/stammbaum.html', {
        'personen': personen,
        'start_person': start_person,
    })


def stammbaum_json(request, person_id):
    modus = request.GET.get('modus', 'nachkommen')
    tiefe_max = int(request.GET.get('tiefe', 6))

    try:
        root = Person.objects.get(pk=person_id)
    except Person.DoesNotExist:
        return JsonResponse({'error': 'Person nicht gefunden'}, status=404)

    visited = set()

    def person_node(p):
        return {
            'id': p.pk,
            'name': p.vollname,
            'nachname': p.nachname,
            'geburtsjahr': p.geburtsjahr,
            'sterbejahr': p.sterbejahr,
            'geschlecht': p.geschlecht,
            'lebensdaten': p.lebensdaten,
            'url': p.get_absolute_url(),
        }

    def nachkommen(p, tiefe):
        if tiefe > tiefe_max or p.pk in visited:
            return None
        visited.add(p.pk)
        node = person_node(p)
        kinder = list(
            Person.objects.filter(
                Q(elternschaft_kind__vater=p) | Q(elternschaft_kind__mutter=p)
            ).distinct()
        )
        children = [nachkommen(k, tiefe + 1) for k in kinder]
        children = [c for c in children if c is not None]
        if children:
            node['children'] = children
        return node

    def vorfahren(p, tiefe):
        if tiefe > tiefe_max or p.pk in visited:
            return None
        visited.add(p.pk)
        node = person_node(p)
        try:
            elternschaft = p.elternschaft_kind
            children = []
            if elternschaft.vater:
                v = vorfahren(elternschaft.vater, tiefe + 1)
                if v:
                    children.append(v)
            if elternschaft.mutter:
                m = vorfahren(elternschaft.mutter, tiefe + 1)
                if m:
                    children.append(m)
            if children:
                node['children'] = children
        except Elternschaft.DoesNotExist:
            pass
        return node

    if modus == 'vorfahren':
        data = vorfahren(root, 0)
    else:
        data = nachkommen(root, 0)

    return JsonResponse(data or {})


# ---------------------------------------------------------------------------
# Personenliste & Detail
# ---------------------------------------------------------------------------

def personen_liste(request):
    personen = Person.objects.all()
    nachname_filter = request.GET.get('nachname', '')
    ort_filter = request.GET.get('ort', '')
    if nachname_filter:
        personen = personen.filter(nachname__icontains=nachname_filter)
    if ort_filter:
        personen = personen.filter(
            Q(geburtsort__icontains=ort_filter) | Q(sterbeort__icontains=ort_filter)
        )
    return render(request, 'stammbaum/personen_liste.html', {
        'personen': personen,
        'nachname_filter': nachname_filter,
        'ort_filter': ort_filter,
    })


def person_detail(request, pk):
    person = get_object_or_404(Person, pk=pk)
    vater, mutter = person.get_eltern()
    kinder = person.get_kinder()
    partner_qs, ehen = person.get_partner()
    geschwister = person.get_geschwister()
    quellen = person.quellen.all()
    fotos = person.fotos.all()
    aenderungen = person.aenderungen.all() if request.user.is_authenticated else None
    return render(request, 'stammbaum/person_detail.html', {
        'person': person,
        'vater': vater,
        'mutter': mutter,
        'kinder': kinder,
        'partner': partner_qs,
        'ehen': ehen,
        'geschwister': geschwister,
        'quellen': quellen,
        'fotos': fotos,
        'aenderungen': aenderungen,
    })


# ---------------------------------------------------------------------------
# Person erstellen / bearbeiten
# ---------------------------------------------------------------------------

@login_required
def person_neu(request):
    if request.method == 'POST':
        form = PersonForm(request.POST, request.FILES)
        eltern_form = ElternschaftForm(request.POST)
        if form.is_valid():
            person = form.save()
            if eltern_form.is_valid():
                vater = eltern_form.cleaned_data.get('vater')
                mutter = eltern_form.cleaned_data.get('mutter')
                if vater or mutter:
                    Elternschaft.objects.create(kind=person, vater=vater, mutter=mutter)
            Aenderungslog.objects.create(
                person=person, benutzer=request.user, aktion='erstellt',
                felder={'nachher': {'nachname': person.nachname, 'vornamen': person.vornamen}},
            )
            messages.success(request, f'Person „{person.vollname}" wurde angelegt.')
            return redirect(person.get_absolute_url())
    else:
        form = PersonForm()
        eltern_form = ElternschaftForm()
    return render(request, 'stammbaum/person_form.html', {
        'form': form, 'eltern_form': eltern_form, 'titel': 'Neue Person anlegen',
    })


@login_required
def person_bearbeiten(request, pk):
    person = get_object_or_404(Person, pk=pk)
    vorher = {
        'nachname': person.nachname, 'vornamen': person.vornamen,
        'geburtsdatum': str(person.geburtsdatum), 'geburtsort': person.geburtsort,
    }
    try:
        elternschaft = person.elternschaft_kind
    except Elternschaft.DoesNotExist:
        elternschaft = None

    if request.method == 'POST':
        form = PersonForm(request.POST, request.FILES, instance=person)
        eltern_form = ElternschaftForm(request.POST, instance=elternschaft)
        if form.is_valid():
            person = form.save()
            if eltern_form.is_valid():
                vater = eltern_form.cleaned_data.get('vater')
                mutter = eltern_form.cleaned_data.get('mutter')
                if elternschaft:
                    elternschaft.vater = vater
                    elternschaft.mutter = mutter
                    elternschaft.save()
                elif vater or mutter:
                    Elternschaft.objects.create(kind=person, vater=vater, mutter=mutter)
            Aenderungslog.objects.create(
                person=person, benutzer=request.user, aktion='geaendert',
                felder={'vorher': vorher, 'nachher': {
                    'nachname': person.nachname, 'vornamen': person.vornamen,
                    'geburtsdatum': str(person.geburtsdatum), 'geburtsort': person.geburtsort,
                }},
            )
            messages.success(request, f'Person „{person.vollname}" wurde gespeichert.')
            return redirect(person.get_absolute_url())
    else:
        form = PersonForm(instance=person)
        eltern_form = ElternschaftForm(instance=elternschaft)
    return render(request, 'stammbaum/person_form.html', {
        'form': form, 'eltern_form': eltern_form,
        'titel': f'{person.vollname} bearbeiten', 'person': person,
    })


# ---------------------------------------------------------------------------
# Karte
# ---------------------------------------------------------------------------

def karte(request):
    return render(request, 'stammbaum/karte.html')


def karte_json(request):
    orte_geburt = {}
    orte_tod = {}

    for p in Person.objects.exclude(geburtsort='').values('pk', 'nachname', 'vornamen', 'geburtsort', 'geburtsdatum'):
        name = p['geburtsort'].strip()
        if name not in orte_geburt:
            orte_geburt[name] = {'ort': name, 'typ': 'geburt', 'personen': []}
        orte_geburt[name]['personen'].append({
            'id': p['pk'],
            'name': f"{p['vornamen']} {p['nachname']}".strip(),
        })

    for p in Person.objects.exclude(sterbeort='').values('pk', 'nachname', 'vornamen', 'sterbeort', 'sterbedatum'):
        name = p['sterbeort'].strip()
        if name not in orte_tod:
            orte_tod[name] = {'ort': name, 'typ': 'tod', 'personen': []}
        orte_tod[name]['personen'].append({
            'id': p['pk'],
            'name': f"{p['vornamen']} {p['nachname']}".strip(),
        })

    alle_orte = list(set(list(orte_geburt.keys()) + list(orte_tod.keys())))
    ort_coords = {}
    for ort_obj in Ort.objects.filter(name__in=alle_orte, lat__isnull=False):
        ort_coords[ort_obj.name] = {'lat': ort_obj.lat, 'lon': ort_obj.lon}

    result = []
    for name, data in {**orte_geburt, **orte_tod}.items():
        if name in ort_coords:
            result.append({**data, **ort_coords[name]})
    return JsonResponse(result, safe=False)


def geocode_ort(request, ort_name):
    if not request.user.is_authenticated:
        return JsonResponse({'error': 'Login erforderlich'}, status=403)
    ort, created = Ort.objects.get_or_create(name=ort_name)
    if ort.lat is None:
        try:
            resp = requests.get(
                'https://nominatim.openstreetmap.org/search',
                params={'q': ort_name, 'format': 'json', 'limit': 1},
                headers={'User-Agent': 'Stammbaum-App/1.0'},
                timeout=5,
            )
            data = resp.json()
            if data:
                ort.lat = float(data[0]['lat'])
                ort.lon = float(data[0]['lon'])
                ort.geocodiert_am = timezone.now()
                ort.save()
        except Exception:
            pass
    return JsonResponse({'lat': ort.lat, 'lon': ort.lon, 'name': ort.name})


# ---------------------------------------------------------------------------
# Statistiken
# ---------------------------------------------------------------------------

def statistiken(request):
    top_nachnamen = (
        Person.objects.values('nachname')
        .annotate(anzahl=Count('nachname'))
        .order_by('-anzahl')[:15]
    )
    top_vornamen_raw = []
    for p in Person.objects.exclude(vornamen='').values_list('vornamen', flat=True):
        for vn in p.split(','):
            vn = vn.strip()
            if vn:
                top_vornamen_raw.append(vn)
    from collections import Counter
    vornamen_counter = Counter(top_vornamen_raw).most_common(15)

    jahrzehnte_geburt = {}
    jahrzehnte_tod = {}
    for p in Person.objects.filter(geburtsdatum__isnull=False).values_list('geburtsdatum', flat=True):
        jz = (p.year // 10) * 10
        jahrzehnte_geburt[jz] = jahrzehnte_geburt.get(jz, 0) + 1
    for p in Person.objects.filter(sterbedatum__isnull=False).values_list('sterbedatum', flat=True):
        jz = (p.year // 10) * 10
        jahrzehnte_tod[jz] = jahrzehnte_tod.get(jz, 0) + 1

    alle_jahrzehnte = sorted(set(list(jahrzehnte_geburt.keys()) + list(jahrzehnte_tod.keys())))

    return render(request, 'stammbaum/statistiken.html', {
        'top_nachnamen': list(top_nachnamen),
        'top_vornamen': vornamen_counter,
        'jahrzehnte': alle_jahrzehnte,
        'jahrzehnte_geburt': [jahrzehnte_geburt.get(j, 0) for j in alle_jahrzehnte],
        'jahrzehnte_tod': [jahrzehnte_tod.get(j, 0) for j in alle_jahrzehnte],
        'personen_count': Person.objects.count(),
    })


# ---------------------------------------------------------------------------
# Suche
# ---------------------------------------------------------------------------

def suche(request):
    q = request.GET.get('q', '').strip()
    ergebnisse = []
    if q:
        ergebnisse = Person.objects.filter(
            Q(nachname__icontains=q)
            | Q(vornamen__icontains=q)
            | Q(geburtsort__icontains=q)
            | Q(sterbeort__icontains=q)
            | Q(anmerkungen__icontains=q)
        ).distinct()
    return render(request, 'stammbaum/suche.html', {'ergebnisse': ergebnisse, 'q': q})


# ---------------------------------------------------------------------------
# GEDCOM-Export
# ---------------------------------------------------------------------------

def export_gedcom(request):
    lines = [
        '0 HEAD',
        '1 GEDC',
        '2 VERS 5.5.1',
        '1 CHAR UTF-8',
        '1 LANG German',
    ]

    for p in Person.objects.all():
        lines.append(f'0 @I{p.pk}@ INDI')
        name = p.vornamen + ' /' + p.nachname + '/'
        if p.titel:
            name += ' ' + p.titel
        lines.append(f'1 NAME {name}')
        if p.geschlecht in ('M', 'F'):
            lines.append(f'1 SEX {p.geschlecht}')
        if p.geburtsdatum or p.geburtsort:
            lines.append('1 BIRT')
            if p.geburtsdatum:
                lines.append(f'2 DATE {p.geburtsdatum.strftime("%-d %b %Y").upper()}')
            if p.geburtsort:
                lines.append(f'2 PLAC {p.geburtsort}')
        if p.sterbedatum or p.sterbeort:
            lines.append('1 DEAT Y')
            if p.sterbedatum:
                lines.append(f'2 DATE {p.sterbedatum.strftime("%-d %b %Y").upper()}')
            if p.sterbeort:
                lines.append(f'2 PLAC {p.sterbeort}')
        try:
            elternschaft = p.elternschaft_kind
            fam_id = p.pk
            lines.append(f'1 FAMC @F{fam_id}@')
        except Elternschaft.DoesNotExist:
            pass

    fam_counter = 1
    for elternschaft in Elternschaft.objects.select_related('kind', 'vater', 'mutter'):
        lines.append(f'0 @F{elternschaft.kind.pk}@ FAM')
        if elternschaft.vater:
            lines.append(f'1 HUSB @I{elternschaft.vater.pk}@')
        if elternschaft.mutter:
            lines.append(f'1 WIFE @I{elternschaft.mutter.pk}@')
        lines.append(f'1 CHIL @I{elternschaft.kind.pk}@')

    lines.append('0 TRLR')
    content = '\n'.join(lines)
    response = HttpResponse(content, content_type='text/plain; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="stammbaum.ged"'
    return response


# ---------------------------------------------------------------------------
# Import (Datei-Upload + Import-Trigger)
# ---------------------------------------------------------------------------

@login_required
def import_view(request):
    import_path = settings.IMPORT_PATH
    vorhandene = []
    if os.path.isdir(import_path):
        vorhandene = [
            f for f in os.listdir(import_path)
            if f.lower().endswith(('.elk', '.gm3'))
        ]

    import_output = None
    import_fehler = None

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'upload':
            form = ElkeUploadForm(request.POST, request.FILES)
            if form.is_valid():
                os.makedirs(import_path, exist_ok=True)
                hochgeladen = []
                for f in request.FILES.getlist('dateien'):
                    ziel = os.path.join(import_path, f.name)
                    with open(ziel, 'wb') as out:
                        for chunk in f.chunks():
                            out.write(chunk)
                    hochgeladen.append(f.name)
                messages.success(request, f'{len(hochgeladen)} Datei(en) hochgeladen: {", ".join(hochgeladen)}')
                return redirect('import')
        elif action == 'importieren':
            form = ElkeUploadForm()
            try:
                buf = io.StringIO()
                call_command('import_elke', stdout=buf, stderr=buf)
                import_output = buf.getvalue()
                messages.success(request, 'Import erfolgreich abgeschlossen.')
            except Exception as e:
                import_fehler = str(e)
                messages.error(request, f'Import fehlgeschlagen: {e}')
        else:
            form = ElkeUploadForm()
    else:
        form = ElkeUploadForm()

    vorhandene = []
    if os.path.isdir(import_path):
        vorhandene = [
            f for f in os.listdir(import_path)
            if f.lower().endswith(('.elk', '.gm3'))
        ]

    return render(request, 'stammbaum/import.html', {
        'form': form,
        'vorhandene': vorhandene,
        'import_output': import_output,
        'import_fehler': import_fehler,
    })
