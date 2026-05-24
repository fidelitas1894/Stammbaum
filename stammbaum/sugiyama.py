"""
Sugiyama-Layout für genealogischen DAG.

Kernprinzip:
  - Generationen werden NUR aus Eltern-Kind-Beziehungen berechnet.
  - Partnerbeziehungen beeinflussen das Layer-Ranking NICHT.
  - Personen liegen auf geraden Lagen (0, 2, 4, …),
    Familienknoten auf ungeraden Lagen (1, 3, 5, …) dazwischen.

Knotentypen:
  p_{id}   – Person
  f_{id}   – Ehe-Familienknoten
  vf_{id}  – Virtueller Familienknoten (Elternschaft ohne Ehe-Eintrag)
  d_{n}    – Dummy-Knoten für Kanten über mehrere Lagen
"""

from collections import defaultdict

NODE_W = 140
NODE_H = 50
FAM_W  = 14
FAM_H  = 14
H_GAP  = 50
V_GAP  = 90


# ─── 1. Generationsnummern aus Eltern-Kind-Beziehungen ───────────────────────

def _compute_generations(person_pks, elternschaften):
    """
    Longest-Path-Generationen, ausschließlich über Eltern-Kind-Kanten.
    Gibt gen[pk] → int und children_of[pk] → [pk, ...] zurück.
    """
    children_of = defaultdict(list)
    parents_of  = defaultdict(list)

    for el in elternschaften:
        if el.kind_id not in person_pks:
            continue
        for parent_id in (el.vater_id, el.mutter_id):
            if parent_id and parent_id in person_pks:
                children_of[parent_id].append(el.kind_id)
                parents_of[el.kind_id].append(parent_id)

    # Kahn-Topologiesort
    in_deg = {pk: len(parents_of[pk]) for pk in person_pks}
    queue  = [pk for pk in person_pks if in_deg[pk] == 0]
    topo   = []
    while queue:
        pk = queue.pop(0)
        topo.append(pk)
        for child_pk in children_of[pk]:
            in_deg[child_pk] -= 1
            if in_deg[child_pk] == 0:
                queue.append(child_pk)
    # Zyklen-Reste anfügen (sollte bei echten Familiendaten nicht vorkommen)
    topo_set = set(topo)
    for pk in person_pks:
        if pk not in topo_set:
            topo.append(pk)

    gen = {pk: 0 for pk in person_pks}
    for pk in topo:
        for child_pk in children_of[pk]:
            if gen[child_pk] < gen[pk] + 1:
                gen[child_pk] = gen[pk] + 1

    return gen, children_of


# ─── 2. Graph + vordefinierte Layer aufbauen ─────────────────────────────────

def _build_graph(personen, ehen, elternschaften):
    """
    Gibt (nodes, edges, layers) zurück.
    layers ist von Anfang an vollständig befüllt (kein separater Layering-Schritt nötig).
    """
    person_pks = {p.pk for p in personen}
    gen, children_of = _compute_generations(person_pks, elternschaften)

    nodes  = {}
    layers = {}
    edges  = set()

    # Personenknoten
    for p in personen:
        nid = f"p_{p.pk}"
        nodes[nid]  = {'type': 'person', 'label': p.vollname,
                        'db_id': p.pk, 'geschlecht': p.geschlecht}
        layers[nid] = 2 * gen[p.pk]

    # Ehe-Lookup
    ehe_by_partners = {}
    for e in ehen:
        key = frozenset(filter(None, [e.partner1_id, e.partner2_id]))
        ehe_by_partners[key] = e

    # Elternschaften → Familienknoten + Kanten
    processed_fam = set()
    for el in elternschaften:
        kind_nid = f"p_{el.kind_id}"
        if kind_nid not in nodes:
            continue

        # Passenden Familienknoten finden oder anlegen
        key = frozenset(filter(None, [el.vater_id, el.mutter_id]))
        ehe = ehe_by_partners.get(key)

        if ehe is not None:
            fam_nid = f"f_{ehe.pk}"
            db_id   = ehe.pk
        else:
            fam_nid = f"vf_{el.kind_id}"
            db_id   = None

        # Familienknoten-Layer: ungerade Lage zwischen Eltern und Kind
        parent_gens = [gen[pid] for pid in (el.vater_id, el.mutter_id)
                       if pid and pid in gen]
        fam_layer = 2 * max(parent_gens) + 1 if parent_gens else 1

        if fam_nid not in nodes:
            nodes[fam_nid]  = {'type': 'family', 'label': '', 'db_id': db_id}
            layers[fam_nid] = fam_layer
        else:
            # Mehrere Kinder desselben Paars: Layer bleibt unverändert (konsistent)
            layers[fam_nid] = max(layers[fam_nid], fam_layer)

        # Kanten Partner → Familienknoten (einmalig)
        if fam_nid not in processed_fam:
            for pid in (el.vater_id, el.mutter_id):
                if pid and f"p_{pid}" in nodes:
                    edges.add((f"p_{pid}", fam_nid))
            processed_fam.add(fam_nid)

        # Kante Familienknoten → Kind
        edges.add((fam_nid, kind_nid))

    # Kinderlose Ehen (haben keinen Eintrag in Elternschaft)
    for e in ehen:
        fid = f"f_{e.pk}"
        if fid not in nodes:
            p1_gen = gen.get(e.partner1_id, 0) if e.partner1_id else 0
            p2_gen = gen.get(e.partner2_id, 0) if e.partner2_id else 0
            nodes[fid]  = {'type': 'family', 'label': '', 'db_id': e.pk}
            layers[fid] = 2 * max(p1_gen, p2_gen) + 1
            for pid in (e.partner1_id, e.partner2_id):
                if pid and f"p_{pid}" in nodes:
                    edges.add((f"p_{pid}", fid))

    return nodes, list(edges), layers


# ─── 3. Dummy-Knoten für Kanten über mehrere Lagen ───────────────────────────

def _insert_dummies(nodes, edges, layers):
    new_nodes  = dict(nodes)
    new_layers = dict(layers)
    new_edges  = []
    idx = 0

    for s, t in edges:
        span = layers[t] - layers[s]
        if span <= 1:
            new_edges.append((s, t))
            continue
        if span <= 0:
            # Rückwärtskante (Datenfehler / Zyklus) – überspringen
            continue
        prev = s
        for step in range(1, span):
            did = f"d_{idx}"
            idx += 1
            new_nodes[did]  = {'type': 'dummy'}
            new_layers[did] = layers[s] + step
            new_edges.append((prev, did))
            prev = did
        new_edges.append((prev, t))

    return new_nodes, new_edges, new_layers


# ─── 4. Kreuzungsminimierung: Median-Heuristik ───────────────────────────────

def _minimize_crossings(layers_dict, edges, max_iter=4):
    pos = {}
    for layer_nodes in layers_dict.values():
        for i, n in enumerate(layer_nodes):
            pos[n] = float(i)

    above = defaultdict(list)
    below = defaultdict(list)
    for s, t in edges:
        if s in pos and t in pos:
            below[s].append(t)
            above[t].append(s)

    def median(node, nbr_dict):
        vals = sorted(pos[nb] for nb in nbr_dict[node] if nb in pos)
        if not vals:
            return pos.get(node, 0.0)
        m = len(vals)
        return vals[m // 2] if m % 2 else (vals[m // 2 - 1] + vals[m // 2]) / 2.0

    num_layers = max(layers_dict, default=-1) + 1
    for _ in range(max_iter):
        for l in range(1, num_layers):
            if l not in layers_dict:
                continue
            layers_dict[l].sort(key=lambda n: median(n, above))
            for i, n in enumerate(layers_dict[l]):
                pos[n] = float(i)
        for l in range(num_layers - 2, -1, -1):
            if l not in layers_dict:
                continue
            layers_dict[l].sort(key=lambda n: median(n, below))
            for i, n in enumerate(layers_dict[l]):
                pos[n] = float(i)

    return layers_dict, pos


# ─── 5. Koordinaten zuweisen ─────────────────────────────────────────────────

def _assign_coords(layers_dict, layers, nodes, edges):
    """
    Phase A: Gleichmäßige x-Verteilung (Reihenfolge aus Kreuzungsminimierung).
    Phase B: Baryzentrische Zentrierung – bewegt jeden Knoten zur Mitte seiner
             Nachbarn, aber nur soweit, bis er den Mindestabstand zum linken
             bzw. rechten Nachbarn in der gleichen Lage unterschreitet.
             Keine separate Überlappungs-Korrektur nötig.
    """
    num_layers = max(layers_dict, default=-1) + 1

    x = {}
    for l, layer_nodes in layers_dict.items():
        for i, nid in enumerate(layer_nodes):
            x[nid] = float(i * (NODE_W + H_GAP))

    above = defaultdict(list)
    below = defaultdict(list)
    for s, t in edges:
        if s in x and t in x:
            below[s].append(t)
            above[t].append(s)

    def barycenter(nid, nbr_dict):
        vals = [x[nb] for nb in nbr_dict[nid] if nb in x]
        return sum(vals) / len(vals) if vals else None

    def shift_within_bounds(layer_nodes):
        n = len(layer_nodes)
        for i, nid in enumerate(layer_nodes):
            target = barycenter(nid, above) or barycenter(nid, below)
            if target is None:
                continue
            lo = x[layer_nodes[i - 1]] + NODE_W + H_GAP if i > 0     else float('-inf')
            hi = x[layer_nodes[i + 1]] - NODE_W - H_GAP if i < n - 1 else float('inf')
            x[nid] = max(lo, min(hi, target))

    for _ in range(6):
        for l in range(1, num_layers):
            if l in layers_dict:
                shift_within_bounds(layers_dict[l])
        for l in range(num_layers - 2, -1, -1):
            if l in layers_dict:
                shift_within_bounds(layers_dict[l])

    y = {nid: layers[nid] * (NODE_H + V_GAP) + NODE_H / 2.0 for nid in x}

    if not x:
        return {}
    min_x = min(x.values())
    min_y = min(y.values())
    return {
        nid: (round(x[nid] - min_x + H_GAP, 2), round(y[nid] - min_y + V_GAP, 2))
        for nid in x
    }


# ─── Öffentliche API ──────────────────────────────────────────────────────────

def compute_sugiyama_layout(personen=None, ehen=None, elternschaften=None):
    """
    Berechnet Sugiyama-Layout für den genealogischen DAG.

    Rückgabe:
      {
        'nodes': [{'id', 'type', 'label', 'db_id', 'x', 'y'}, ...],
        'edges': [{'source', 'target'}, ...],
        'width': int,
        'height': int,
      }
    """
    from .models import Person, Ehe, Elternschaft

    personen       = list(personen or Person.objects.all())
    ehen           = list(ehen     or Ehe.objects.all())
    elternschaften = list(
        elternschaften
        or Elternschaft.objects.select_related('kind', 'vater', 'mutter').all()
    )

    if not personen:
        return {'nodes': [], 'edges': [], 'width': 0, 'height': 0}

    nodes, edges, layers = _build_graph(personen, ehen, elternschaften)
    nodes, edges, layers = _insert_dummies(nodes, edges, layers)

    layers_dict = defaultdict(list)
    for nid, l in layers.items():
        layers_dict[l].append(nid)

    layers_dict, _ = _minimize_crossings(layers_dict, edges)
    coords = _assign_coords(layers_dict, layers, nodes, edges)

    out_nodes = []
    for nid, meta in nodes.items():
        if nid not in coords:
            continue
        x, y = coords[nid]
        entry = {'id': nid, 'type': meta['type'], 'x': x, 'y': y}
        if meta['type'] != 'dummy':
            entry['label'] = meta.get('label', '')
            entry['db_id'] = meta.get('db_id')
            if meta['type'] == 'person':
                entry['geschlecht'] = meta.get('geschlecht', 'U')
        out_nodes.append(entry)

    out_edges = [{'source': s, 'target': t} for s, t in edges]

    max_x = max((n['x'] for n in out_nodes), default=0)
    max_y = max((n['y'] for n in out_nodes), default=0)

    return {
        'nodes': out_nodes,
        'edges': out_edges,
        'width':  int(max_x + NODE_W + H_GAP),
        'height': int(max_y + NODE_H + V_GAP),
    }
