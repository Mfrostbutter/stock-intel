#!/usr/bin/env python3
"""Generate zone-background sticky notes for an n8n workflow from a zone spec.

Reads a workflow JSON and a zone spec, computes one sticky per zone that
frames its member nodes (header band + padding, snapped to the 16px grid),
levels zones that share a row, stacks multiple rows with a uniform vertical
gutter, left-aligns every row, and writes the workflow back with the stickies
replaced. Optional doc panels (setup, credits) tile in a row above.

Rows come from the node y you lay: put a stage's nodes at one y for a row, and
drop the next stage to a lower y to wrap it. Keep a row to <= 8 nodes so it
stays legible in one screenshot (layout_check enforces this). The vertical
gutter between rows is intentionally wider than the horizontal gutter so a wrap
connector routes through open canvas, outside every sticky and its text.

Spec (JSON):
{
  "gutter": 16,
  "zones": [
    {"title": "Trigger", "body": "Runs daily...", "color": 4,
     "nodes": ["Schedule Trigger", "Webhook"]},
    ...
  ],
  "panels": [
    {"title": "Setup", "body": "1. ...\\n2. ...", "color": 6, "width": 600}
  ]
}

Usage: python zone_layout.py <workflow.json> <spec.json> [-o out.json]
"""
import json, sys, math, argparse

GRID = 16
NODE_W, NODE_H = 100, 100       # regular node footprint
LABEL_H = 40                    # node name label below the node
CHAR_PX = 9.5                   # px per char at default zoom
LINE_PX = 22
H1_PX = 40                      # '#'/'##' title line incl. spacing
PAD_X = 64                      # node-to-zone-edge clearance, left/right
PAD_TOP_TITLE_ONLY = 96         # title-only zone: band above first node row
BAND_GAP = 40                   # air between the last body line and the node row
PAD_BOTTOM = 64                 # clearance under the lowest node label
MIN_W = 304                     # narrower zones wrap body text into a column
PANEL_MIN_H = 256
ROW_GUTTER = 48                 # vertical gutter between rows: tight + even, still a wrap-connector lane

def snap(v, up=True):
    f = math.ceil if up else math.floor
    return int(f(v / GRID) * GRID)

def text_height(title, body, width):
    """Estimated px the header band needs for title + body at this width."""
    cpl = max(8, int((width - 32) / CHAR_PX))
    lines = 0
    for raw in (body or "").split("\n"):
        lines += 1 if raw == "" else max(1, math.ceil(len(raw) / cpl))
    return H1_PX + lines * LINE_PX + (16 if body else 0)

def content(title, body):
    return f"## {title}\n\n{body}".rstrip() if body else f"## {title}"

def sticky(name, x, y, w, h, title, body, color):
    return {
        "parameters": {"content": content(title, body), "width": w, "height": h, "color": color},
        "id": f"zone-{name}", "name": f"zone-{name}",
        "type": "n8n-nodes-base.stickyNote", "typeVersion": 1, "position": [x, y],
    }

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("workflow"); ap.add_argument("spec"); ap.add_argument("-o", "--out")
    a = ap.parse_args()
    wf = json.load(open(a.workflow, encoding="utf-8"))
    spec = json.load(open(a.spec, encoding="utf-8"))
    G = int(spec.get("gutter", GRID))
    by_name = {n["name"]: n for n in wf["nodes"] if not n["type"].endswith("stickyNote")}

    zones = []
    for i, z in enumerate(spec["zones"]):
        members = [by_name[n] for n in z["nodes"] if n in by_name]
        missing = [n for n in z["nodes"] if n not in by_name]
        if missing:
            print(f"  warn : zone '{z['title']}' references unknown nodes {missing}", file=sys.stderr)
        if not members:
            continue
        xs = [n["position"][0] for n in members]; ys = [n["position"][1] for n in members]
        left, right = min(xs), max(xs) + NODE_W
        top, bottom = min(ys), max(ys) + NODE_H + LABEL_H
        w = max(MIN_W, snap(right - left + 2 * PAD_X))
        band = text_height(z["title"], z.get("body", ""), w) + BAND_GAP if z.get("body") else PAD_TOP_TITLE_ONLY
        band = max(band, PAD_TOP_TITLE_ONLY)
        x = snap(left - PAD_X, up=False)
        y = snap(top - band, up=False)
        w = max(MIN_W, snap(right + PAD_X - x))
        h = snap(bottom + PAD_BOTTOM - y)
        zones.append({"i": i, "z": z, "x": x, "y": y, "w": w, "h": h, "members": members})

    # group zones into rows by their member NODES' y, not the sticky y: band
    # height varies with body text, so two zones on the same node row can have
    # very different sticky tops. Node y is the real row.
    for zn in zones:
        zn["ny"] = min(n["position"][1] for n in zn["members"])
    rows = []
    for zn in sorted(zones, key=lambda d: d["ny"]):
        for row in rows:
            if abs(row[0]["ny"] - zn["ny"]) <= 64:
                row.append(zn); break
        else:
            rows.append([zn])
    for row in rows:
        y = min(d["y"] for d in row)
        bottom = max(d["y"] + d["h"] for d in row)
        for d in row:
            d["y"] = y; d["h"] = snap(bottom - y)
        # cascade x so gutters equal G, anchored on the leftmost zone;
        # member nodes ride along so clearance is preserved
        row.sort(key=lambda d: d["x"])
        for prev, cur in zip(row, row[1:]):
            want = prev["x"] + prev["w"] + G
            shift = want - cur["x"]
            if shift:
                cur["x"] = want
                for n in cur["members"]:
                    n["position"][0] += shift

    # stack rows top-to-bottom with a uniform vertical gutter, and left-align
    # every row to a common left edge. members ride with their row.
    RG = int(spec.get("row_gutter", ROW_GUTTER))
    rows.sort(key=lambda r: min(d["y"] for d in r))
    left = min(d["x"] for d in zones)
    prev_bottom = None
    for row in rows:
        dx = left - min(d["x"] for d in row)
        row_top = min(d["y"] for d in row)
        dy = 0 if prev_bottom is None else (prev_bottom + RG) - row_top
        for d in row:
            d["x"] += dx; d["y"] += dy
            for n in d["members"]:
                n["position"][0] += dx; n["position"][1] += dy
        prev_bottom = max(d["y"] + d["h"] for d in row)

    out = [sticky(f"{d['i']+1:02d}", d["x"], d["y"], d["w"], d["h"],
                  d["z"]["title"], d["z"].get("body", ""), d["z"].get("color", 7)) for d in zones]

    # doc panels: one row above the top zone row, same gutter
    panels = spec.get("panels", [])
    if panels and zones:
        row_top = min(d["y"] for d in zones)
        left = min(d["x"] for d in zones)
        heights = []
        for p in panels:
            w = snap(p.get("width", 560))
            heights.append(snap(max(PANEL_MIN_H, text_height(p["title"], p.get("body", ""), w) + 48)))
        ph = max(heights)
        x = left
        for k, p in enumerate(panels):
            w = snap(p.get("width", 560))
            out.append(sticky(f"panel-{k+1}", x, row_top - G - ph, w, ph,
                              p["title"], p.get("body", ""), p.get("color", 7)))
            x += w + G

    wf["nodes"] = [n for n in wf["nodes"] if not n["type"].endswith("stickyNote")] + out
    dest = a.out or a.workflow
    json.dump(wf, open(dest, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    for s in out:
        p = s["parameters"]
        print(f"  {s['name']:<10} x={s['position'][0]:<6} y={s['position'][1]:<6} w={p['width']:<5} h={p['height']}")
    print(f"wrote {dest}: {len(out)} stickies")

if __name__ == "__main__":
    main()
