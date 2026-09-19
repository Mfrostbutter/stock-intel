#!/usr/bin/env python3
"""Respace workflow node columns to a fixed pitch so one-node zones (min 304px) fit with a 16px gutter."""
import json, sys
path, pitch = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 336
wf = json.load(open(path, encoding="utf-8"))
nodes = [n for n in wf["nodes"] if not n["type"].endswith("stickyNote")]
cols = sorted({n["position"][0] for n in nodes})
xmap = {x: i * pitch for i, x in enumerate(cols)}
for n in nodes:
    n["position"][0] = xmap[n["position"][0]]
wf["nodes"] = nodes  # drop stickies; the generator re-adds them
json.dump(wf, open(path, "w", encoding="utf-8"), indent=2)
print(f"{path}: {len(cols)} columns at pitch {pitch}")
