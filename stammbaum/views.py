import csv
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

def stammbaum_gesamt(request):
    return render(request, 'stammbaum/stammbaum_gesamt.html')


def stammbaum_medium(request):
    return render(request, 'stammbaum/stammbaum_medium.html')


def stammbaum_ftm(request):
    return render(request, 'stammbaum/stammbaum_ftm.html')


def stammbaum_ftm_svg(request):
    import graphviz
    from .familytreemaker import Family

    personen       = list(Person.objects.all())
    ehen           = list(Ehe.objects.select_related('partner1', 'partner2').all())
    elternschaften = list(Elternschaft.objects.select_related('kind', 'vater', 'mutter').all())

    family = Family()
    family.populate_from_django(personen, ehen, elternschaften)

    ancestor = family.find_first_ancestor()
    if not ancestor:
        return HttpResponse('<svg xmlns="http://www.w3.org/2000/svg"/>', content_type='image/svg+xml')

    dot_string = family.output_dot_string(ancestor)

    src = graphviz.Source(dot_string)
    svg_bytes = src.pipe(format='svg')
    svg_text  = svg_bytes.decode('utf-8')
    svg_text  = svg_text[svg_text.index('<svg'):]

    return HttpResponse(svg_text, content_type='image/svg+xml')


def stammbaum_medium_svg(request):
    import graphviz

    personen       = list(Person.objects.all())
    ehen           = list(Ehe.objects.select_related('partner1', 'partner2').all())
    elternschaften = list(Elternschaft.objects.select_related('kind', 'vater', 'mutter').all())

    # Ehe-Lookup: frozenset(p1_id, p2_id) → Ehe-PK
    ehe_by_partners = {}
    for e in ehen:
        key = frozenset(filter(None, [e.partner1_id, e.partner2_id]))
        ehe_by_partners[key] = e.pk

    dot = graphviz.Digraph(
        'Stammbaum',
        graph_attr={
            'rankdir':  'TB',
            'splines':  'ortho',
            'nodesep':  '0.6',
            'ranksep':  '0.9',
            'bgcolor':  'transparent',
            'fontname': 'Helvetica',
        },
        node_attr={
            'fontname': 'Helvetica',
            'fontsize': '11',
            'margin':   '0.18,0.10',
        },
        edge_attr={
            'arrowsize': '0.6',
            'color':     '#94a3b8',
        },
    )

    # Personenknoten
    for p in personen:
        if p.geschlecht == 'M':
            fill, stroke = '#dbeafe', '#3b82f6'
        elif p.geschlecht == 'F':
            fill, stroke = '#fce7f3', '#ec4899'
        else:
            fill, stroke = '#f1f5f9', '#94a3b8'

        label   = p.vollname
        tooltip = p.vollname
        if p.lebensdaten:
            label   += f'\n{p.lebensdaten}'
            tooltip += f' ({p.lebensdaten})'

        dot.node(
            f'p{p.pk}',
            label     = label,
            shape     = 'box',
            style     = 'filled,rounded',
            fillcolor = fill,
            color     = stroke,
            penwidth  = '1.5',
            URL       = f'/personen/{p.pk}/',
            tooltip   = tooltip,
        )

    # Familien-/Partnerknoten + Kanten
    seen_fam = set()

    for el in elternschaften:
        key    = frozenset(filter(None, [el.vater_id, el.mutter_id]))
        ehe_pk = ehe_by_partners.get(key)
        fam_id = f'f{ehe_pk}' if ehe_pk else f'vf{el.kind_id}'

        if fam_id not in seen_fam:
            seen_fam.add(fam_id)
            dot.node(fam_id, shape='point', width='0.08', height='0.08',
                     label='', style='filled', fillcolor='#64748b', color='#64748b')

            with dot.subgraph() as s:
                s.attr(rank='same')
                for pid in (el.vater_id, el.mutter_id):
                    if pid:
                        s.node(f'p{pid}')
                        dot.edge(f'p{pid}', fam_id,
                                 arrowhead='none', style='solid', color='#f59e0b', penwidth='2')
                s.node(fam_id)

        if el.kind_id:
            dot.edge(fam_id, f'p{el.kind_id}', color='#64748b')

    # Kinderlose Ehen
    for e in ehen:
        fid = f'f{e.pk}'
        if fid not in seen_fam:
            seen_fam.add(fid)
            dot.node(fid, shape='point', width='0.08', height='0.08',
                     label='', style='filled', fillcolor='#64748b', color='#64748b')
            with dot.subgraph() as s:
                s.attr(rank='same')
                for pid in (e.partner1_id, e.partner2_id):
                    if pid:
                        s.node(f'p{pid}')
                        dot.edge(f'p{pid}', fid,
                                 arrowhead='none', style='solid', color='#f59e0b', penwidth='2')
                s.node(fid)

    svg_bytes = dot.pipe(format='svg')
    svg_text  = svg_bytes.decode('utf-8')
    # Nur den <svg>-Teil zurückgeben, ohne XML-Prolog
    svg_text  = svg_text[svg_text.index('<svg'):]

    return HttpResponse(svg_text, content_type='image/svg+xml')


def stammbaum_genogramm(request):
    return render(request, 'stammbaum/stammbaum_genogramm.html')


def stammbaum_fraktal(request):
    return render(request, 'stammbaum/stammbaum_fraktal.html')


def stammbaum_fraktal_json(request):
    from collections import defaultdict, deque

    personen_qs = list(Person.objects.all().values(
        'pk', 'nachname', 'vornamen', 'geschlecht', 'geburtsdatum'
    ))
    personen = {p['pk']: p for p in personen_qs}

    # Build partner map: pk → [{id, name, geschlecht, url}]
    partner_map = defaultdict(list)
    for ehe in Ehe.objects.values('partner1_id', 'partner2_id'):
        p1_id, p2_id = ehe['partner1_id'], ehe['partner2_id']
        for own_id, other_id in ((p1_id, p2_id), (p2_id, p1_id)):
            if own_id in personen and other_id in personen:
                op = personen[other_id]
                vorname = (op['vornamen'] or '').split(',')[0].strip()
                partner_map[own_id].append({
                    'id': other_id,
                    'name': f"{vorname} {op['nachname']}".strip(),
                    'geschlecht': op['geschlecht'],
                    'url': f"/personen/{other_id}/",
                })

    children_of = defaultdict(list)
    has_parent = set()

    for e in Elternschaft.objects.values('vater_id', 'mutter_id', 'kind_id'):
        if e['vater_id']:
            children_of[e['vater_id']].append(e['kind_id'])
        if e['mutter_id']:
            children_of[e['mutter_id']].append(e['kind_id'])
        has_parent.add(e['kind_id'])

    # Count descendants per person via bottom-up BFS
    desc_count = {pk: 0 for pk in personen}
    # Topological order: process children before parents
    in_deg = {pk: len(children_of.get(pk, [])) for pk in personen}
    queue = deque(pk for pk in personen if not children_of.get(pk))
    topo = []
    visited_topo = set()
    while queue:
        pk = queue.popleft()
        if pk in visited_topo:
            continue
        visited_topo.add(pk)
        topo.append(pk)
    # Simple BFS descendant count
    for pk in personen:
        q2 = deque(children_of.get(pk, []))
        seen = set()
        while q2:
            c = q2.popleft()
            if c in seen:
                continue
            seen.add(c)
            desc_count[pk] += 1
            q2.extend(children_of.get(c, []))

    # Use ?root=pk or default to person with most descendants
    root_pk = request.GET.get('root')
    if root_pk:
        try:
            root_pk = int(root_pk)
            if root_pk not in personen:
                root_pk = None
        except (ValueError, TypeError):
            root_pk = None

    if root_pk is None:
        # Person with most descendants who has no parent (true root)
        candidates = [(pk, desc_count[pk]) for pk in personen if pk not in has_parent and desc_count[pk] > 0]
        if not candidates:
            candidates = [(pk, desc_count[pk]) for pk in personen if desc_count[pk] > 0]
        root_pk = max(candidates, key=lambda x: x[1])[0] if candidates else (next(iter(personen)) if personen else None)

    # Build top-10 candidates for dropdown (most descendants, preferring true roots)
    top_candidates = sorted(
        [(pk, desc_count[pk]) for pk in personen if desc_count[pk] > 0],
        key=lambda x: x[1], reverse=True
    )[:10]
    candidates_data = []
    for pk, cnt in top_candidates:
        p = personen[pk]
        vorname = (p['vornamen'] or '').split(',')[0].strip()
        candidates_data.append({
            'id': pk,
            'name': f"{vorname} {p['nachname']}".strip(),
            'descendants': cnt,
        })

    def build_node(pk, visited):
        if pk in visited:
            return None
        visited.add(pk)
        p = personen[pk]
        vorname = (p['vornamen'] or '').split(',')[0].strip()
        node = {
            'id': pk,
            'name': f"{vorname} {p['nachname']}".strip(),
            'geschlecht': p['geschlecht'],
            'geburtsjahr': p['geburtsdatum'].year if p['geburtsdatum'] else None,
            'url': f"/personen/{pk}/",
            'partners': partner_map.get(pk, []),
        }
        kids = [build_node(c, set(visited)) for c in children_of.get(pk, [])]
        kids = [k for k in kids if k]
        if kids:
            node['children'] = kids
        return node

    tree = build_node(root_pk, set()) if root_pk else None
    if not tree:
        tree = {'id': 0, 'name': 'Kein Stammbaum', 'virtual': True}

    return JsonResponse({'tree': tree, 'candidates': candidates_data, 'root_pk': root_pk})


def stammbaum_gesamt_json(request):
    personen = list(Person.objects.all().values(
        'pk', 'nachname', 'vornamen', 'geschlecht', 'geburtsdatum', 'sterbedatum'
    ))
    nodes = []
    for p in personen:
        geb = p['geburtsdatum'].year if p['geburtsdatum'] else None
        tod = p['sterbedatum'].year if p['sterbedatum'] else None
        vorname = (p['vornamen'] or '').split(',')[0].strip()
        nodes.append({
            'id': p['pk'],
            'name': f"{vorname} {p['nachname']}".strip(),
            'nachname': p['nachname'],
            'geschlecht': p['geschlecht'],
            'geburtsjahr': geb,
            'sterbejahr': tod,
            'lebensdaten': (f"* {geb}" if geb else '') + (f" † {tod}" if tod else ''),
            'url': f"/personen/{p['pk']}/",
        })

    links = []
    for e in Elternschaft.objects.select_related('kind', 'vater', 'mutter').all():
        if e.vater_id:
            links.append({'source': e.vater_id, 'target': e.kind_id, 'typ': 'elternteil'})
        if e.mutter_id:
            links.append({'source': e.mutter_id, 'target': e.kind_id, 'typ': 'elternteil'})

    pk_set = {p['pk'] for p in personen}
    ehen = [
        {'a': e.partner1_id, 'b': e.partner2_id}
        for e in Ehe.objects.all()
        if e.partner1_id in pk_set and e.partner2_id in pk_set
    ]

    return JsonResponse({'nodes': nodes, 'links': links, 'ehen': ehen})

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
        # Prefer someone with the most descendants
        top = (
            Person.objects.annotate(
                kinder_count=Count('kinder_als_vater') + Count('kinder_als_mutter')
            ).order_by('-kinder_count').first()
        )
        start_person = top or Person.objects.first()
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

    if modus == 'beides':
        all_nodes = {}
        all_links = []
        link_set = set()

        def add_n(p):
            if p.pk not in all_nodes:
                all_nodes[p.pk] = person_node(p)

        def add_l(src, tgt, typ):
            k = (src, tgt, typ)
            if k not in link_set:
                link_set.add(k)
                all_links.append({'source': src, 'target': tgt, 'typ': typ})

        anc_vis = set()

        def collect_anc(p, depth):
            if p.pk in anc_vis or depth > tiefe_max:
                return
            anc_vis.add(p.pk)
            add_n(p)
            try:
                e = p.elternschaft_kind
                if e.vater:
                    add_n(e.vater)
                    add_l(e.vater.pk, p.pk, 'elternteil')
                    collect_anc(e.vater, depth + 1)
                if e.mutter:
                    add_n(e.mutter)
                    add_l(e.mutter.pk, p.pk, 'elternteil')
                    collect_anc(e.mutter, depth + 1)
            except Elternschaft.DoesNotExist:
                pass

        desc_vis = set()

        def collect_desc(p, depth):
            if p.pk in desc_vis or depth > tiefe_max:
                return
            desc_vis.add(p.pk)
            add_n(p)
            kinder = list(Person.objects.filter(
                Q(elternschaft_kind__vater=p) | Q(elternschaft_kind__mutter=p)
            ).distinct())
            for k in kinder:
                add_n(k)
                add_l(p.pk, k.pk, 'elternteil')
                collect_desc(k, depth + 1)

        collect_anc(root, 0)
        collect_desc(root, 0)

        # Siblings of focal person
        try:
            e_root = root.elternschaft_kind
            sib_q = Q()
            if e_root.vater:
                sib_q |= Q(elternschaft_kind__vater=e_root.vater)
            if e_root.mutter:
                sib_q |= Q(elternschaft_kind__mutter=e_root.mutter)
            if sib_q:
                for s in Person.objects.filter(sib_q).exclude(pk=root.pk).distinct():
                    add_n(s)
                    if e_root.vater:
                        add_l(e_root.vater.pk, s.pk, 'elternteil')
                    if e_root.mutter:
                        add_l(e_root.mutter.pk, s.pk, 'elternteil')
        except Elternschaft.DoesNotExist:
            pass

        # Spouses of all collected persons
        ehe_done = set()
        for pk in list(all_nodes.keys()):
            try:
                p_obj = Person.objects.get(pk=pk)
            except Person.DoesNotExist:
                continue
            partner_qs, _ = p_obj.get_partner()
            for partner in partner_qs:
                add_n(partner)
                ep = (min(pk, partner.pk), max(pk, partner.pk))
                if ep not in ehe_done:
                    ehe_done.add(ep)
                    add_l(ep[0], ep[1], 'ehe')

        return JsonResponse({
            'type': 'graph',
            'focal_id': root.pk,
            'nodes': list(all_nodes.values()),
            'links': all_links,
        })

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
    geschwister = person.get_geschwister()
    quellen = person.quellen.all()
    fotos = person.fotos.all()
    aenderungen = person.aenderungen.all() if request.user.is_authenticated else None

    # Build partner → gemeinsame Kinder from Elternschaft records
    partner_kinder = {}  # partner_pk → {'partner': Person|None, 'kinder': [Person]}
    for e in Elternschaft.objects.filter(
        Q(vater=person) | Q(mutter=person)
    ).select_related('kind', 'vater', 'mutter'):
        other = e.mutter if e.vater_id == person.pk else e.vater
        key = other.pk if other else 0
        if key not in partner_kinder:
            partner_kinder[key] = {'partner': other, 'kinder': []}
        partner_kinder[key]['kinder'].append(e.kind)

    # Also include Ehe-based partners with no shared children
    for ehe in Ehe.objects.filter(
        Q(partner1=person) | Q(partner2=person)
    ).select_related('partner1', 'partner2'):
        partner = ehe.partner2 if ehe.partner1_id == person.pk else ehe.partner1
        if partner.pk not in partner_kinder:
            partner_kinder[partner.pk] = {'partner': partner, 'kinder': []}

    # Sort each group's children by birth date
    for group in partner_kinder.values():
        group['kinder'].sort(key=lambda p: p.geburtsdatum or date.max)

    partner_kinder_list = sorted(
        partner_kinder.values(),
        key=lambda g: (g['partner'] is None, g['partner'].vollname if g['partner'] else '')
    )

    return render(request, 'stammbaum/person_detail.html', {
        'person': person,
        'vater': vater,
        'mutter': mutter,
        'partner_kinder': partner_kinder_list,
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

    partner_list = []
    for ehe in Ehe.objects.filter(Q(partner1=person) | Q(partner2=person)):
        partner = ehe.partner2 if ehe.partner1_id == person.pk else ehe.partner1
        partner_list.append({'ehe_pk': ehe.pk, 'partner': partner})

    return render(request, 'stammbaum/person_form.html', {
        'form': form, 'eltern_form': eltern_form,
        'titel': f'{person.vollname} bearbeiten', 'person': person,
        'partner_list': partner_list,
    })


# ---------------------------------------------------------------------------
# Partner-Verwaltung (AJAX)
# ---------------------------------------------------------------------------

@login_required
def partner_hinzufuegen(request, pk):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    person = get_object_or_404(Person, pk=pk)
    try:
        data = json.loads(request.body)
        partner_id = int(data['partner_id'])
    except (KeyError, ValueError, json.JSONDecodeError):
        return JsonResponse({'error': 'Ungültige Daten'}, status=400)
    if partner_id == pk:
        return JsonResponse({'error': 'Person kann nicht eigener Partner sein'}, status=400)
    partner = get_object_or_404(Person, pk=partner_id)
    existing = Ehe.objects.filter(
        Q(partner1=person, partner2=partner) | Q(partner1=partner, partner2=person)
    ).first()
    if existing:
        return JsonResponse({'error': 'Bereits eingetragen', 'ehe_pk': existing.pk}, status=400)
    ehe = Ehe.objects.create(partner1=person, partner2=partner)
    return JsonResponse({
        'ok': True, 'ehe_pk': ehe.pk,
        'partner_name': partner.vollname,
        'partner_url': partner.get_absolute_url(),
    })


@login_required
def partner_entfernen(request, pk, ehe_pk):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    get_object_or_404(Person, pk=pk)
    ehe = get_object_or_404(Ehe, pk=ehe_pk)
    if ehe.partner1_id != pk and ehe.partner2_id != pk:
        return JsonResponse({'error': 'Nicht berechtigt'}, status=403)
    ehe.delete()
    return JsonResponse({'ok': True})


def personen_suche_api(request):
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse([], safe=False)
    exclude_pk = request.GET.get('exclude')
    qs = Person.objects.filter(Q(nachname__icontains=q) | Q(vornamen__icontains=q))
    if exclude_pk:
        try:
            qs = qs.exclude(pk=int(exclude_pk))
        except ValueError:
            pass
    return JsonResponse([
        {'id': p.pk, 'name': p.vollname} for p in qs[:10]
    ], safe=False)


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
    for ort_obj in Ort.objects.filter(name__in=alle_orte):
        ort_coords[ort_obj.name] = {
            'lat': ort_obj.lat, 'lon': ort_obj.lon,
            'geocodiert': ort_obj.lat is not None,
        }

    result = []
    for name in alle_orte:
        coords = ort_coords.get(name, {'lat': None, 'lon': None, 'geocodiert': False})
        in_geburt = name in orte_geburt
        in_tod = name in orte_tod
        if in_geburt and in_tod:
            result.append({
                'ort': name, 'typ': 'both',
                'personen_geburt': orte_geburt[name]['personen'],
                'personen_tod': orte_tod[name]['personen'],
                **coords,
            })
        elif in_geburt:
            result.append({**orte_geburt[name], **coords})
        else:
            result.append({**orte_tod[name], **coords})
    return JsonResponse(result, safe=False)


def geocode_ort(request, ort_name):
    ort, created = Ort.objects.get_or_create(name=ort_name)
    force = request.GET.get('force') == '1'
    if ort.lat is None or force:
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
                ort.geo_problem = False
                ort.save()
            else:
                # Nominatim returned no result → mark as problem
                ort.geo_problem = True
                ort.save()
        except Exception:
            pass
    return JsonResponse({'lat': ort.lat, 'lon': ort.lon, 'name': ort.name,
                         'geo_problem': ort.geo_problem})


@login_required
def ort_koordinaten_update(request, ort_name):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        data = json.loads(request.body)
        lat = float(data['lat'])
        lon = float(data['lon'])
    except (KeyError, ValueError, json.JSONDecodeError):
        return JsonResponse({'error': 'Ungültige Koordinaten'}, status=400)
    ort, _ = Ort.objects.get_or_create(name=ort_name)
    ort.lat = lat
    ort.lon = lon
    ort.geocodiert_am = timezone.now()
    ort.geo_problem = False
    ort.save()
    return JsonResponse({'ok': True, 'lat': ort.lat, 'lon': ort.lon})


@login_required
def ort_problem_toggle(request, ort_name):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    ort, _ = Ort.objects.get_or_create(name=ort_name)
    ort.geo_problem = not ort.geo_problem
    ort.save()
    return JsonResponse({'geo_problem': ort.geo_problem})


def konflikte(request):
    """Orte ohne Koordinaten oder mit geo_problem=True, gruppiert nach Person."""
    from django.db.models import Q

    # Alle Ort-Namen die als Geburts-/Sterbeort verwendet werden
    orte_in_use = {}
    for p in Person.objects.exclude(geburtsort='').only('pk', 'nachname', 'vornamen', 'geburtsort'):
        name = p.geburtsort.strip()
        if name:
            orte_in_use.setdefault(name, []).append({'person': p, 'art': 'Geburtsort'})
    for p in Person.objects.exclude(sterbeort='').only('pk', 'nachname', 'vornamen', 'sterbeort'):
        name = p.sterbeort.strip()
        if name:
            orte_in_use.setdefault(name, []).append({'person': p, 'art': 'Sterbeort'})

    # Bekannte Orte aus DB
    ort_db = {o.name: o for o in Ort.objects.filter(name__in=orte_in_use.keys())}

    # Konflikte aufbauen
    conflicts = []
    for ort_name, eintraege in sorted(orte_in_use.items()):
        ort = ort_db.get(ort_name)
        hat_koordinaten = ort and ort.lat is not None
        ist_problem = ort and ort.geo_problem

        if hat_koordinaten and not ist_problem:
            continue  # alles ok, überspringen

        status = 'problem' if ist_problem else 'keine_koordinaten'
        for e in eintraege:
            conflicts.append({
                'person': e['person'],
                'art': e['art'],
                'ort_name': ort_name,
                'ort': ort,
                'hat_koordinaten': hat_koordinaten,
                'status': status,
            })

    conflicts.sort(key=lambda c: (c['ort_name'], c['person'].nachname))
    return render(request, 'stammbaum/konflikte.html', {
        'conflicts': conflicts,
        'anzahl': len(conflicts),
    })


def konflikte_count(request):
    """Anzahl offener Konflikte für Badge in Navigation."""
    from django.db.models import Q
    orte_in_use = set()
    for p in Person.objects.exclude(geburtsort='').values_list('geburtsort', flat=True):
        orte_in_use.add(p.strip())
    for p in Person.objects.exclude(sterbeort='').values_list('sterbeort', flat=True):
        orte_in_use.add(p.strip())

    ok_orte = set(
        Ort.objects.filter(name__in=orte_in_use, lat__isnull=False, geo_problem=False)
        .values_list('name', flat=True)
    )
    problem_count = sum(
        1 for name in orte_in_use if name and name not in ok_orte
    )
    return JsonResponse({'count': problem_count})


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
# CSV-Export
# ---------------------------------------------------------------------------

def export_csv(request):
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = 'attachment; filename="stammbaum.csv"'
    response.write('﻿')  # UTF-8 BOM für Excel

    writer = csv.writer(response, delimiter=';')
    writer.writerow([
        'ID', 'ELKE-Nr', 'Nachname', 'Vornamen', 'Rufname', 'Titel',
        'Geschlecht', 'Geburtsdatum', 'Geburtsort', 'Sterbedatum', 'Sterbeort',
        'Konfession', 'Beruf', 'Vater', 'Mutter',
    ])

    for p in Person.objects.all().order_by('nachname', 'vornamen'):
        vater, mutter = p.get_eltern()
        writer.writerow([
            p.pk,
            p.elke_nr or '',
            p.nachname,
            p.vornamen,
            p.rufname,
            p.titel,
            p.get_geschlecht_display(),
            p.geburtsdatum.strftime('%d.%m.%Y') if p.geburtsdatum else '',
            p.geburtsort,
            p.sterbedatum.strftime('%d.%m.%Y') if p.sterbedatum else '',
            p.sterbeort,
            p.konfession,
            p.beruf,
            str(vater) if vater else '',
            str(mutter) if mutter else '',
        ])
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


# ---------------------------------------------------------------------------
# Verwandtschaftsgrad
# ---------------------------------------------------------------------------

def verwandtschaft(request):
    personen = Person.objects.all().order_by('nachname', 'vornamen')
    return render(request, 'stammbaum/verwandtschaft.html', {'personen': personen})


def verwandtschaft_api(request):
    a_id = request.GET.get('a')
    b_id = request.GET.get('b')
    if not a_id or not b_id:
        return JsonResponse({'error': 'Parameter a und b erforderlich'}, status=400)

    try:
        person_a = Person.objects.get(pk=int(a_id))
        person_b = Person.objects.get(pk=int(b_id))
    except (Person.DoesNotExist, ValueError):
        return JsonResponse({'error': 'Person nicht gefunden'}, status=404)

    def p_info(p):
        return {'id': p.pk, 'name': p.vollname,
                'lebensdaten': p.lebensdaten, 'url': p.get_absolute_url(),
                'geschlecht': p.geschlecht}

    if person_a.pk == person_b.pk:
        return JsonResponse({
            'gefunden': True,
            'grad': 'Dieselbe Person',
            'beschreibung': 'Beide Auswahlen zeigen auf dieselbe Person.',
            'person_a': p_info(person_a),
            'person_b': p_info(person_b),
            'gemeinsamer_vorfahre': None,
            'abstand_a': 0, 'abstand_b': 0,
            'pfad_a': [p_info(person_a)],
            'pfad_b': [p_info(person_b)],
        })

    # BFS upward from a given person; returns (dist_map, came_from)
    def bfs_ancestors(start):
        from collections import deque
        dist = {start.pk: 0}
        came_from = {start.pk: None}   # child_pk → parent_pk used
        q = deque([start])
        while q:
            p = q.popleft()
            try:
                e = p.elternschaft_kind
                for parent in filter(None, [e.vater, e.mutter]):
                    if parent.pk not in dist:
                        dist[parent.pk] = dist[p.pk] + 1
                        came_from[parent.pk] = p.pk
                        q.append(parent)
            except Elternschaft.DoesNotExist:
                pass
        return dist, came_from

    dist_a, came_a = bfs_ancestors(person_a)
    dist_b, came_b = bfs_ancestors(person_b)

    common = set(dist_a) & set(dist_b)
    if not common:
        return JsonResponse({
            'gefunden': False,
            'person_a': p_info(person_a),
            'person_b': p_info(person_b),
            'beschreibung': 'Keine bekannte Verwandtschaft — kein gemeinsamer Vorfahre gefunden.',
        })

    # Pick the closest common ancestor (minimum total hops)
    lca_pk = min(common, key=lambda pk: dist_a[pk] + dist_b[pk])
    lca = Person.objects.get(pk=lca_pk)
    da = dist_a[lca_pk]
    db = dist_b[lca_pk]

    # Reconstruct path from start upward to lca.
    # came_from_map[pk] = the child pk one step closer to start (BFS went upward,
    # so came_from[ancestor] = the node below it toward start).
    # Walk from lca down to start via came_from, then reverse.
    def build_path(start_pk, lca_pk, came_from_map):
        path = []
        cur = lca_pk
        while cur is not None:
            path.append(cur)
            if cur == start_pk:
                break
            cur = came_from_map.get(cur)
        path.reverse()  # start_pk first, lca_pk last
        return path

    path_a_pks = build_path(person_a.pk, lca_pk, came_a)
    path_b_pks = build_path(person_b.pk, lca_pk, came_b)

    # Fetch all persons on both paths in one query
    all_pks = list(set(path_a_pks + path_b_pks))
    persons_map = {p.pk: p for p in Person.objects.filter(pk__in=all_pks)}

    # Relationship naming
    gA, gB = person_a.geschlecht, person_b.geschlecht

    def cousin_name(grad, entfernt, g):
        base = 'Cousin' if g == 'M' else 'Cousine'
        s = f'{base} {grad}. Grades'
        if entfernt:
            s += f', {entfernt}× entfernt'
        return s

    def onkel_tante(g, n_grosz):
        # n_grosz=0 → Onkel/Tante, 1 → Großonkel/-tante, 2+ → N×Großonkel/-tante
        if n_grosz == 0:
            return 'Tante' if g == 'F' else 'Onkel'
        if n_grosz == 1:
            return 'Großtante' if g == 'F' else 'Großonkel'
        return f'Großtante/-onkel ({n_grosz}× groß)'

    def nichte_neffe(g, n_grosz):
        if n_grosz == 0:
            return 'Nichte' if g == 'F' else 'Neffe'
        if n_grosz == 1:
            return 'Großnichte' if g == 'F' else 'Großneffe'
        return f'Großnichte/-neffe ({n_grosz}× groß)'

    if da == 0 and db == 0:
        grad = 'Dieselbe Person'
    elif da == 0:
        # A ist direkter Vorfahre von B
        if db == 1:
            grad = 'Vater' if gA == 'M' else 'Mutter'
        elif db == 2:
            grad = 'Großvater' if gA == 'M' else 'Großmutter'
        elif db == 3:
            grad = 'Urgroßvater' if gA == 'M' else 'Urgroßmutter'
        else:
            grad = f'Vorfahr ({db} Generationen)'
    elif db == 0:
        # B ist direkter Vorfahre von A  →  A ist Nachfahre von B
        if da == 1:
            grad = 'Sohn' if gA == 'M' else 'Tochter'        # gA: A's Geschlecht
        elif da == 2:
            grad = 'Enkel' if gA == 'M' else 'Enkelin'
        elif da == 3:
            grad = 'Urenkel' if gA == 'M' else 'Urenkelin'
        else:
            grad = f'Nachfahre ({da} Generationen)'
    elif da == 1 and db == 1:
        grad = 'Geschwister'
    elif min(da, db) == 1:
        # Einer ist direktes Kind des LCA → Onkel/Tante- oder Neffe/Nichte-Linie
        # n_grosz: 0=Onkel/Tante, 1=Groß-, 2=2×Groß-, ...
        n_grosz = max(da, db) - 2   # da=1,db=2 → 0; da=1,db=3 → 1; da=3,db=1 → 1
        if da < db:
            # A näher am Vorfahren → A ist Onkel/Tante-Seite
            grad = onkel_tante(gA, n_grosz)
        else:
            # A weiter vom Vorfahren → A ist Neffe/Nichte-Seite
            grad = nichte_neffe(gA, n_grosz)
    else:
        # Cousins: min(da,db)-1 = Grad, |da-db| = Entfernung
        grad_zahl = min(da, db) - 1
        entfernt  = abs(da - db)
        grad = cousin_name(grad_zahl, entfernt, gA)   # gA: A's Geschlecht

    # Generationsabstand = Nettoabstand auf der Zeitachse (|da - db|)
    # Pfadlänge         = Gesamtzahl der Eltern-Kind-Schritte (da + db)
    gen_abstand = abs(da - db)
    beschreibung = (
        f'{person_a.vollname} ist {grad} von {person_b.vollname}'
        + (f' — gemeinsamer Vorfahre: {lca.vollname}' if da > 0 and db > 0 else '')
        + f' (Generationsabstand: {gen_abstand}, Pfadlänge: {da}↑+{db}↓={da+db}).'
    )

    return JsonResponse({
        'gefunden': True,
        'grad': grad,
        'beschreibung': beschreibung,
        'person_a': p_info(person_a),
        'person_b': p_info(person_b),
        'gemeinsamer_vorfahre': p_info(lca),
        'abstand_a': da,
        'abstand_b': db,
        'pfad_a': [p_info(persons_map[pk]) for pk in path_a_pks],
        'pfad_b': [p_info(persons_map[pk]) for pk in path_b_pks],
    })
