#!/usr/bin/env python3
"""Place nodes on a 336 x 260 grid from a {name: [col, row]} map and strip stickies. Run before zone_layout.py."""
import json, sys
wf_path, layout_path = sys.argv[1], sys.argv[2]
wf = json.load(open(wf_path, encoding="utf-8"))
layout = json.load(open(layout_path, encoding="utf-8"))
nodes = [n for n in wf["nodes"] if not n["type"].endswith("stickyNote")]
for n in nodes:
    col, row = layout[n["name"]]
    n["position"] = [col * 336, row * 260]
wf["nodes"] = nodes
json.dump(wf, open(wf_path, "w", encoding="utf-8"), indent=2)
print(f"{wf_path}: placed {len(nodes)} nodes")
