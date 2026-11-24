#!/usr/bin/env python3
"""g_shared_path.py — FROZEN gate. One path covers ApexForge and ApexBounce.

A Forge-only or Bounce-only fill fails. The behavioral check is the hollow
pass line: both materials pass it, or neither does. A diff that adds a branch
on exactly one of apex_forge / apex_bounce also fails.

usage: g_shared_path.py --forge-pass 0 --bounce-pass 0 [--diff file] [--json out.json]
exit 0 PASS, 1 FAIL
"""
import argparse, json, os, re, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fbh_common as C

_FORGE = re.compile(r"apex[_ ]?forge", re.I)
_BOUNCE = re.compile(r"apex[_ ]?bounce", re.I)


def diff_is_one_sided(diff_text):
    if not diff_text:
        return False, []
    added = "\n".join(line[1:] for line in diff_text.splitlines() if line.startswith("+") and not line.startswith("+++"))
    forge = bool(_FORGE.search(added))
    bounce = bool(_BOUNCE.search(added))
    examples = []
    if forge and not bounce:
        examples.append("added lines name ApexForge and not ApexBounce")
    if bounce and not forge:
        examples.append("added lines name ApexBounce and not ApexForge")
    return bool(examples), examples


def evaluate(forge_pass, bounce_pass, diff_text=None):
    fails = []
    if bool(forge_pass) != bool(bounce_pass):
        which = "ApexForge" if forge_pass else "ApexBounce"
        fails.append("hollow pass line reached on %s only; the fill is not on the shared path" % which)
    one_sided, examples = diff_is_one_sided(diff_text or "")
    if one_sided:
        fails.extend(examples)
    return {
        "gate": "shared_path",
        "forge_hollow_pass": bool(forge_pass),
        "bounce_hollow_pass": bool(bounce_pass),
        "result": "FAIL" if fails else "PASS",
        "failures": fails,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--forge-pass", type=int, required=True)
    ap.add_argument("--bounce-pass", type=int, required=True)
    ap.add_argument("--diff")
    ap.add_argument("--json")
    a = ap.parse_args()
    text = open(a.diff, errors="replace").read() if a.diff else ""
    rep = C.round_report(evaluate(a.forge_pass, a.bounce_pass, text))
    if a.json:
        json.dump(rep, open(a.json, "w"), indent=1)
        open(a.json, "a").write("\n")
    print(json.dumps(rep))
    sys.exit(0 if rep["result"] == "PASS" else 1)


if __name__ == "__main__":
    main()
