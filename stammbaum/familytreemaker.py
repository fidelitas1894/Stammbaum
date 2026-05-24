"""
Adapted from adrienverge/familytreemaker (GPL)
https://github.com/adrienverge/familytreemaker

Changes vs. original:
- Person.graphviz() uses our colour scheme and adds URL/tooltip
- Family.populate_from_django() replaces the text-file parser
- Family.output_dot_string() returns a string instead of printing
- Single-parent households are supported
"""

import re
from io import StringIO


class Person:
    def __init__(self, pk, name, geschlecht, lebensdaten, url):
        self.pk          = pk
        self.id          = f'p{pk}'
        self.name        = name
        self.geschlecht  = geschlecht
        self.lebensdaten = lebensdaten
        self.url         = url
        self.households  = []
        self.parents     = []
        self.follow_kids = True

    def __str__(self):
        return self.name

    def graphviz(self):
        label = self.name.replace('"', '\\"')
        if self.lebensdaten:
            label += '\\n' + self.lebensdaten

        if self.geschlecht == 'M':
            fill, stroke = '#dbeafe', '#3b82f6'
        elif self.geschlecht == 'F':
            fill, stroke = '#fce7f3', '#ec4899'
        else:
            fill, stroke = '#f1f5f9', '#94a3b8'

        tooltip = self.name.replace('"', '\\"')
        if self.lebensdaten:
            tooltip += f' ({self.lebensdaten})'

        return (
            f'{self.id}['
            f'label="{label}",'
            f'shape=box,'
            f'style="filled,rounded",'
            f'fillcolor="{fill}",'
            f'color="{stroke}",'
            f'penwidth=1.5,'
            f'fontname="Helvetica",'
            f'fontsize=11,'
            f'URL="{self.url}",'
            f'tooltip="{tooltip}"'
            f']'
        )


class Household:
    def __init__(self):
        self.parents = []
        self.kids    = []
        self.id      = 0

    def __str__(self):
        return (
            'Family:\n'
            f'\tparents  = {", ".join(map(str, self.parents))}\n'
            f'\tchildren = {", ".join(map(str, self.kids))}'
        )

    def isempty(self):
        return len(self.parents) == 0 and len(self.kids) == 0


class Family:
    # Invisible node for spouse connector
    invisible_couple = (
        '[shape=circle,label="",'
        'height=0.08,width=0.08,'
        'style=filled,'
        'fillcolor="#f59e0b",'
        'color="#d97706"]'
    )
    # Invisible node for child distribution
    invisible_child = '[shape=point,label="",height=0.01,width=0.01]'

    def __init__(self):
        self.everybody  = {}   # pk (int) → Person
        self.households = []

    # ── Population from Django models ────────────────────────────────────────

    def populate_from_django(self, personen, ehen, elternschaften):
        # 1. Person nodes
        for p in personen:
            person = Person(
                pk          = p.pk,
                name        = p.vollname,
                geschlecht  = p.geschlecht,
                lebensdaten = p.lebensdaten,
                url         = f'/personen/{p.pk}/',
            )
            self.everybody[p.pk] = person

        # 2. Ehe-Lookup
        ehe_by_partners = {}
        for e in ehen:
            key = frozenset(filter(None, [e.partner1_id, e.partner2_id]))
            ehe_by_partners[key] = e.pk

        # 3. Households aus Elternschaften aufbauen
        hh_by_key = {}   # frozenset → Household

        for el in elternschaften:
            if el.kind_id not in self.everybody:
                continue

            kind = self.everybody[el.kind_id]
            key  = frozenset(filter(None, [el.vater_id, el.mutter_id]))

            if key not in hh_by_key:
                h = Household()
                for pid in (el.vater_id, el.mutter_id):
                    if pid and pid in self.everybody:
                        h.parents.append(self.everybody[pid])
                hh_by_key[key] = h
            else:
                h = hh_by_key[key]

            if kind not in h.kids:
                h.kids.append(kind)
            kind.parents = list(h.parents)

        # 4. Kinderlose Ehen als leere Households
        for e in ehen:
            key = frozenset(filter(None, [e.partner1_id, e.partner2_id]))
            if key not in hh_by_key:
                h = Household()
                for pid in (e.partner1_id, e.partner2_id):
                    if pid and pid in self.everybody:
                        h.parents.append(self.everybody[pid])
                if not h.isempty():
                    hh_by_key[key] = h

        # 5. Alle Households registrieren (nur mit 2 Eltern – wie Original)
        for h in hh_by_key.values():
            if len(h.parents) == 2:
                self._register_household(h)
            elif len(h.parents) == 1 and h.kids:
                # Einzelelternteil: als Degenerat-Household speichern
                h.id = len(self.households)
                self.households.append(h)
                p = h.parents[0]
                if h not in p.households:
                    p.households.append(h)

    def _register_household(self, h):
        h.id = len(self.households)
        self.households.append(h)
        for p in h.parents:
            if h not in p.households:
                p.households.append(h)

    # ── Hilfsfunktionen (nah am Original) ────────────────────────────────────

    def find_first_ancestor(self):
        """Person ohne Eltern mit den meisten Nachkommen."""
        candidates = [p for p in self.everybody.values() if not p.parents]
        if not candidates:
            return next(iter(self.everybody.values()), None)

        def count_desc(p, seen=None):
            seen = seen or set()
            if p.pk in seen:
                return 0
            seen.add(p.pk)
            return sum(1 + count_desc(k, seen) for h in p.households for k in h.kids)

        return max(candidates, key=lambda p: count_desc(p))

    def next_generation(self, gen):
        nxt, seen = [], set()
        for p in gen:
            if not p.follow_kids:
                continue
            for h in p.households:
                for kid in h.kids:
                    if kid.pk not in seen:
                        seen.add(kid.pk)
                        nxt.append(kid)
        return nxt

    @staticmethod
    def get_spouse(household, person):
        if len(household.parents) < 2:
            return None
        return (household.parents[1]
                if household.parents[0] == person
                else household.parents[0])

    # ── DOT-Ausgabe (wie Original, aber in StringIO) ──────────────────────────

    def _display_generation(self, gen, out):
        out.write('\t{ rank=same;\n')

        prev = None
        for p in gen:
            l = min(len(p.households), 2)   # max 2 Ehen

            if prev:
                sp = (Family.get_spouse(p.households[0], p)
                      if l > 1 else None)
                target = sp.id if sp else p.id
                out.write(f'\t\t{prev} -> {target} [style=invis];\n')

            if l == 0:
                prev = p.id
                continue

            # Linke Ehe(n)
            for i in range(l // 2):
                h = p.households[i]
                sp = Family.get_spouse(h, p)
                if sp:
                    out.write(f'\t\t{sp.id} -> h{h.id} -> {p.id};\n')
                    out.write(f'\t\th{h.id}{Family.invisible_couple};\n')

            # Rechte Ehe(n)
            for i in range(l // 2, l):
                h = p.households[i]
                sp = Family.get_spouse(h, p)
                if sp:
                    out.write(f'\t\t{p.id} -> h{h.id} -> {sp.id};\n')
                    out.write(f'\t\th{h.id}{Family.invisible_couple};\n')
                    prev = sp.id

        out.write('\t}\n')

        # Kindelmente: horizontale Verteilerknoten
        out.write('\t{ rank=same;\n')
        prev = None
        for p in gen:
            for h in p.households:
                if not h.kids:
                    continue
                if prev:
                    out.write(f'\t\t{prev} -> h{h.id}_0 [style=invis];\n')
                l = len(h.kids)
                if l % 2 == 0:
                    l += 1
                out.write('\t\t' + ' -> '.join(f'h{h.id}_{x}' for x in range(l)) + ';\n')
                for i in range(l):
                    out.write(f'\t\th{h.id}_{i}{Family.invisible_child};\n')
                    prev = f'h{h.id}_{i}'
        out.write('\t}\n')

        for p in gen:
            for h in p.households:
                if not h.kids:
                    continue
                mid = len(h.kids) // 2
                out.write(f'\t\th{h.id} -> h{h.id}_{mid};\n')
                i = 0
                for c in h.kids:
                    out.write(f'\t\th{h.id}_{i} -> {c.id};\n')
                    i += 1
                    if i == mid:
                        i += 1   # mittleren (Phantom-)Knoten überspringen

    def output_dot_string(self, ancestor):
        """Gibt den vollständigen DOT-Graphen als String zurück."""
        out = StringIO()
        out.write('digraph {\n')
        out.write('\tnode [shape=box];\n')
        out.write('\tedge [dir=none,color="#94a3b8"];\n')
        out.write('\tgraph [rankdir=TB,nodesep=0.7,ranksep=1.0,splines=ortho];\n\n')

        for p in self.everybody.values():
            out.write(f'\t{p.graphviz()};\n')
        out.write('\n')

        gen = [ancestor]
        while gen:
            self._display_generation(gen, out)
            gen = self.next_generation(gen)

        out.write('}\n')
        return out.getvalue()
