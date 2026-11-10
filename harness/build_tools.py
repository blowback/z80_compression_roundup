#!/usr/bin/env python3
"""Clone each compressor in tools.json at its pinned ref, build it, and copy
its binaries to harness/tools/bin.

A tool is skipped when all its binaries are already in bin/ and were built
from the same ref (and no harness source it lists is newer), unless --force is
given. Afterwards the Z80 decoders used by linesim are assembled into
harness/tools/decoders/ (see decoders.py). Prints a built/failed summary.
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys

HARNESS = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HARNESS, "tools.json")
TOOLS_DIR = os.path.join(HARNESS, "tools")
SRC_DIR = os.path.join(TOOLS_DIR, "src")
BIN_DIR = os.path.join(TOOLS_DIR, "bin")
COMPAT_DIR = os.path.join(HARNESS, "compat")
sys.path.insert(0, HARNESS)


class BuildError(Exception):
    pass


def load_manifest(path=MANIFEST):
    with open(path) as f:
        return json.load(f)["tools"]


def stamp_path(name):
    return os.path.join(BIN_DIR, f".{name}.stamp.json")


def read_stamp(name):
    try:
        with open(stamp_path(name)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def run(cmd, cwd, log):
    log.write(f"$ (cd {cwd}) {' '.join(cmd)}\n")
    log.flush()
    proc = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True,
                          errors="replace")
    log.write(proc.stdout)
    if proc.returncode != 0:
        tail = "\n".join(proc.stdout.strip().splitlines()[-5:])
        raise BuildError(f"`{' '.join(cmd)}` exited {proc.returncode}: {tail}")
    return proc.stdout


def checkout(name, spec, log):
    src = os.path.join(SRC_DIR, name)
    if not os.path.isdir(os.path.join(src, ".git")):
        os.makedirs(SRC_DIR, exist_ok=True)
        run(["git", "clone", "--quiet", spec["repo"], src], SRC_DIR, log)
    # Make sure the ref is present (a tag or commit added after the clone).
    have = subprocess.run(["git", "rev-parse", "--verify", "--quiet",
                           spec["ref"] + "^{commit}"], cwd=src,
                          capture_output=True, text=True)
    if have.returncode != 0:
        run(["git", "fetch", "--quiet", "--tags", "origin"], src, log)
    # A clean tree at the pin: drop earlier patches and build output.
    run(["git", "checkout", "--quiet", "--force", spec["ref"]], src, log)
    run(["git", "reset", "--quiet", "--hard"], src, log)
    run(["git", "clean", "-q", "-fdx"], src, log)
    commit = run(["git", "rev-parse", "HEAD"], src, log).strip()
    if re.fullmatch(r"[0-9a-f]{40}", spec["ref"]) and commit != spec["ref"]:
        raise BuildError(f"checked out {commit}, expected {spec['ref']}")
    return src, commit


def apply_patches(src, patches, log):
    for p in patches:
        files = sorted(glob.glob(os.path.join(src, p["files"])))
        if not files:
            raise BuildError(f"patch matched no files: {p['files']}")
        hits = 0
        for path in files:
            with open(path, encoding="utf-8", errors="surrogateescape") as f:
                text = f.read()
            new, n = re.subn(p["pattern"], p["replace"], text)
            if n:
                with open(path, "w", encoding="utf-8",
                          errors="surrogateescape") as f:
                    f.write(new)
                hits += n
        log.write(f"patch {p['pattern']!r} -> {p['replace']!r}: {hits} hits\n")
        if hits == 0:
            raise BuildError(f"patch {p['pattern']!r} matched nothing")


def build_tool(name, spec, log):
    src, commit = checkout(name, spec, log)
    apply_patches(src, spec.get("patches", []), log)
    for step in spec["build"]:
        cmd = [c.replace("{compat}", COMPAT_DIR) for c in step["cmd"]]
        run(cmd, os.path.join(src, step.get("cwd", ".")), log)
    os.makedirs(BIN_DIR, exist_ok=True)
    # Drop the old stamp first so a build that fails part-way through
    # copying counts as not built.
    try:
        os.remove(stamp_path(name))
    except FileNotFoundError:
        pass
    for binname, rel in spec["binaries"].items():
        built = os.path.join(src, rel)
        if not os.path.isfile(built):
            raise BuildError(f"build finished but {rel} is missing")
        shutil.copy2(built, os.path.join(BIN_DIR, binname))
    with open(stamp_path(name), "w") as f:
        json.dump({"ref": spec["ref"], "commit": commit,
                   "repo": spec["repo"]}, f, indent=2)
    return commit


def is_built(name, spec):
    stamp = read_stamp(name)
    if not stamp or stamp.get("ref") != spec["ref"]:
        return False
    # Harness sources built into the tool (linesim.c) force a rebuild when
    # they change.
    stamp_time = os.path.getmtime(stamp_path(name))
    for rel in spec.get("sources", []):
        path = os.path.join(HARNESS, rel)
        if os.path.isfile(path) and os.path.getmtime(path) > stamp_time:
            return False
    return all(os.path.isfile(os.path.join(BIN_DIR, b))
               for b in spec["binaries"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tools", nargs="*", help="only these tools (default: all)")
    ap.add_argument("--force", action="store_true",
                    help="rebuild tools that are already built")
    args = ap.parse_args(argv)

    manifest = load_manifest()
    names = args.tools or list(manifest)
    unknown = [n for n in names if n not in manifest]
    if unknown:
        ap.error(f"unknown tool(s): {', '.join(unknown)}")

    os.makedirs(TOOLS_DIR, exist_ok=True)
    results = []
    for name in names:
        spec = manifest[name]
        if not args.force and is_built(name, spec):
            results.append((name, "skipped", "already built at " + spec["ref"]))
            print(f"[skip]  {name}: already built", flush=True)
            continue
        print(f"[build] {name} @ {spec['ref']} ...", flush=True)
        log_path = os.path.join(TOOLS_DIR, f"build-{name}.log")
        with open(log_path, "w") as log:
            try:
                commit = build_tool(name, spec, log)
                results.append((name, "built", commit))
            except (BuildError, OSError) as e:
                results.append((name, "failed", f"{e} (log: {log_path})"))

    # The Z80 decoders for linesim are assembled every run (it takes well
    # under a second) once the assembler and the decoder sources exist.
    if "sjasmplus" in names or "z80depacker" in names:
        import decoders
        ok, detail = decoders.build_all()
        results.append(("decoders", "built" if ok else "failed", detail))

    print("\nSummary:")
    for name, state, detail in results:
        print(f"  {name:12s} {state:8s} {detail}")
    failed = [r for r in results if r[1] == "failed"]
    if failed:
        print(f"\n{len(failed)} tool(s) failed: "
              + ", ".join(r[0] for r in failed))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
