"""Collision-gated Apps Script pusher / reader for the bound "Running Reship" project.

WHY: projects/{id}/content PUT REPLACES ALL FILES. clasp push of the directory would
also upload PivotSheet.gs (defines a conflicting refresh()). So: GET live content,
swap ONLY the named local file(s) in, verify every other file came back byte-identical
to what was live, then PUT.

Usage:
  python gas_swap.py get                 -> dump live file list + sha + save to ./live/
  python gas_swap.py diff <Name>         -> live vs local diff for one file
  python gas_swap.py push <Name>[=<path>] [...] [--allow-create]

`--allow-create` is the ONLY way to add a file the live project does not have. It exists because a
push that carried only [appsscript, Code] DELETED Exceptions.gs + PivotAnalytics.gs from the live
project (observed 2026-08-14 — the exact hazard this tool was written to prevent, executed by some
other path). Restoring a deleted file is a CREATE, so the implicit-create gate has to be opened by
hand, per push, with the source spelled out. `Name=<path>` pins the bytes to an explicit file —
restore from the last-deployed snapshot, not from a worktree that may hold uncommitted work.
"""
import hashlib
import json
import os
import sys
import time

import requests

RC = os.path.expanduser("~/.clasprc.json")
SCRIPT_ID = "15K0MrUssFqacWybQAToz6CeHTouRU4IeNY4-DzZ4NeE1rBCCNGpGjAjv"
SRC = r"C:\Users\Work\Claude Projects\AppyHour\ShippingReports\appsscript"
LIVE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live")
API = f"https://script.googleapis.com/v1/projects/{SCRIPT_ID}/content"


def token():
    rc = json.load(open(RC))
    oc = rc["oauth2ClientSettings"]
    tk = rc["token"]
    if tk.get("expiry_date", 0) > (time.time() + 120) * 1000:
        return tk["access_token"]
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": oc["clientId"], "client_secret": oc["clientSecret"],
        "refresh_token": tk["refresh_token"], "grant_type": "refresh_token"}, timeout=30)
    r.raise_for_status()
    d = r.json()
    tk["access_token"] = d["access_token"]
    tk["expiry_date"] = int(time.time() * 1000) + d.get("expires_in", 3600) * 1000
    json.dump(rc, open(RC, "w"))
    return tk["access_token"]


def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def get_live(at):
    r = requests.get(API, headers={"Authorization": "Bearer " + at}, timeout=60)
    r.raise_for_status()
    return r.json()["files"]


def ext(f):
    return ".gs" if f["type"] == "SERVER_JS" else (".json" if f["type"] == "JSON" else ".html")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "get"
    at = token()
    files = get_live(at)
    if cmd == "get":
        os.makedirs(LIVE, exist_ok=True)
        for f in files:
            p = os.path.join(LIVE, f["name"] + ext(f))
            with open(p, "w", encoding="utf-8", newline="") as fh:
                fh.write(f["source"])
            loc = os.path.join(SRC, f["name"] + ext(f))
            lsha = sha(open(loc, encoding="utf-8").read()) if os.path.exists(loc) else "-missing-"
            print(f'{f["name"]:<18} {f["type"]:<10} live={sha(f["source"])} local={lsha} '
                  f'{"SAME" if lsha == sha(f["source"]) else "DIFF"} bytes={len(f["source"])}')
        return
    if cmd == "diff":
        name = sys.argv[2]
        f = [x for x in files if x["name"] == name][0]
        loc = open(os.path.join(SRC, name + ext(f)), encoding="utf-8").read()
        import difflib
        d = list(difflib.unified_diff(f["source"].splitlines(), loc.splitlines(),
                                      "live", "local", lineterm="", n=2))
        print("\n".join(d[:400]) or "IDENTICAL")
        return
    if cmd == "push":
        args = sys.argv[2:]
        allow_create = "--allow-create" in args
        targets = {}
        for a in [x for x in args if not x.startswith("--")]:
            name, _, path = a.partition("=")
            targets[name] = path or os.path.join(SRC, name + (".json" if name == "appsscript" else ".gs"))
        assert targets, "name at least one file"
        before = {f["name"]: f["source"] for f in files}
        out = []
        for f in files:
            if f["name"] in targets:
                src = open(targets[f["name"]], encoding="utf-8").read()
                print(f'  SWAP {f["name"]}: {sha(f["source"])} -> {sha(src)}')
                out.append({"name": f["name"], "type": f["type"], "source": src})
            else:
                out.append({"name": f["name"], "type": f["type"], "source": f["source"]})
        missing = [t for t in targets if t not in before]
        assert not missing or allow_create, f"not live, refusing to create implicitly: {missing}"
        for n in missing:
            src = open(targets[n], encoding="utf-8").read()
            print(f"  CREATE {n}: (absent live) -> {sha(src)}  from {targets[n]}")
            out.append({"name": n, "type": "SERVER_JS", "source": src})
        r = requests.put(API, headers={"Authorization": "Bearer " + at},
                         json={"files": out}, timeout=60)
        print("PUT", r.status_code, r.text[:300] if not r.ok else "ok")
        r.raise_for_status()
        after = {f["name"]: f["source"] for f in get_live(at)}
        expect = set(before) | set(targets)
        assert set(after) == expect, f"file set changed! {expect ^ set(after)}"
        bad = []
        for n in after:
            want = open(targets[n], encoding="utf-8").read() if n in targets else before[n]
            if after[n] != want:
                bad.append(n)
        # 🔴 NO NON-ASCII HERE. This print used to carry ✅/❌ and died on Windows' cp1252 stdout
        # AFTER the PUT had already landed — taking `assert not bad` down with it, so the push
        # reported a traceback instead of a verdict and NOTHING verified the other files. The
        # verification must not be reachable-only-through-a-print-that-can-throw.
        print("VERIFY:", "ALL BYTE-IDENTICAL" if not bad else f"MISMATCH {bad}")
        for n in sorted(after):
            print(f"   {n:<18} {sha(after[n])} {'(pushed)' if n in targets else '(untouched)'}")
        assert not bad
        return
    raise SystemExit("unknown cmd")


if __name__ == "__main__":
    main()
