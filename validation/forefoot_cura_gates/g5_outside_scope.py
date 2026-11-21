#!/usr/bin/env python3
"""g5_outside_scope.py — FROZEN gate G5. Do not edit; checked by SHA256SUMS.

G5 Outside scope. Layers after the forefoot boundary are byte-identical to BASE
(the end-script G91 and the settings footer are not part of that span, and the
OrcaSlicer timestamp is not either). The ordered fan, temperature, acceleration
and flow-factor M-codes of the whole file match BASE. Each extra output that is
named (ApexLight, ApexForge, ApexBounce, the other STL) is byte-identical after
the one timestamp line is removed.

ApexForge and ApexBounce are checked when the caller passes them. The current
BASE CI artifacts (docker-server-proof, slice_all.sh) upload ApexCore and
ApexLight only, so a grade that does not pass Forge/Bounce lists them as
unchecked. Unchecked is not a pass on that material. Pass --strict-materials
to fail closed when either is missing.

usage:
  g5_outside_scope.py --core orthocad:BASE.gcode:CAND.gcode
                      [--core insole:BASE.gcode:CAND.gcode]
                      [--pair ApexLight:BASE.gcode:CAND.gcode ...]
                      [--strict-materials] [--json out.json]
exit 0 PASS, 1 FAIL
"""
import argparse, hashlib, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fcg_common as C


def _first_diff(a, b):
    if a == b:
        return None
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    return n


def _pair(spec):
    parts = spec.split(":")
    if len(parts) != 3 or not parts[0]:
        raise SystemExit("expected Label:base.gcode:cand.gcode, got %r" % spec)
    return parts[0], parts[1], parts[2]


def evaluate(cores, pairs, strict_materials):
    fails = []
    core_rows = []
    for model, base, cand in cores:
        bp, cp = C.parse(base), C.parse(cand)
        bt, ct = C.post_forefoot_text(bp, model), C.post_forefoot_text(cp, model)
        diff = _first_diff(ct, bt)
        if diff is not None:
            fails.append("%s layers after %d differ at character %d" % (model, C.FOREFOOT_LAST[model], diff))
        if bp["mcodes"] != cp["mcodes"]:
            fails.append("%s process M-codes (fan, temperature, accel, flow) differ from BASE" % model)
        core_rows.append({
            "model": model,
            "forefoot_last_layer": C.FOREFOOT_LAST[model],
            "post_forefoot_chars": len(ct),
            "post_forefoot_equal": diff is None,
            "first_diff_char": diff,
            "process_mcodes_equal": bp["mcodes"] == cp["mcodes"],
            "base_sha256": hashlib.sha256(open(base, "rb").read()).hexdigest(),
            "cand_sha256": hashlib.sha256(open(cand, "rb").read()).hexdigest(),
        })
    pair_rows = []
    labels = set()
    for label, base, cand in pairs:
        labels.add(label.lower())
        try:
            bb, _nb = C.strip_stamp(open(base, "rb").read())
            cb, _nc = C.strip_stamp(open(cand, "rb").read())
        except SystemExit as exc:
            fails.append("%s: %s" % (label, exc))
            continue
        diff = _first_diff(cb, bb)
        if diff is not None:
            fails.append("%s differs from BASE at normalized byte %d" % (label, diff))
        pair_rows.append({
            "label": label,
            "equal": diff is None,
            "first_diff_byte": diff,
            "base_sha256_norm": hashlib.sha256(bb).hexdigest(),
            "cand_sha256_norm": hashlib.sha256(cb).hexdigest(),
        })
    unchecked = []
    for name in ("ApexForge", "ApexBounce"):
        if not any(name.lower() in lab for lab in labels):
            unchecked.append(name)
    if strict_materials and unchecked:
        fails.append("strict materials: not supplied: %s" % ", ".join(unchecked))
    if not cores:
        fails.append("no ApexCore pair was supplied — fail closed")
    return {
        "gate": "G5 outside_scope",
        "cores": core_rows,
        "pairs": pair_rows,
        "unchecked_materials": unchecked,
        "strict_materials": strict_materials,
        "note": ("Unchecked ApexForge/ApexBounce means those files were not in the grade inputs. "
                 "That is not a pass on Forge or Bounce. BASE CI run 44943125841 uploads ApexCore and ApexLight only."),
        "result": "FAIL" if fails else "PASS",
        "failures": fails,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--core", action="append", default=[], help="model:base.gcode:cand.gcode")
    ap.add_argument("--pair", action="append", default=[], help="Label:base.gcode:cand.gcode")
    ap.add_argument("--strict-materials", action="store_true")
    ap.add_argument("--json")
    a = ap.parse_args()
    cores = []
    for spec in a.core:
        model, base, cand = _pair(spec)
        if model not in C.FOREFOOT_LAST:
            raise SystemExit("core model must be orthocad or insole, got %r" % model)
        cores.append((model, base, cand))
    pairs = [_pair(spec) for spec in a.pair]
    rep = evaluate(cores, pairs, a.strict_materials)
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
        open(a.json, "a").write("\n")
    print(json.dumps({k: rep[k] for k in ("gate", "result", "failures", "unchecked_materials")}))
    sys.exit(0 if rep["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
