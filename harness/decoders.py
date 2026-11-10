#!/usr/bin/env python3
"""Z80 decoders used by linesim.py, and how to assemble them.

Each candidate maps to one decoder from uniabis/z80depacker (the fastest
decoder per format in its readme). The decoder sources are used unchanged
apart from what the line-simulation plan allows: the origin (set by a
generated wrapper), buffer addresses, and the defines from z80depacker's
build.bat. Two decoders hard-code a buffer address as a plain assignment
rather than a define, so those lines are rewritten in the copy that gets
assembled (Shrinkler `probs`, RIP `workarea`). RIP's source also has an
`OUTPUT derip.C` directive that would send the code to another file; it is
commented out.

Flat-mode memory layout (logical addresses, all in RAM):

  0x0100 ..          output, then the compressed input right after it
  0xC000 - 0xCFFF    decoder code
  0xD000 - 0xD13F    upkr probs (256-aligned, 320 B)
  0xD800 - 0xDFFF    Shrinkler probs (256-aligned, 2 KB)
  0xE000 - 0xE09B    Exomizer decrunch table (mapbase)
  0xE800 - 0xEDC1    RIP work area (even, 0x5C2 B)
  0xFF00             sentinel HALT (the decoder's return address)
  0xFFF0             initial stack top (grows down)

Banked mode (linesim --mode banked) uses a second set, BANKED: one port of
each decoder in harness/sim/banked/, which reads its input from an 8 KB
receive ring and pages the output through windows 0-2 itself (see
harness/sim/banked/banked.inc). Banked layout:

  0x0000 - 0xBFFF    output, windows 0-2 (RAM pages 0x20.. physical 0x80000)
  0xC000 - 0xDFFF    receive ring, window 3 = RAM page 0x3F
  0xE000 - 0xEFFF    decoder code (banked.inc + the port)
  0xF000 - 0xF7FF    the decoder's buffer (probs, decrunch table, work area)
  0xF800 - 0xF8FF    ring pointers and variables (WP 0xF800, RP 0xF802)
  0xFF00             sentinel HALT, 0xFFF0 stack top

Run `python3 harness/decoders.py` to assemble both sets into
harness/tools/decoders/ and harness/tools/decoders/banked/ (build_tools.py
does this too).
"""

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

HARNESS = os.path.dirname(os.path.abspath(__file__))
TOOLS_DIR = os.path.join(HARNESS, "tools")
BIN_DIR = os.path.join(TOOLS_DIR, "bin")
SRC_DIR = os.path.join(TOOLS_DIR, "src", "z80depacker")
OUT_DIR = os.path.join(TOOLS_DIR, "decoders")
SJASMPLUS = os.path.join(BIN_DIR, "sjasmplus")

# Layout (see module docstring)
OUT_START = 0x0100
RESERVED_START = 0xC000
CODE_ORG = 0xC000
CODE_LIMIT = 0xD000
UPKR_PROBS = 0xD000
SHRINKLER_PROBS = 0xD800
EXO_MAPBASE = 0xE000
RIP_WORKAREA = 0xE800
SENTINEL = 0xFF00
STACK_TOP = 0xFFF0

# Banked layout (see module docstring)
BANKED_SRC_DIR = os.path.join(HARNESS, "sim", "banked")
BANKED_OUT_DIR = os.path.join(OUT_DIR, "banked")
BANKED_CODE_ORG = 0xE000
BANKED_CODE_LIMIT = 0xF000
BANKED_BUFFER = 0xF000
BANKED_RING = 0xC000
BANKED_RING_SIZE = 8192
BANKED_WP = 0xF800
BANKED_RP = 0xF802
BANKED_LEN = 0xF804
BANKED_LAST = 0xF80E
BANKED_REBANKS = 0xF810
BANKED_WRAPS = 0xF812
BANKED_FIRST_PAGE = 0x20
BANKED_LAST_PAGE = 0x3E            # page 0x3F holds the ring and the code
BANKED_OUT_MAX = (BANKED_LAST_PAGE - BANKED_FIRST_PAGE + 1) * 0x4000
BANKED_STREAM_MAX = 0xFFFF         # @len is a 16-bit word


@dataclass
class Decoder:
    name: str
    source: str                       # file in z80depacker
    regs: Dict[str, str]              # linesim --reg values
    entry: Optional[str] = None       # label; None = first byte (CODE_ORG)
    defines: List[str] = field(default_factory=list)
    # (regex, replacement) rewrites of buffer addresses; each must match once
    rewrites: List[Tuple[str, str]] = field(default_factory=list)
    note: str = ""


DECODERS = {d.name: d for d in [
    Decoder("dzx0_mega", "dzx0_mega.asm", {"HL": "@in", "DE": "@out"},
            entry="dzx0_mega", note="ZX0 v2, mega (fastest)"),
    Decoder("unlzsa2_fast", "unlzsa2_fast.asm", {"HL": "@in", "DE": "@out"},
            entry="DecompressLZSA2", note="LZSA2 raw block, fast"),
    Decoder("unaplib_fast", "unaplib_fast.asm", {"HL": "@in", "DE": "@out"},
            entry="Decompress", note="aPLib, fast"),
    Decoder("deexoopt_x3_p39", "deexoopt_x3.asm", {"HL": "@in", "DE": "@out"},
            defines=["OPTIMIZE_JUMP=1", "PFLAG_CODE=39",
                     f"mapbase=0x{EXO_MAPBASE:04X}", "literals=1"],
            note="Exomizer 3.1 raw -P39"),
    Decoder("derip_fast", "derip_fast.asm", {"IX": "@in", "DE": "@out-1"},
            rewrites=[(r"(?m)^(\s*)OUTPUT derip\.C", r"\1;OUTPUT derip.C"),
                      (r"(?m)^workarea=#0800", f"workarea=#{RIP_WORKAREA:04X}")],
            note="RIP, fast; DE = destination - 1"),
    Decoder("unlz4_spke_fast", "unlz4_spke_fast.asm",
            {"HL": "@in", "DE": "@out"}, entry="DecompressLZ4",
            note="LZ4 raw block, fast"),
    Decoder("unpack_upkr_z80_speed", "unpack_upkr_z80.asm",
            {"IX": "@in", "DE'": "@out"}, entry="upkr.unpack",
            defines=[f"UPKR_PROBS_ORIGIN=0x{UPKR_PROBS:04X}",
                     "UPKR_ALLOW_SELF_MODIFYING", "UPKR_UNPACK_SPEED"],
            note="upkr --z80, speed build; DE' = destination"),
    Decoder("deshrinkler_np", "deshrinkler_np.asm",
            {"IX": "@in", "DE": "@out"}, entry="shrinkler_decrunch",
            rewrites=[(r"(?m)^probs = 01000h",
                       f"probs = 0{SHRINKLER_PROBS:04X}h")],
            note="Shrinkler -b (no parity context)"),
]}

# Banked ports: same names as DECODERS, sources in harness/sim/banked/
# (<name>.asm, which INCLUDEs banked.inc). Entry is banked_entry, which sets
# the pages, HL = first input byte in the ring and DE = 0, then calls the
# port. Buffers sit at BANKED_BUFFER.
BANKED = {d.name: d for d in [
    Decoder("dzx0_mega", "dzx0_mega.asm", {}, entry="banked_entry",
            note="ZX0 v2, mega, banked port"),
    Decoder("unlzsa2_fast", "unlzsa2_fast.asm", {}, entry="banked_entry",
            note="LZSA2 raw block, fast, banked port"),
    Decoder("unaplib_fast", "unaplib_fast.asm", {}, entry="banked_entry",
            defines=["SupportLongOffsets"],
            note="aPLib, fast, banked port, long offsets on"),
    Decoder("deexoopt_x3_p39", "deexoopt_x3_p39.asm", {},
            entry="banked_entry",
            defines=["OPTIMIZE_JUMP=1", "PFLAG_CODE=39",
                     f"mapbase=0x{BANKED_BUFFER:04X}", "literals=1",
                     "RING_COUNT_MEM"],
            note="Exomizer 3.1 raw -P39, banked port"),
    Decoder("derip_fast", "derip_fast.asm", {}, entry="banked_entry",
            note="RIP, fast, banked port"),
    Decoder("unlz4_spke_fast", "unlz4_spke_fast.asm", {},
            entry="banked_entry", note="LZ4 raw block, fast, banked port"),
    Decoder("unpack_upkr_z80_speed", "unpack_upkr_z80_speed.asm", {},
            entry="banked_entry",
            defines=[f"UPKR_PROBS_ORIGIN=0x{BANKED_BUFFER:04X}",
                     "UPKR_ALLOW_SELF_MODIFYING", "UPKR_UNPACK_SPEED"],
            note="upkr --z80, speed build, banked port"),
    Decoder("deshrinkler_np", "deshrinkler_np.asm", {},
            entry="banked_entry", defines=["RING_COUNT_MEM"],
            note="Shrinkler -b (no parity context), banked port"),
]}

# Bench candidate name -> decoder name. "none" has no decoder.
CANDIDATE_DECODER = {
    "zx0": "dzx0_mega",
    "salvador": "dzx0_mega",
    "lzsa2": "unlzsa2_fast",
    "aplib-oapack": "unaplib_fast",
    "aplib-apultra": "unaplib_fast",
    "exomizer": "deexoopt_x3_p39",
    "rip": "derip_fast",
    "lz4": "unlz4_spke_fast",
    "upkr": "unpack_upkr_z80_speed",
    "shrinkler": "deshrinkler_np",
}


class AssembleError(Exception):
    pass


def _paths(name, out_dir=OUT_DIR):
    base = os.path.join(out_dir, name)
    return {"bin": base + ".bin", "sym": base + ".sym", "meta": base + ".json",
            "wrapper": base + ".wrap.asm", "src": base + ".src.asm",
            "log": base + ".log"}


def parse_sym(path):
    """sjasmplus --sym output: `label: EQU 0x0000C000` per line."""
    syms = {}
    with open(path) as f:
        for line in f:
            m = re.match(r"\s*([^\s:]+):\s+EQU\s+(0x[0-9A-Fa-f]+|\d+)", line)
            if m:
                syms[m.group(1)] = int(m.group(2), 0)
    return syms


def assemble(dec, src_dir=SRC_DIR, out_dir=OUT_DIR, sjasmplus=SJASMPLUS,
             banked=False):
    """Assemble one decoder. Returns its metadata dict.

    banked: dec is a port from BANKED (origin BANKED_CODE_ORG, banked.inc
    on the include path, wait-loop range in the metadata)."""
    org = BANKED_CODE_ORG if banked else CODE_ORG
    limit = BANKED_CODE_LIMIT if banked else CODE_LIMIT
    p = _paths(dec.name, out_dir)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(src_dir, dec.source), encoding="latin-1") as f:
        text = f.read()
    changes = []
    for pattern, repl in dec.rewrites:
        hits = list(re.finditer(pattern, text))
        if len(hits) != 1:
            raise AssembleError(f"{dec.name}: rewrite {pattern!r} matched "
                                f"{len(hits)} times, expected 1")
        changes.append([hits[0].group(0).strip(),
                        hits[0].expand(repl).strip()])
        text = re.sub(pattern, repl, text)
    with open(p["src"], "w", encoding="latin-1") as f:
        f.write(text)
    with open(p["wrapper"], "w") as f:
        f.write(f"; generated by harness/decoders.py\n"
                f"        ORG 0x{org:04X}\n"
                f"__entry:\n"
                f"        INCLUDE \"{os.path.basename(p['src'])}\"\n")
    for path in (p["bin"], p["sym"]):
        if os.path.exists(path):
            os.remove(path)
    cmd = [sjasmplus, "--nologo", f"--raw={os.path.basename(p['bin'])}",
           f"--sym={os.path.basename(p['sym'])}"]
    if banked:
        cmd += ["-i", src_dir]
    cmd += [f"-D{d}" for d in dec.defines]
    cmd.append(os.path.basename(p["wrapper"]))
    proc = subprocess.run(cmd, cwd=out_dir, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True, errors="replace")
    with open(p["log"], "w") as f:
        f.write("$ " + " ".join(cmd) + "\n" + proc.stdout)
    if proc.returncode != 0 or not os.path.isfile(p["bin"]):
        tail = " | ".join(proc.stdout.strip().splitlines()[-3:])
        raise AssembleError(f"{dec.name}: sjasmplus exited "
                            f"{proc.returncode}: {tail}")
    size = os.path.getsize(p["bin"])
    if size == 0 or org + size > limit:
        raise AssembleError(f"{dec.name}: code size {size} B does not fit "
                            f"0x{org:04X}-0x{limit:04X}")
    syms = parse_sym(p["sym"])
    label = dec.entry or "__entry"
    if label not in syms:
        raise AssembleError(f"{dec.name}: entry label {label} not found")
    meta = {"name": dec.name, "source": dec.source, "bin": p["bin"],
            "org": org, "size": size, "entry": syms[label],
            "code_lo": org, "code_hi": org + size,
            "regs": dec.regs, "defines": dec.defines,
            "rewrites": changes, "note": dec.note, "banked": banked}
    if banked:
        for need in ("ring_wait_loop", "ring_wait_exit", "banked_decoder"):
            if need not in syms:
                raise AssembleError(f"{dec.name}: label {need} not found")
        meta["wait_lo"] = syms["ring_wait_loop"]
        meta["wait_hi"] = syms["ring_wait_exit"]
    with open(p["meta"], "w") as f:
        json.dump(meta, f, indent=2)
    return meta


def build_all(src_dir=SRC_DIR, out_dir=OUT_DIR, sjasmplus=SJASMPLUS,
              banked_src_dir=BANKED_SRC_DIR, banked_out_dir=BANKED_OUT_DIR):
    """Assemble every decoder, flat and banked. Returns (all_ok, summary)."""
    if not os.access(sjasmplus, os.X_OK):
        return False, f"sjasmplus not built ({sjasmplus})"
    if not os.path.isdir(src_dir):
        return False, f"z80depacker sources missing ({src_dir})"
    done, failed = [], []
    for dec in DECODERS.values():
        try:
            meta = assemble(dec, src_dir, out_dir, sjasmplus)
            done.append(f"{dec.name} {meta['size']} B")
        except (AssembleError, OSError) as e:
            failed.append(str(e))
    for dec in BANKED.values():
        try:
            meta = assemble(dec, banked_src_dir, banked_out_dir, sjasmplus,
                            banked=True)
            done.append(f"banked {dec.name} {meta['size']} B")
        except (AssembleError, OSError) as e:
            failed.append("banked " + str(e))
    if failed:
        return False, "; ".join(failed)
    return True, (f"{len(done)} decoders ({len(DECODERS)} flat in {out_dir}, "
                  f"{len(BANKED)} banked in {banked_out_dir})")


def load(name, out_dir=OUT_DIR):
    """Metadata of an assembled decoder, or None when it is not built."""
    try:
        with open(_paths(name, out_dir)["meta"]) as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return None
    return meta if os.path.isfile(meta.get("bin", "")) else None


def main():
    ok, detail = build_all()
    print(("built: " if ok else "failed: ") + detail)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
