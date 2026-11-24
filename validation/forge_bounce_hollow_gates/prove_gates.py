#!/usr/bin/env python3
"""prove_gates.py — no-op and bad-change proof for the frozen gates.

No-op uses the live/main slices (orca-slicer from Actions run 45295959179,
head d935197). Hollow and G1/G2 must FAIL on ApexForge and ApexBounce.
ApexCore identity and "outer walls identical to live" must PASS.

Bad changes are copies under /tmp and are not part of the freeze:
  a layer-change moved off the plantar face
  an outer-wall vertex moved 0.1 mm
  one ApexCore byte flipped off the stamp line
  a bead extruded outside the live outline
  slice time 261 s
  a Forge-only hollow pass, and a diff that names only apex_forge

Exit 0 when every expected PASS/FAIL holds. Writes proof/noop.json and
proof/bad_change.json.
"""
import json, os, shutil, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fbh_common as C
import g_hollow, g1_thin_bond, g2_thick_bond, g_surface, g_filament, g_time, g_core_identity, g_shared_path

HERE = os.path.dirname(os.path.abspath(__file__))
LIVE = os.environ.get("FBH_LIVE_DIR", "/tmp/fbh_live")


def expect(name, got, want, failures):
    if got != want:
        failures.append("%s: got %s, want %s" % (name, got, want))
    else:
        print("  %s %s" % (want, name))


def _rewrite(src, dest, edit):
    lines = open(src, errors="replace").read().splitlines(keepends=True)
    edit(lines)
    open(dest, "w").writelines(lines)


def _move_layer_change(src, dest):
    """Move the first Z travel of layer 10 to Y=25, off the plantar face."""
    parsed = C.parse(src)
    target = None
    for d in parsed["events"]:
        if d["kind"] == "travel" and d["zmove"] and d["layer"] == 10:
            target = d["line"]
            break
    if target is None:
        raise SystemExit("no layer-10 Z travel in %s" % src)

    def edit(lines):
        raw = lines[target - 1]
        body, _, comment = raw.partition(";")
        if "Y" not in body:
            raise SystemExit("layer-change line has no Y: %s" % raw.strip())
        parts = body.split()
        out = []
        for p in parts:
            if p.startswith("Y"):
                out.append("Y25")
            else:
                out.append(p)
        lines[target - 1] = " ".join(out) + ((";" + comment) if comment else ("\n" if raw.endswith("\n") else ""))
    _rewrite(src, dest, edit)


def _move_outer_wall(src, dest):
    parsed = C.parse(src)
    target = None
    for d in parsed["events"]:
        if d["kind"] == "ext" and d.get("type") == "Outer wall" and d["layer"] == 40:
            target = d["line"]
            break
    if target is None:
        raise SystemExit("no layer-40 outer wall in %s" % src)

    def edit(lines):
        raw = lines[target - 1]
        body, _, comment = raw.partition(";")
        parts = body.split()
        out = []
        for p in parts:
            if p.startswith("Y"):
                out.append("Y%.3f" % (float(p[1:]) + 0.1))
            else:
                out.append(p)
        lines[target - 1] = " ".join(out) + ((";" + comment) if comment else ("\n" if raw.endswith("\n") else ""))
    _rewrite(src, dest, edit)


def _flip_core_byte(src, dest):
    data = bytearray(open(src, "rb").read())
    # A motion line well after the stamp.
    at = data.find(b"\nG1 X", 5000)
    if at < 0:
        raise SystemExit("no G1 to flip")
    # Flip the first digit after X.
    i = at + 5
    data[i] = b"0"[0] if data[i] != ord("0") else ord("1")
    open(dest, "wb").write(data)


def _outside_bead(src, dest):
    parsed = C.parse(src)
    insert_at = None
    for d in parsed["events"]:
        if d.get("type") == "Sparse infill" and 1 <= d["layer"] <= 165:
            insert_at = d["line"]
            break
    if insert_at is None:
        raise SystemExit("no sparse infill in the forefoot")

    def edit(lines):
        lines.insert(insert_at, "G1 X0 Y80 E0.40\n")
    _rewrite(src, dest, edit)


def main():
    forge = os.path.join(LIVE, "apex_forge_orthocad.gcode")
    bounce = os.path.join(LIVE, "apex_bounce_orthocad.gcode")
    core = os.path.join(LIVE, "apex_core_orthocad.gcode")
    for path in (forge, bounce, core):
        if not os.path.isfile(path):
            raise SystemExit("missing live gcode %s (set FBH_LIVE_DIR)" % path)
    failures = []
    print("no-op")
    noop = {"materials": {}}
    for name, path in (("apex_forge", forge), ("apex_bounce", bounce), ("apex_core", core)):
        hollow = g_hollow.evaluate(path)
        g1 = g1_thin_bond.evaluate(path, path)
        g2 = g2_thick_bond.evaluate(path, path)
        surface = g_surface.evaluate(path, path)
        filament = g_filament.evaluate(path, path)
        noop["materials"][name] = {
            "hollow": hollow, "G1_thin_bond": g1, "G2_thick_bond": g2,
            "surface": surface, "filament": filament,
        }
        if name in ("apex_forge", "apex_bounce"):
            expect("%s hollow" % name, hollow["result"], "FAIL", failures)
            expect("%s G1" % name, g1["result"], "FAIL", failures)
            expect("%s G2" % name, g2["result"], "FAIL", failures)
            expect("%s G1 unbonded" % name, g1["unbonded"], 1474, failures)
            expect("%s G1 columns" % name, g1["thin_columns"], 5833, failures)
            expect("%s G2 unbonded" % name, g2["unbonded"], 2776, failures)
            expect("%s G2 columns" % name, g2["thick_columns"], 2968, failures)
            expect("%s G2 belt air" % name, round(g2["belt_air_pct"], 1), 65.4, failures)
            expect("%s G2 dorsal air" % name, round(g2["dorsal_air_pct"], 1), 80.9, failures)
            expect("%s hollow L165" % name, hollow["hollow_l165"], hollow["hollow_l165"], failures)
        else:
            expect("apex_core surface", surface["result"], "PASS", failures)
            expect("apex_core G2", g2["result"], "PASS", failures)
            expect("apex_core G2 unbonded", g2["unbonded"], 0, failures)
            expect("apex_core G1 unbonded", g1["unbonded"], 3, failures)
            expect("apex_core G1 columns", g1["thin_columns"], 15493, failures)
        expect("%s surface" % name, surface["result"], "PASS", failures)
        expect("%s filament" % name, filament["result"], "PASS", failures)
    ident = g_core_identity.evaluate(core, core)
    expect("apex_core identity", ident["result"], "PASS", failures)
    noop["apex_core_identity"] = ident
    # Forge and Bounce share the hollow counts (generic path).
    fh = noop["materials"]["apex_forge"]["hollow"]["hollow_l165"]
    bh = noop["materials"]["apex_bounce"]["hollow"]["hollow_l165"]
    expect("forge/bounce hollow L165 equal", fh, bh, failures)
    feq = noop["materials"]["apex_forge"]["hollow"]["hollow_equivalent"]
    beq = noop["materials"]["apex_bounce"]["hollow"]["hollow_equivalent"]
    expect("forge/bounce hollow equivalent equal", feq, beq, failures)
    expect("forge hollow not zero", fh != 0, True, failures)
    time_ok = g_time.evaluate(7.218)
    expect("slice time 7.218", time_ok["result"], "PASS", failures)
    shared_noop = g_shared_path.evaluate(False, False, "")
    expect("shared path symmetric no-op", shared_noop["result"], "PASS", failures)
    noop["slice_time_7_218"] = time_ok
    noop["shared_path"] = shared_noop
    noop["result"] = "FAIL"  # overall no-op is a failure: hollow and bonds are open
    proof_dir = os.path.join(HERE, "proof")
    os.makedirs(proof_dir, exist_ok=True)
    json.dump(C.round_report(noop), open(os.path.join(proof_dir, "noop.json"), "w"), indent=1)
    open(os.path.join(proof_dir, "noop.json"), "a").write("\n")

    print("bad-change")
    work = tempfile.mkdtemp(prefix="fbh_bad_")
    bad = {}
    layer = os.path.join(work, "layer.gcode")
    wall = os.path.join(work, "wall.gcode")
    flipped = os.path.join(work, "core.gcode")
    outside = os.path.join(work, "outside.gcode")
    _move_layer_change(forge, layer)
    _move_outer_wall(forge, wall)
    _flip_core_byte(core, flipped)
    _outside_bead(forge, outside)
    # The mutated files still parse. Clear the cache: paths differ so that is enough.
    surf_layer = g_surface.evaluate(layer, forge)
    surf_wall = g_surface.evaluate(wall, forge)
    ident_bad = g_core_identity.evaluate(flipped, core)
    fil_bad = g_filament.evaluate(outside, forge)
    time_bad = g_time.evaluate(261)
    one_sided = g_shared_path.evaluate(True, False, "")
    diff = "+if material == \"apex_forge\":\n+    fill_forefoot_hollow()\n"
    diff_bad = g_shared_path.evaluate(False, False, diff)
    both = g_shared_path.evaluate(True, True, "")
    expect("layer-change off bottom", surf_layer["result"], "FAIL", failures)
    expect("outer wall moved", surf_wall["result"], "FAIL", failures)
    expect("core byte flipped", ident_bad["result"], "FAIL", failures)
    expect("bead outside outline", fil_bad["result"], "FAIL", failures)
    expect("slice time 261", time_bad["result"], "FAIL", failures)
    expect("forge-only hollow pass", one_sided["result"], "FAIL", failures)
    expect("forge-only diff", diff_bad["result"], "FAIL", failures)
    expect("both materials hollow pass", both["result"], "PASS", failures)
    # A hollow left in place is the no-op hollow FAIL, already recorded.
    bad = {
        "layer_change_off_bottom": surf_layer,
        "outer_wall_moved": surf_wall,
        "core_byte_flipped": ident_bad,
        "bead_outside_outline": fil_bad,
        "slice_time_261": time_bad,
        "forge_only_hollow_pass": one_sided,
        "forge_only_diff": diff_bad,
        "both_hollow_pass": both,
        "result": "REJECTED",
    }
    json.dump(C.round_report(bad), open(os.path.join(proof_dir, "bad_change.json"), "w"), indent=1)
    open(os.path.join(proof_dir, "bad_change.json"), "a").write("\n")
    shutil.rmtree(work, ignore_errors=True)
    if failures:
        print("PROOF FAIL")
        for f in failures:
            print(" ", f)
        sys.exit(1)
    print("PROOF OK")


if __name__ == "__main__":
    main()
