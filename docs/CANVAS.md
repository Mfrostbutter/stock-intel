# Canvas documentation

Every workflow in `workflows/` carries its own documentation on the n8n canvas: background
stickies that frame each stage, plus one or two panels that explain the workflow as a whole. The
stickies are generated from a spec file, never hand-placed, so the canvas and the repo cannot
drift. `scripts/deploy_workflow.py` refuses to deploy a workflow whose canvas does not pass the
check, which is the only thing keeping that true.

## Files

| Path | What it is |
|---|---|
| `workflows/<name>.json` | the workflow, nodes and generated stickies together |
| `workflows/zones/<name>.json` | the zone spec: which nodes belong to which stage, and the prose |
| `scripts/canvas/zone_layout.py` | generates the stickies from workflow + spec |
| `scripts/canvas/layout_check.py` | the gate: geometry, containment, row rules |
| `scripts/canvas/apply_layout.py` | places nodes from a `{name: [col, row]}` map, for a fresh layout |
| `scripts/canvas/respace.py` | re-pitches node columns when a zone no longer fits |

## Spec format

```json
{
  "gutter": 16,
  "panels": [
    {"title": "What this workflow is", "body": "markdown", "color": 6, "width": 720}
  ],
  "zones": [
    {"title": "Trigger", "body": "markdown", "color": 4,
     "nodes": ["Weekdays 06:00 ET", "Run webhook"]}
  ]
}
```

Every node in the workflow must appear in exactly one zone. An unlisted node is an orphan and
fails the check. Panels tile in a row above the canvas and belong to no zone.

## Layout rules the check enforces

| Rule | Value |
|---|---|
| Grid | every sticky x, y, width and height is a multiple of 16 |
| Column pitch | 336 px between nodes in a row |
| Max nodes per row | 8; a longer flow wraps to the next row |
| Row gutter | at least 128 px, so a wrap connector routes through open canvas |
| Rows | tops level, bottoms level, gutters even |
| Text band | no node inside a sticky's title and body band |
| Containment | no node clipped by a zone edge, no orphan node |

The 16 px grid is not taste: n8n snaps sticky positions to 16 px on save, so an 8 px gutter
becomes 4 and 20 as soon as anyone drags a node in the editor.

## Colour key

| Colour | Meaning |
|---|---|
| Green (4) | human touchpoints: webhook intake, approval, the notify-a-person zone |
| Red (3) | critical state: the store, the one node that must not fail |
| Blue (5) | logic and data: validate, enrich, score, route |
| Purple (6) | the overview panel |
| Gray (7) | documentation panels: conventions, quotas, runbook |
| Gold (2) | an outer frame around a frame. Rare |
| Yellow (1) | n8n's default. Avoid; it reads as unfinished |

## Changing a workflow

```bash
# 1. edit workflows/<name>.json (nodes, connections, parameters)
# 2. keep the zone spec in step: a new node needs a home
python scripts/canvas/zone_layout.py workflows/daily.json workflows/zones/daily.json
python scripts/canvas/layout_check.py workflows/daily.json      # must print RESULT: ok
python scripts/deploy_workflow.py workflows/daily.json --activate
```

`zone_layout.py` rewrites only the stickies; it never moves a node. If a new node pushes a row
past eight columns, drop it to the next row by giving it a lower `y` in the JSON and re-running
the generator, which levels and spaces the rows for you.

Two more rules the deploy script enforces for reasons that have nothing to do with the canvas:
every workflow carries at least one tag, and activation happens before the update, because
activating afterwards republishes the previously active version and silently discards the change.
