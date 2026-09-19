#!/usr/bin/env python3
"""Check an n8n workflow's sticky layout before pushing it.

Construction-time verification for canvas documentation, no render needed.

ERROR (exit 1):
  STICKY OVERLAP      two stickies collide side by side (nesting is a warn)
  TRUNCATION          sticky height < estimated text height (text clips)
  HEADER COLLISION    a node sits in a zone's title/body band
  ORPHAN NODE         a node is inside no zone sticky (zone layouts only)
  ROW NOT LEVEL       zones in one row have different tops or bottoms
  GUTTERS UNEVEN      gaps between zones in one row differ
  ROW OVERFLOW        a row-band spans more than 8 columns (wrap to another row)
  ROW GUTTERS UNEVEN  vertical gaps between rows differ, or too tight to route
warn:
  nested              one sticky fully inside another
  off-grid            sticky x/y/width/height not a multiple of 16
  tight clearance     node closer than MIN_CLEAR px to a zone edge
info:
  containment         sticky contains node (expected for zone backgrounds)

A "zone" is a sticky that contains at least one node. A "row" is a set of
zones whose tops are within ROW_TOL px. Orphan checks only run when the
workflow has at least one zone, so caption-style layouts are not penalised.

Usage: python layout_check.py <workflow.json> [--strict]
  --strict   promote off-grid and tight-clearance warns to errors
"""
import json, sys, math

NODE_W, NODE_H = 100, 100          # regular node footprint
LABEL_H = 40                       # node name label below the node
CHAR_PX = 9.5                      # px per char in a sticky at default zoom
LINE_PX = 22                       # px per wrapped line
H1_PX = 40                         # a '#'/'##' title line incl. spacing
PAD = 32                           # vertical padding inside a sticky
ROW_TOL = 40                       # zones within this top-y delta are one row
MIN_CLEAR = 32                     # node-to-zone-edge clearance
MAX_ROW_COLS = 8                   # a flow wider than this many columns wraps to a new row
MIN_ROW_GUTTER = 32                # vertical gap between rows: tight even stacking, still routable
GRID = 16

def is_sticky(n): return n["type"].endswith("stickyNote")

def box(n):
    x, y = n["position"]
    if is_sticky(n):
        p = n["parameters"]
        return x, y, p.get("width", 240), p.get("height", 160)
    return x, y, NODE_W, NODE_H

def overlap(a, b):
    ax, ay, aw, ah = a; bx, by, bw, bh = b
    return not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay)

def inside(inner, outer):
    ix, iy, iw, ih = inner; ox, oy, ow, oh = outer
    return ix >= ox and iy >= oy and ix + iw <= ox + ow and iy + ih <= oy + oh

def text_lines(content, width):
    """(lines, has_header): wrapped line count for the sticky body."""
    cpl = max(8, int((width - 32) / CHAR_PX))
    lines, header = 0, False
    for raw in content.split("\n"):
        if raw.startswith("#"):
            header = True; continue
        lines += 1 if raw == "" else max(1, math.ceil(len(raw) / cpl))
    return lines, header

def est_height(content, width):
    lines, header = text_lines(content, width)
    return lines * LINE_PX + PAD + (H1_PX if header else 0)

def band_height(content, width):
    """Height of the title+body band; nodes must sit below it."""
    stripped = content.strip()
    if not stripped:
        return 0
    lines, header = text_lines(content, width)
    # trailing blank lines are not a band
    body = [l for l in content.split("\n") if not l.startswith("#")]
    while body and body[-1].strip() == "": body.pop()
    if not body:
        return H1_PX + 16 if header else 0
    return est_height(content, width) - PAD + 24

def main():
    strict = "--strict" in sys.argv
    path = [a for a in sys.argv[1:] if not a.startswith("--")][0]
    wf = json.load(open(path, encoding="utf-8"))
    nodes = wf["nodes"]
    stickies = [n for n in nodes if is_sticky(n)]
    regular = [n for n in nodes if not is_sticky(n)]
    errors, warns, infos = [], [], []

    # sticky vs sticky
    for i in range(len(stickies)):
        for j in range(i + 1, len(stickies)):
            a, b = box(stickies[i]), box(stickies[j])
            if overlap(a, b):
                nested = inside(a, b) or inside(b, a)
                (warns if nested else errors).append(
                    f"{'nested' if nested else 'STICKY OVERLAP'}: '{stickies[i]['name']}' x '{stickies[j]['name']}'")

    # per sticky: truncation, grid, containment, header band, clearance
    zones = {}
    for s in stickies:
        sb = box(s); x, y, w, h = sb
        content = s["parameters"].get("content", "")
        need = est_height(content, w)
        if h < need:
            errors.append(f"TRUNCATION: '{s['name']}' height {h} < needs ~{need} (width {w})")
        if any(v % GRID for v in (x, y, w, h)):
            warns.append(f"off-grid: '{s['name']}' x={x} y={y} w={w} h={h} (use multiples of {GRID})")
        band = band_height(content, w)
        members = []
        for r in regular:
            rb = box(r)
            if overlap(sb, rb):
                members.append(r)
                infos.append(f"sticky '{s['name']}' contains node '{r['name']}'")
                rx, ry, rw, rh = rb
                if ry < y + band:
                    errors.append(f"HEADER COLLISION: node '{r['name']}' (y={ry}) sits in the text band of '{s['name']}' (band ends y={y + band})")
                if not inside(rb, sb):
                    errors.append(f"NODE CLIPPED: '{r['name']}' crosses the edge of '{s['name']}'")
                else:
                    clear = min(rx - x, (x + w) - (rx + rw), (y + h) - (ry + rh + LABEL_H))
                    if clear < MIN_CLEAR:
                        warns.append(f"tight clearance: '{r['name']}' is {clear}px from an edge of '{s['name']}' (want >= {MIN_CLEAR})")
        if members:
            zones[s["name"]] = (s, members)

    # orphans (only meaningful on a zone layout)
    if zones:
        covered = {r["name"] for _, m in zones.values() for r in m}
        for r in regular:
            if r["name"] not in covered:
                errors.append(f"ORPHAN NODE: '{r['name']}' is inside no zone")

    # row alignment across zones. group by member node y (the real row), not
    # sticky y: band height varies with body text, so same-row zones can have
    # different sticky tops.
    def zone_ny(s):
        return min(m["position"][1] for m in zones[s["name"]][1])
    rows = []
    for s in sorted((z[0] for z in zones.values()), key=zone_ny):
        for row in rows:
            if abs(zone_ny(row[0]) - zone_ny(s)) <= ROW_TOL:
                row.append(s); break
        else:
            rows.append([s])
    for row in rows:
        if len(row) < 2: continue
        row.sort(key=lambda n: n["position"][0])
        tops = {n["position"][1] for n in row}
        bottoms = {n["position"][1] + box(n)[3] for n in row}
        gutters = [row[i]["position"][0] - (row[i-1]["position"][0] + box(row[i-1])[2]) for i in range(1, len(row))]
        names = ", ".join(n["name"] for n in row)
        if len(tops) > 1:
            errors.append(f"ROW TOPS NOT LEVEL ({names}): {sorted(tops)}")
        if len(bottoms) > 1:
            errors.append(f"ROW BOTTOMS NOT LEVEL ({names}): {sorted(bottoms)} (unify heights)")
        if len(set(gutters)) > 1:
            errors.append(f"GUTTERS UNEVEN ({names}): {gutters}")

    # row-band width: a flow is "long" by how many columns it spans, not how many
    # nodes it holds (parallel branch lanes share columns). A band wider than
    # MAX_ROW_COLS wraps to the next row so it stays legible in one screenshot.
    for row in rows:
        cols = {round(m["position"][0] / GRID) for s in row for m in zones[s["name"]][1]}
        if len(cols) > MAX_ROW_COLS:
            names = ", ".join(s["name"] for s in row)
            errors.append(f"ROW OVERFLOW ({names}): {len(cols)} columns in one row (max {MAX_ROW_COLS}); wrap to another row")

    # even vertical spacing: gaps between consecutive row-bands must be uniform,
    # and wide enough that a wrap connector routes through open canvas, not text.
    if len(rows) > 1:
        bands = sorted((min(s["position"][1] for s in row),
                        max(s["position"][1] + box(s)[3] for s in row)) for row in rows)
        gaps = [bands[i][0] - bands[i - 1][1] for i in range(1, len(bands))]
        if any(g < MIN_ROW_GUTTER for g in gaps):
            errors.append(f"ROW GUTTER TIGHT: vertical gaps {gaps} (want >= {MIN_ROW_GUTTER} to route a wrap connector)")
        elif len(set(gaps)) > 1:
            errors.append(f"ROW GUTTERS UNEVEN: {gaps} (space rows evenly)")

    if strict:
        errors += [w for w in warns if w.startswith(("off-grid", "tight clearance"))]
        warns = [w for w in warns if not w.startswith(("off-grid", "tight clearance"))]

    print(f"stickies: {len(stickies)}  nodes: {len(regular)}  zones: {len(zones)}  rows: {sum(1 for r in rows if len(r) > 1)}")
    for e in errors: print("  ERROR:", e)
    for w in warns: print("  warn :", w)
    for i in infos[:3]: print("  info :", i)
    if len(infos) > 3: print(f"  info : ... +{len(infos) - 3} more containments")
    print("RESULT:", "FAIL" if errors else "ok")
    sys.exit(1 if errors else 0)

if __name__ == "__main__":
    main()
