# G4 audit — plantar seam versus the lowest centerline point

This note is an interpretation of the frozen G4 check. It does not change
`validation/forefoot_cura_gates/`. The frozen result stands on its own.

## What the frozen check measures

G4 takes each closed forefoot loop and builds `bottom_edge` from the loop's
own centerline. The bottom is the X interval that contains the lowest
centerline point, grown by `BOT_TOL` (0.25 mm). A seam passes when the loop
start is within 0.15 mm of that interval. A layer change passes when the
first Z travel is within 0.55 mm of it and within 0.05 mm in X and 0.50 mm
in Y of the BASE station.

The interval is the one that contains the minimum Y. It is not the longest
low wall. After the plantar wall's centerline moves inward, a short corner
that did not move can become that minimum, and the long wall drops out of
the interval. The distance G4 then reports is the sideways distance from the
seam to the corner, not a measurement of the seam leaving the plantar face.

## What eb85d62 actually did

On commit `eb85d62` the orthocad candidate failed G4 with 3 closed starts and
3 layer changes. `starts_moved` was 0. For every one of the three layer
changes, `dx` and `dy` were 0: the travel point itself was the BASE point.

| Layer | BASE start | Candidate start | G4 distance | Layer-change point | dx, dy |
| --- | --- | --- | --- | --- | --- |
| 110 | (174.053, 0.100) | (174.053, 0.269) | 45.936 mm | (174.053, 0.500) | 0, 0 |
| 146 | (132.812, 0.023) | (132.812, 0.249) | 37.237 mm | (132.812, 0.423) | 0, 0 |
| 148 | (133.357, 0.023) | (133.357, 0.253) | 38.328 mm | (133.357, 0.423) | 0, 0 |

The start X is unchanged. The start Y moves inward by 0.17–0.23 mm, inside
the 0.50 mm station window. The layer-change Y is the original seam Y plus
the 0.40 mm hop the BASE file already had (0.023 → 0.423, 0.100 → 0.500).

On layer 146 the unmoved toe nick is the BASE segment
`(184.008, 0.079) → (184.156, 0.144)`, length 0.162 mm. The long plantar
wall sits at about y = 0.023. Widening that wall lifts its centerline to
about y = 0.25 and leaves the nick at y = 0.144. The nick is then the
minimum, `bottom_edge` covers only the nick's X range, and the seam at
x = 132.8 is about 37 mm away along X. The seam is still on the plantar
wall. The same pattern produces the layer 110 and 148 distances.

## What changed, and what the frozen check says now

A corner shorter than 0.30 mm whose outward normal faces the belt now takes
the inset of the neighboring wall, and it is shifted along its own normal.
The nick rises with the wall, so it is no longer a lower isolated point.
The long wall remains the minimum. On the current candidate the frozen G4
check passes for both ApexCore models: 0 closed starts off the bottom, 0
starts moved, 0 layer changes bad.

The four loops that used to fail, measured on this candidate:

| Layer | Candidate start | Minimum centerline | Distance to that bottom |
| --- | --- | --- | --- |
| 110 | (174.053, 0.269) | (178.024, 0.213) | 0.000 mm |
| 116 | (131.407, 0.512) | (178.308, 0.265) | 0.000 mm |
| 146 | (132.812, 0.249) | (97.143, 0.247) | 0.000 mm |
| 148 | (133.357, 0.253) | (97.528, 0.251) | 0.000 mm |

Start X matches BASE. Nothing was moved onto a side or the top. The outer
edge of each nick stays on its own line because the shift is along that
nick's normal and the width grows by twice the shift.

## Proposed grader correction

Do not put this in `validation/forefoot_cura_gates/`.

Identify the plantar face as the long low stretch of the loop, or as the
BASE bottom station, rather than whichever short corner becomes the new
minimum Y after an inward centerline shift. A distance of tens of
millimetres in X, together with `dx = 0` and `dy = 0` on the layer-change
point, means the gate selected a different region. It does not by itself
mean the seam left the plantar face.

The frozen check is unchanged and is still the pass/fail result.
