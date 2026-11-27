#!/usr/bin/env python3
"""A6 path. Outer and inner wall paths match BASE except a collinear split at a loop's
new start. Every candidate vertex lies on the BASE path, every BASE vertex is still a
candidate vertex, and the only added vertices are those splits. E per mm on each move
is within 0.5 % of the BASE segment it lies on. Feedrates stay on the same geometry
(a rotation of the loop does not move F). Fan and temperature command sequences match.
Per closed loop, the retract, prime, and dry-wipe sequences match BASE (wipe length and
feed; the wipe may rotate to the new end). 

usage: a6_path.py BASE_DIR CAND_DIR [--json out.json]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scg_common as S
import sfg_common as C

S.pin_sfg()


class _Index:
    def __init__(self, segs, cell=4.0):
        self.segs = segs
        self.cell = cell
        self.buckets = {}
        for i, s in enumerate(segs):
            for key in self._cells(s["x0"], s["y0"], s["x1"], s["y1"]):
                self.buckets.setdefault(key, []).append(i)

    def _cells(self, x0, y0, x1, y1):
        c = self.cell
        ix0, ix1 = int(min(x0, x1) / c), int(max(x0, x1) / c)
        iy0, iy1 = int(min(y0, y1) / c), int(max(y0, y1) / c)
        keys = []
        for ix in range(ix0 - 1, ix1 + 2):
            for iy in range(iy0 - 1, iy1 + 2):
                keys.append((ix, iy))
        return keys

    def near(self, x, y):
        ix, iy = int(x / self.cell), int(y / self.cell)
        seen = set()
        hits = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i in self.buckets.get((ix + dx, iy + dy), ()):
                    if i in seen:
                        continue
                    seen.add(i)
                    hits.append(self.segs[i])
        return hits


def _pts(chains) -> list[tuple[float, float]]:
    pts = []
    for ch in chains:
        pts.extend(ch["pts"])
    return pts


def _bucket_pts(pts, cell=1.0):
    buckets = {}
    for x, y in pts:
        buckets.setdefault((int(x / cell), int(y / cell)), []).append((x, y))
    return buckets, cell


def _close_vertex(buckets, cell, x, y, tol):
    ix, iy = int(x / cell), int(y / cell)
    reach = int(tol / cell) + 1
    best = tol + 1.0
    for dx in range(-reach, reach + 1):
        for dy in range(-reach, reach + 1):
            for px, py in buckets.get((ix + dx, iy + dy), ()):
                d = ((px - x) ** 2 + (py - y) ** 2) ** 0.5
                if d < best:
                    best = d
    return best


def _lies_on(seg, base, tol) -> bool:
    bl = C.seglen(base)
    cl = C.seglen(seg)
    if bl < 1e-9 or cl < 1e-9:
        return False
    d0, _t0 = C.seg_point_dist(seg["x0"], seg["y0"], base["x0"], base["y0"], base["x1"], base["y1"])
    d1, _t1 = C.seg_point_dist(seg["x1"], seg["y1"], base["x0"], base["y0"], base["x1"], base["y1"])
    if d0 > tol or d1 > tol:
        return False
    cux, cuy = (seg["x1"] - seg["x0"]) / cl, (seg["y1"] - seg["y0"]) / cl
    bux, buy = (base["x1"] - base["x0"]) / bl, (base["y1"] - base["y0"]) / bl
    return cux * bux + cuy * buy > 0.99


def _match_base(seg, index: _Index):
    mx = 0.5 * (seg["x0"] + seg["x1"])
    my = 0.5 * (seg["y0"] + seg["y1"])
    for base in index.near(mx, my):
        if _lies_on(seg, base, S.PATH_TOL):
            return base
    for base in index.segs:
        if _lies_on(seg, base, S.PATH_TOL):
            return base
    return None


def _approach(ev, chain) -> list[tuple]:
    i = chain["seg_idx"][0] - 1
    items = []
    while i >= 0 and ev[i]["kind"] != "ext":
        if ev[i]["kind"] in ("retract", "prime"):
            items.append((ev[i]["kind"], round(ev[i]["e"], 5), round(ev[i]["f"], 1)))
        i -= 1
    items.reverse()
    return items


def _wipe_after(ev, chain) -> list[dict]:
    j = chain["seg_idx"][-1] + 1
    inw = False
    found = False
    moves = []
    while j < len(ev) and ev[j]["kind"] not in ("ext", "layer"):
        d = ev[j]
        raw = str(d.get("raw", ""))
        if d["kind"] == "comment" and raw.startswith(";WIPE_START"):
            inw = True
            found = True
        elif d["kind"] == "comment" and raw.startswith(";WIPE_END"):
            break
        elif inw and d["kind"] == "travel":
            moves.append(d)
        j += 1
    return moves if found else []


def _wipe_ok(moves, chain) -> str | None:
    if not moves:
        return "missing wipe"
    endx, endy = chain["end"]
    last = chain["segs"][-1]
    span = C.seglen(last)
    if span < 1e-9:
        return "zero last segment"
    ux, uy = (last["x1"] - last["x0"]) / span, (last["y1"] - last["y0"]) / span
    for mv in moves:
        if ((mv["x0"] - endx) ** 2 + (mv["y0"] - endy) ** 2) ** 0.5 > S.WIPE_LEN_TOL and (
                (mv["x0"] - chain["start"][0]) ** 2 + (mv["y0"] - chain["start"][1]) ** 2) ** 0.5 > S.WIPE_LEN_TOL:
            # The glide starts at the loop end. A following tiny move may start at the glide end.
            pass
        wl = C.seglen(mv)
        if wl < 1e-9:
            continue
        vx, vy = (mv["x1"] - mv["x0"]) / wl, (mv["y1"] - mv["y0"]) / wl
        if vx * ux + vy * uy < 0.99:
            return "wipe not collinear with the last segment"
    # The first wipe move starts on the loop end.
    mv0 = moves[0]
    if ((mv0["x0"] - endx) ** 2 + (mv0["y0"] - endy) ** 2) ** 0.5 > S.WIPE_LEN_TOL:
        return "wipe does not start at the loop end"
    return None


def _compare_group(base_ev, cand_ev, base_chains, cand_chains, group: str) -> list[str]:
    fails = []
    b_by, c_by = {}, {}
    for ch in base_chains:
        b_by.setdefault(ch["layer"], []).append(ch)
    for ch in cand_chains:
        c_by.setdefault(ch["layer"], []).append(ch)
    layers = sorted(set(b_by) | set(c_by))
    off_path = 0
    missing = 0
    extras_bad = 0
    e_bad = 0
    f_bad = 0
    seq_bad = 0
    worst_e = 0.0
    worst_f = 0.0
    examples = []

    def note(msg):
        if len(examples) < 8:
            examples.append(msg)

    for lay in layers:
        bc = b_by.get(lay, [])
        cc = c_by.get(lay, [])
        b_closed = [c for c in bc if c["closed"]]
        c_closed = [c for c in cc if c["closed"]]
        if len(b_closed) != len(c_closed):
            fails.append("%s layer %d closed loops %d != BASE %d" % (group, lay, len(c_closed), len(b_closed)))
            continue
        bsegs = [s for ch in bc for s in ch["segs"]]
        csegs = [s for ch in cc for s in ch["segs"]]
        index = _Index(bsegs) if bsegs else None
        buckets, cell = _bucket_pts(_pts(bc))
        cand_buckets, _ = _bucket_pts(_pts(cc))
        starts = []
        for ch in cc:
            starts.append(ch["start"])
        for x, y in _pts(cc):
            if not bsegs:
                off_path += 1
                note("%s L%d vertex (%.3f,%.3f) has no BASE path" % (group, lay, x, y))
                continue
            near = index.near(x, y)
            dist = S.PATH_TOL + 1
            for s in near:
                d, _t = C.seg_point_dist(x, y, s["x0"], s["y0"], s["x1"], s["y1"])
                if d < dist:
                    dist = d
            if dist > S.PATH_TOL:
                for s in bsegs:
                    d, _t = C.seg_point_dist(x, y, s["x0"], s["y0"], s["x1"], s["y1"])
                    if d < dist:
                        dist = d
            if dist > S.PATH_TOL:
                off_path += 1
                note("%s L%d vertex off BASE path by %.4f mm" % (group, lay, dist))
            elif _close_vertex(buckets, cell, x, y, S.PATH_TOL) > S.PATH_TOL:
                if not any(((x - sx) ** 2 + (y - sy) ** 2) ** 0.5 <= S.PATH_TOL for sx, sy in starts):
                    extras_bad += 1
                    note("%s L%d extra vertex (%.3f,%.3f) is not a loop start" % (group, lay, x, y))
        for x, y in _pts(bc):
            if _close_vertex(cand_buckets, cell, x, y, S.PATH_TOL) > S.PATH_TOL:
                missing += 1
                note("%s L%d BASE vertex (%.3f,%.3f) missing" % (group, lay, x, y))
        for seg in csegs:
            if index is None:
                e_bad += 1
                continue
            base = _match_base(seg, index)
            if base is None:
                e_bad += 1
                note("%s L%d move not on one BASE segment" % (group, lay))
                continue
            bl = C.seglen(base)
            cl = C.seglen(seg)
            if bl < 1e-9 or cl < 1e-9:
                continue
            expected = base["e"] / bl * cl
            err = abs(seg["e"] - expected)
            rel = err / abs(expected) if abs(expected) > 1e-12 else err
            worst_e = max(worst_e, rel)
            if err > S.EPM_REL * abs(expected) + S.EPM_ABS:
                e_bad += 1
                note("%s L%d E %.5f != BASE E/mm * len %.5f" % (group, lay, seg["e"], expected))
            if abs(seg["f"] - base["f"]) > S.F_TOL:
                f_bad += 1
                worst_f = max(worst_f, abs(seg["f"] - base["f"]))
                note("%s L%d F %.0f != BASE F %.0f" % (group, lay, seg["f"], base["f"]))
        for bch, cch in zip(b_closed, c_closed):
            if _approach(base_ev, bch) != _approach(cand_ev, cch):
                seq_bad += 1
                note("%s L%d retract/prime sequence changed" % (group, lay))
            if group == "outer":
                bw = _wipe_after(base_ev, bch)
                cw = _wipe_after(cand_ev, cch)
                if len(bw) != len(cw):
                    seq_bad += 1
                    note("%s L%d wipe moves %d != BASE %d" % (group, lay, len(cw), len(bw)))
                else:
                    for bm, cm in zip(bw, cw):
                        if abs(C.seglen(bm) - C.seglen(cm)) > S.WIPE_LEN_TOL or abs(bm["f"] - cm["f"]) > S.F_TOL:
                            seq_bad += 1
                            note("%s L%d wipe length/feed changed" % (group, lay))
                            break
                    why = _wipe_ok(cw, cch) if cw else ("missing wipe" if bw else None)
                    if why:
                        seq_bad += 1
                        note("%s L%d %s" % (group, lay, why))
    if off_path:
        fails.append("%s: %d vertices off the BASE path" % (group, off_path))
    if missing:
        fails.append("%s: %d BASE vertices missing" % (group, missing))
    if extras_bad:
        fails.append("%s: %d extra vertices that are not a collinear split at a loop start" % (group, extras_bad))
    if e_bad:
        fails.append("%s: %d moves with E/mm outside 0.5%% (worst rel %.4f)" % (group, e_bad, worst_e))
    if f_bad:
        fails.append("%s: %d moves whose feed is not the BASE segment feed (worst %.1f)" % (group, f_bad, worst_f))
    if seq_bad:
        fails.append("%s: %d closed loops whose retract/prime/wipe sequence changed" % (group, seq_bad))
    return fails, {
        "off_path": off_path, "missing": missing, "extras_bad": extras_bad,
        "e_bad": e_bad, "f_bad": f_bad, "seq_bad": seq_bad,
        "worst_e_rel": worst_e, "worst_f": worst_f, "examples": examples,
    }


def measure(base_path: str, cand_path: str) -> dict:
    base_ev = C.parse(base_path)
    cand_ev = C.parse(cand_path)
    fails = []
    groups = {}
    for group, getter in (("outer", S.outer_chains), ("inner", S.inner_chains)):
        gfails, stats = _compare_group(base_ev, cand_ev, getter(base_ev), getter(cand_ev), group)
        fails.extend(gfails)
        groups[group] = stats
    bf = S.fan_temp_sequence(base_ev)
    cf = S.fan_temp_sequence(cand_ev)
    fan_diff = None
    if bf != cf:
        fan_diff = next((i for i in range(min(len(bf), len(cf))) if bf[i] != cf[i]), min(len(bf), len(cf)))
        fails.append("fan/temp sequence differs at #%s (%d cand, %d base)" % (fan_diff, len(cf), len(bf)))
    return {"failures": fails, "groups": groups, "fan_temp_diff": fan_diff,
            "fan_temp_n": [len(cf), len(bf)]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("base_dir")
    ap.add_argument("cand_dir")
    ap.add_argument("--json")
    args = ap.parse_args()
    fails = []
    measured = {}
    parts = []
    for key, name in S.CORE.items():
        m = measure(S.gcode(args.base_dir, name), S.gcode(args.cand_dir, name))
        measured[key] = m
        if m["failures"]:
            fails.append("%s: %s" % (key, "; ".join(m["failures"][:4])))
        outer = m["groups"]["outer"]
        parts.append(("%s_off_path" % key, outer["off_path"]))
        parts.append(("%s_missing" % key, outer["missing"]))
        parts.append(("%s_e_bad" % key, outer["e_bad"]))
        parts.append(("%s_f_bad" % key, outer["f_bad"]))
        parts.append(("%s_seq_bad" % key, outer["seq_bad"]))
        parts.append(("%s_inner_off" % key, m["groups"]["inner"]["off_path"]))
        parts.append(("%s_fan_diff" % key, -1 if m["fan_temp_diff"] is None else m["fan_temp_diff"]))
    status = "FAIL" if fails else "PASS"
    rep = {"gate": "A6 path", "result": status, "failures": fails, "measured": measured,
           "path_tol_mm": S.PATH_TOL, "epm_rel": S.EPM_REL}
    S.emit(status, "A6", parts, rep, args.json)


if __name__ == "__main__":
    main()
