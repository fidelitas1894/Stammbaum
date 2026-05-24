"""
Sugiyama-Layout für genealogischen DAG.

Knotentypen im Graphen:
  p_{id}   – Person
  f_{id}   – Ehe (Familienknoten, aus Ehe-Tabelle)
  vf_{id}  – Virtueller Familienknoten (Elternschaft ohne Ehe-Datensatz)
  d_{n}    – Dummy-Knoten für Kanten über mehrere Lagen

Kantenrichtung: immer von älterer zu jüngerer Generation.
  Partner → Familienknoten → Kind
"""

from collections import defaultdict

# SVG-Maße (Pixel)
NODE_W = 140
NODE_H = 50
FAM_W  = 14   # Familienknoten (kleines Quadrat)
FAM_H  = 14
H_GAP  = 50
V_GAP  = 90


# ─── 1. Graph aufbauen ────────────────────────────────────────────────────────

def _build_graph(personen, ehen, elternschaften):
    nodes = {}   # node_id → meta-dict
    edges = set()

    for p in personen:
        nodes[f"p_{p.pk}"] = {
            'type': 'person',
            'label': p.vollname,
            'db_id': p.pk,
            'geschlecht': p.geschlecht,
        }

    ehe_by_partners = {}
    for e in ehen:
        fid = f"f_{e.pk}"
        nodes[fid] = {'type': 'family', 'label': '', 'db_id': e.pk}
        key = frozenset(filter(None, [e.partner1_id, e.partner2_id]))
        ehe_by_partners[key] = fid
        for pid in (e.partner1_id, e.partner2_id):
            if pid and f"p_{pid}" in nodes:
                edges.add((f"p_{pid}", fid))

    for el in elternschaften:
        kind_nid = f"p_{el.kind_id}"
        if kind_nid not in nodes:
            continue

        fam_nid = None
        if el.vater_id or el.mutter_id:
            key = frozenset(filter(None, [el.vater_id, el.mutter_id]))
            fam_nid = ehe_by_partners.get(key)

        if fam_nid is None:
            # Virtueller Familienknoten – einmalig pro Kind
            vfid = f"vf_{el.kind_id}"
            if vfid not in nodes:
                nodes[vfid] = {'type': 'family', 'label': '', 'db_id': None}
                for pid in (el.vater_id, el.mutter_id):
                    if pid and f"p_{pid}" in nodes:
                        edges.add((f"p_{pid}", vfid))
            fam_nid = vfid

        edges.add((fam_nid, kind_nid))

    return nodes, list(edges)


# ─── 2. Layering: Longest-Path von den Wurzeln ───────────────────────────────

def _longest_path_layering(node_ids, edges):
    out_adj = defaultdict(list)
    in_adj  = defaultdict(list)
    for s, t in edges:
        out_adj[s].append(t)
        in_adj[t].append(s)

    # Kahn-Algorithmus (Zyklen werden ignoriert – Knoten bleiben bei Lage 0)
    in_degree = {n: len(in_adj[n]) for n in node_ids}
    queue = [n for n in node_ids if in_degree[n] == 0]
    topo = []
    while queue:
        n = queue.pop(0)
        topo.append(n)
        for m in out_adj[n]:
            in_degree[m] -= 1
            if in_degree[m] == 0:
                queue.append(m)
    # Knoten in Zyklen hinten anhängen
    topo_set = set(topo)
    for n in node_ids:
        if n not in topo_set:
            topo.append(n)

    layers = {n: 0 for n in node_ids}
    for n in topo:
        for m in out_adj[n]:
            if layers[m] < layers[n] + 1:
                layers[m] = layers[n] + 1

    return layers


# ─── 3. Dummy-Knoten für lange Kanten ────────────────────────────────────────

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

    return layers_dict


# ─── 5. Koordinaten zuweisen ──────────────────────────────────────────────────

def _assign_coords(layers_dict, nodes):
    """Gibt {node_id: (x, y)} zurück – alle Werte ≥ 0."""
    coords = {}
    for l, layer_nodes in layers_dict.items():
        x_cursor = 0.0
        for nid in layer_nodes:
            ntype = nodes[nid]['type']
            w = FAM_W if ntype == 'family' else NODE_W
            y = l * (NODE_H + V_GAP)
            coords[nid] = (x_cursor + w / 2.0, y + NODE_H / 2.0)
            x_cursor += w + H_GAP

    if not coords:
        return coords

    min_x = min(x for x, y in coords.values())
    min_y = min(y for x, y in coords.values())
    return {
        n: (round(x - min_x + H_GAP, 2), round(y - min_y + V_GAP, 2))
        for n, (x, y) in coords.items()
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

    personen       = list(personen       or Person.objects.all())
    ehen           = list(ehen           or Ehe.objects.all())
    elternschaften = list(
        elternschaften
        or Elternschaft.objects.select_related('kind', 'vater', 'mutter').all()
    )

    nodes, edges = _build_graph(personen, ehen, elternschaften)
    if not nodes:
        return {'nodes': [], 'edges': [], 'width': 0, 'height': 0}

    layers = _longest_path_layering(list(nodes), edges)
    nodes, edges, layers = _insert_dummies(nodes, edges, layers)

    layers_dict = defaultdict(list)
    for nid, l in layers.items():
        layers_dict[l].append(nid)

    _minimize_crossings(layers_dict, edges)
    coords = _assign_coords(layers_dict, nodes)

    out_nodes = []
    for nid, meta in nodes.items():
        if nid not in coords:
            continue
        x, y = coords[nid]
        entry = {'id': nid, 'type': meta['type'], 'x': x, 'y': y}
        if meta['type'] != 'dummy':
            entry['label']  = meta.get('label', '')
            entry['db_id']  = meta.get('db_id')
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
