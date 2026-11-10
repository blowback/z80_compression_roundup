#!/usr/bin/env python3
"""Benchmark Z80 compression candidates on the test corpus.

For each (file, candidate): compress in a temp dir, time it, check the round
trip with the host decompressor where there is one, and estimate the transfer
time over the serial line. Writes results.csv and report.md to --out.

Estimated transfer time = max(line time, decode time), where
  line time   = (compressed bytes + overhead) / (baud / 10)
  decode time = original bytes * T-states per output byte / CPU Hz
Both are estimates: no emulator, no simulated line, no decoder stalls.
"""

import argparse
import csv
import glob
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import List, Optional

HARNESS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HARNESS)

import candidates as cand_mod  # noqa: E402

PROJECT = os.path.dirname(HARNESS)
DEFAULT_FILES = os.path.join(PROJECT, "test_files")
DEFAULT_BIN = os.path.join(HARNESS, "tools", "bin")
DEFAULT_OUT = os.path.join(HARNESS, "results")
MANIFEST = os.path.join(HARNESS, "tools.json")

OK = "ok"
UNVERIFIED = "ok-unverified"
MISSING = "missing"
TIMEOUT = "timeout"
ERROR = "error"
MISMATCH = "mismatch"
GOOD = (OK, UNVERIFIED)

TYPES = {".com": "executable", ".vbm": "bitmap", ".vbp": "bitmap"}

CSV_FIELDS = ["file", "type", "candidate", "tool", "status", "original_bytes",
              "compressed_bytes", "ratio", "compress_s", "est_line_s", "est_decode_s",
              "est_transfer_s", "limited_by", "pieces", "note"]


# --------------------------------------------------------------------------
# Estimate model

@dataclass
class Settings:
    baud: float = 19200
    cpu_hz: float = 8_000_000
    overhead: int = 0
    chunk: int = 0
    timeout: float = 600
    mem_limit_mb: int = 12288
    bin_dir: str = DEFAULT_BIN
    keep_packed: Optional[str] = None   # directory for packed outputs


def estimate(compressed, original, tstates_per_byte, baud, cpu_hz, overhead):
    """Return (line_s, decode_s, est_s, limited_by)."""
    line_s = (compressed + overhead) / (baud / 10.0)
    decode_s = original * tstates_per_byte / cpu_hz
    if decode_s > line_s:
        return line_s, decode_s, decode_s, "Z80"
    return line_s, decode_s, line_s, "line"


def file_type(path):
    return TYPES.get(os.path.splitext(path)[1].lower(), "other")


def split_chunks(data, chunk):
    if not chunk or chunk <= 0 or len(data) <= chunk:
        return [data]
    return [data[i:i + chunk] for i in range(0, len(data), chunk)]


# --------------------------------------------------------------------------
# Running tools

def _limit_memory(mb):
    def apply():
        if mb > 0:
            import resource
            limit = mb * 1024 * 1024
            _, hard = resource.getrlimit(resource.RLIMIT_AS)
            if hard != resource.RLIM_INFINITY:
                limit = min(limit, hard)
            resource.setrlimit(resource.RLIMIT_AS, (limit, hard))
    return apply


def run_cmd(argv, cwd, timeout, mem_limit_mb=0):
    """Run argv. Return (status, seconds, message)."""
    start = time.perf_counter()
    try:
        proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                start_new_session=True,
                                preexec_fn=_limit_memory(mem_limit_mb))
    except OSError as e:
        return ERROR, 0.0, f"cannot run {argv[0]}: {e}"
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        return TIMEOUT, time.perf_counter() - start, f"timed out after {timeout:g} s"
    secs = time.perf_counter() - start
    if proc.returncode != 0:
        text = out.decode("utf-8", "replace").strip().splitlines()
        tail = " | ".join(line.strip() for line in text[-2:] if line.strip())
        why = (f"killed by signal {-proc.returncode}" if proc.returncode < 0
               else f"exit {proc.returncode}")
        return ERROR, secs, f"{why}: {tail}" if tail else why
    return OK, secs, ""


def expand(template, bin_dir, inp, outp):
    argv = []
    for tok in template:
        if tok.startswith("{bin:") and tok.endswith("}"):
            argv.append(os.path.join(bin_dir, tok[5:-1]))
        else:
            argv.append(tok.replace("{in}", inp).replace("{out}", outp))
    return argv


def template_bins(template):
    return [t[5:-1] for t in template or []
            if t.startswith("{bin:") and t.endswith("}")]


def missing_bins(template, bin_dir):
    return [b for b in template_bins(template)
            if not os.access(os.path.join(bin_dir, b), os.X_OK)]


@dataclass
class PieceResult:
    status: str
    size: Optional[int] = None
    seconds: float = 0.0
    note: str = ""


def packed_name(file_name, candidate_name, piece=None):
    """File name used by --keep-packed: <file>.<candidate>[.<piece>]."""
    name = f"{file_name}.{candidate_name}"
    return name if piece is None else f"{name}.{piece}"


def drop_packed(packed_dir, file_name, candidate_name):
    """Remove packed files an earlier run saved for this (file, candidate)."""
    os.makedirs(packed_dir, exist_ok=True)
    base = packed_name(file_name, candidate_name)
    for name in os.listdir(packed_dir):
        rest = name[len(base):]
        if name.startswith(base) and (
                rest == "" or (rest[:1] == "." and rest[1:].isdigit())):
            os.remove(os.path.join(packed_dir, name))


def run_piece(candidate, data, settings, keep_as=None):
    """Compress one piece and check its round trip.

    keep_as: if set, the packed output is copied to this path once the
    compressor has produced it (whatever the round-trip result).
    """
    variant = candidate.variant_for(len(data))
    if variant is None:
        return PieceResult(ERROR, note="no variant for this input size")
    miss = missing_bins(variant.compress, settings.bin_dir)
    if miss:
        return PieceResult(MISSING, note="not built: " + ", ".join(miss))
    with tempfile.TemporaryDirectory(prefix="z80bench-") as tmp:
        inp = os.path.join(tmp, "input.bin")
        comp = os.path.join(tmp, "packed.bin")
        back = os.path.join(tmp, "unpacked.bin")
        with open(inp, "wb") as f:
            f.write(data)
        st, secs, msg = run_cmd(expand(variant.compress, settings.bin_dir,
                                       inp, comp),
                                tmp, settings.timeout, settings.mem_limit_mb)
        if st != OK:
            return PieceResult(st, seconds=secs, note="compress: " + msg)
        if not os.path.isfile(comp) or os.path.getsize(comp) == 0:
            return PieceResult(ERROR, seconds=secs,
                               note="compress: no output written")
        size = os.path.getsize(comp)
        if keep_as:
            shutil.copyfile(comp, keep_as)
        notes = [variant.note] if variant.note else []
        if variant.decompress is None:
            notes.append("no host decompressor")
            return PieceResult(UNVERIFIED, size, secs, "; ".join(notes))
        dmiss = missing_bins(variant.decompress, settings.bin_dir)
        if dmiss:
            notes.append("decompressor not built: " + ", ".join(dmiss))
            return PieceResult(UNVERIFIED, size, secs, "; ".join(notes))
        dst, _, dmsg = run_cmd(expand(variant.decompress, settings.bin_dir,
                                      comp, back),
                               tmp, settings.timeout, settings.mem_limit_mb)
        if dst == TIMEOUT:
            return PieceResult(TIMEOUT, seconds=secs,
                               note="decompress: " + dmsg)
        if dst != OK or not os.path.isfile(back):
            return PieceResult(MISMATCH, seconds=secs,
                               note="decompress failed: " + (dmsg or "no output"))
        with open(back, "rb") as f:
            if f.read() != data:
                return PieceResult(MISMATCH, seconds=secs,
                                   note="decompressed output differs")
        return PieceResult(OK, size, secs, "; ".join(notes))


def run_candidate(candidate, path, data, settings):
    """Return one result row (a dict with CSV_FIELDS keys)."""
    row = {k: "" for k in CSV_FIELDS}
    row.update(file=os.path.basename(path), type=file_type(path),
               candidate=candidate.name, tool=candidate.tool or "",
               original_bytes=len(data))
    pieces = split_chunks(data, settings.chunk)
    row["pieces"] = len(pieces)

    if candidate.is_baseline:
        status, size, secs, notes = OK, len(data), 0.0, []
    else:
        status, size, secs, notes = OK, 0, 0.0, []
        seen = set()
        keep = None
        if settings.keep_packed:
            drop_packed(settings.keep_packed, row["file"], candidate.name)
        for i, piece in enumerate(pieces):
            if settings.keep_packed:
                keep = os.path.join(settings.keep_packed, packed_name(
                    row["file"], candidate.name,
                    None if len(pieces) == 1 else i))
            r = run_piece(candidate, piece, settings, keep)
            secs += r.seconds
            if r.note and r.note not in seen:
                seen.add(r.note)
                notes.append(r.note if len(pieces) == 1
                             else f"piece {i}: {r.note}")
            if r.status not in GOOD:
                status, size = r.status, None
                break
            size += r.size
            if r.status == UNVERIFIED:
                status = UNVERIFIED

    row["status"] = status
    row["compress_s"] = round(secs, 4)
    row["note"] = "; ".join(notes)
    if status in GOOD:
        line_s, decode_s, est_s, lim = estimate(
            size, len(data), candidate.tstates_per_byte, settings.baud,
            settings.cpu_hz, settings.overhead)
        row.update(compressed_bytes=size,
                   ratio=round(size / len(data), 5) if data else "",
                   est_line_s=round(line_s, 4), est_decode_s=round(decode_s, 4),
                   est_transfer_s=round(est_s, 4), limited_by=lim)
    return row


# --------------------------------------------------------------------------
# Aggregation

def rank_file(rows):
    """Rows for one file: good rows by estimated time, then failures."""
    good = sorted((r for r in rows if r["status"] in GOOD),
                  key=lambda r: (r["est_transfer_s"], r["compressed_bytes"],
                                 r["candidate"]))
    bad = sorted((r for r in rows if r["status"] not in GOOD),
                 key=lambda r: r["candidate"])
    return good, bad


def group_totals(rows, group=None):
    """Per-candidate totals over rows whose type is `group` (None = all).

    Sizes and times are summed over files where the status is ok or
    ok-unverified. Any other status in the group marks the candidate
    incomplete there.
    """
    out = {}
    for r in rows:
        if group is not None and r["type"] != group:
            continue
        t = out.setdefault(r["candidate"], {
            "candidate": r["candidate"], "files": 0, "counted": 0,
            "original": 0, "compressed": 0, "est_s": 0.0, "compress_s": 0.0,
            "z80_limited": 0, "incomplete": False, "unverified": False,
            "failures": []})
        t["files"] += 1
        if r["status"] in GOOD:
            t["counted"] += 1
            t["original"] += r["original_bytes"]
            t["compressed"] += r["compressed_bytes"]
            t["est_s"] += r["est_transfer_s"]
            t["compress_s"] += r["compress_s"]
            t["z80_limited"] += r["limited_by"] == "Z80"
            if r["status"] == UNVERIFIED:
                t["unverified"] = True
        else:
            t["incomplete"] = True
            t["failures"].append(f"{r['file']}: {r['status']}")
    return sorted(out.values(),
                  key=lambda t: (t["incomplete"], t["est_s"], t["candidate"]))


# --------------------------------------------------------------------------
# Output

def write_csv(rows, path):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)


def pinned_versions(bin_dir, manifest_path=MANIFEST):
    try:
        with open(manifest_path) as f:
            tools = json.load(f)["tools"]
    except (OSError, ValueError, KeyError):
        return []
    out = []
    for name, spec in tools.items():
        commit = "not built"
        try:
            with open(os.path.join(bin_dir, f".{name}.stamp.json")) as f:
                stamp = json.load(f)
            commit = stamp.get("commit", "?")
            if stamp.get("ref") != spec["ref"]:
                commit += f" (built from {stamp.get('ref')}, manifest differs)"
        except (OSError, ValueError):
            pass
        out.append((name, spec.get("version", ""), spec["ref"], commit,
                    spec["repo"]))
    return out


def _fmt_s(x):
    return f"{x:.2f}" if x >= 0.1 else f"{x:.3f}"


def _ratio(packed, original):
    pct = packed / original * 100
    return f"{pct:.1f}%" if pct >= 1 else f"{pct:.2f}%"


def _cell(text):
    """Make free text safe inside a Markdown table cell."""
    return " ".join(str(text).split()).replace("|", "\\|")


def _status_cell(status):
    return status if status == OK else f"**{status}**"


def write_report(rows, cands, settings, path, files_desc):
    by_name = {c.name: c for c in cands}
    L = []
    L.append("# Z80 compression benchmark")
    L.append("")
    L.append(f"Settings: {settings.baud:g} baud (8N1, {settings.baud / 10:g} B/s), "
             f"Z80 at {settings.cpu_hz / 1e6:g} MHz, per-file overhead "
             f"{settings.overhead} B, chunking "
             f"{f'{settings.chunk} B independent pieces' if settings.chunk else 'off'}, "
             f"timeout {settings.timeout:g} s per tool run. Files: {files_desc}.")
    L.append("")
    L.append("**All transfer times are estimates.** Estimated transfer = "
             "max(line time, decode time). Line time = (compressed bytes + "
             "overhead) / (baud / 10). Decode time = original bytes x "
             "T-states per output byte / CPU Hz, using the research's "
             "\"Measured performance\" figures. \"Limited by\" names the larger "
             "of the two. There is no emulator and no simulated line, so "
             "decoder stalls, UART receive cost and banking are not modelled.")
    L.append("")
    L.append("Statuses: `ok` = round trip checked with the host decompressor; "
             "`ok-unverified` = no host decompressor, size counted but not "
             "checked (marked with †); `mismatch`, `timeout`, `error`, "
             "`missing` = no valid size, left out of totals and rankings.")
    L.append("")
    L.append("Compress time is host wall-clock time for the compressor run(s).")
    L.append("")

    files = []
    for r in rows:
        if r["file"] not in files:
            files.append(r["file"])

    # Winners summary
    L.append("## Winner per file")
    L.append("")
    L.append("| File | Type | Bytes | Winner | Packed | Est. transfer (s) | "
             "Limited by | Baseline est. (s) |")
    L.append("|---|---|---:|---|---:|---:|---|---:|")
    for fn in files:
        frows = [r for r in rows if r["file"] == fn]
        good, _ = rank_file(frows)
        base = next((r for r in frows if r["candidate"] == "none"), None)
        base_s = _fmt_s(base["est_transfer_s"]) if base and base["status"] in GOOD else "-"
        if good:
            w = good[0]
            mark = " †" if w["status"] == UNVERIFIED else ""
            L.append(f"| {fn} | {w['type']} | {w['original_bytes']} | "
                     f"{w['candidate']}{mark} | {w['compressed_bytes']} | "
                     f"{_fmt_s(w['est_transfer_s'])} | {w['limited_by']} | {base_s} |")
        else:
            L.append(f"| {fn} | {frows[0]['type']} | {frows[0]['original_bytes']} "
                     f"| (none valid) | | | | {base_s} |")
    L.append("")

    # Per-file tables
    L.append("## Per file (ranked by estimated transfer time)")
    L.append("")
    for fn in files:
        frows = [r for r in rows if r["file"] == fn]
        good, bad = rank_file(frows)
        L.append(f"### {fn} ({frows[0]['type']}, {frows[0]['original_bytes']} B)")
        L.append("")
        L.append("| Rank | Candidate | Status | Packed | Ratio | Compress (s) | "
                 "Est. line (s) | Est. decode (s) | Est. transfer (s) | Limited by | Note |")
        L.append("|---:|---|---|---:|---:|---:|---:|---:|---:|---|---|")
        for i, r in enumerate(good, 1):
            win = " (winner)" if i == 1 else ""
            mark = " †" if r["status"] == UNVERIFIED else ""
            L.append(f"| {i} | {r['candidate']}{mark}{win} | {_status_cell(r['status'])} | "
                     f"{r['compressed_bytes']} | {_ratio(r['compressed_bytes'], r['original_bytes'])} | "
                     f"{_fmt_s(r['compress_s'])} | {_fmt_s(r['est_line_s'])} | "
                     f"{_fmt_s(r['est_decode_s'])} | {_fmt_s(r['est_transfer_s'])} | "
                     f"{r['limited_by']} | {_cell(r['note'])} |")
        for r in bad:
            L.append(f"| - | {r['candidate']} | {_status_cell(r['status'])} | | | "
                     f"{_fmt_s(r['compress_s'])} | | | | | {_cell(r['note'])} |")
        L.append("")

    # Group tables
    def group_table(title, group):
        totals = group_totals(rows, group)
        if not totals:
            return
        L.append(f"## {title}")
        L.append("")
        L.append("Sums over files where the candidate's status is `ok` or "
                 "`ok-unverified`. **incomplete** means at least one file in "
                 "this group failed and is not in the sums, so the row is not "
                 "comparable with complete rows.")
        L.append("")
        L.append("| Rank | Candidate | Files counted | Original | Packed | Ratio | "
                 "Est. transfer (s) | Compress (s) | Z80-limited files | Flags |")
        L.append("|---:|---|---:|---:|---:|---:|---:|---:|---:|---|")
        rank = 0
        for t in totals:
            flags = []
            if t["incomplete"]:
                flags.append("**incomplete** (" + _cell(", ".join(t["failures"])) + ")")
            if t["unverified"]:
                flags.append("† unverified")
            if t["incomplete"]:
                rk = "-"
            else:
                rank += 1
                rk = str(rank)
            ratio = _ratio(t["compressed"], t["original"]) if t["original"] else "-"
            L.append(f"| {rk} | {t['candidate']} | {t['counted']}/{t['files']} | "
                     f"{t['original']} | {t['compressed']} | {ratio} | "
                     f"{_fmt_s(t['est_s'])} | {_fmt_s(t['compress_s'])} | "
                     f"{t['z80_limited']} | {'; '.join(flags)} |")
        L.append("")

    group_table("Per type: executable (.COM)", "executable")
    group_table("Per type: bitmap (.vbm / .vbp)", "bitmap")
    if any(r["type"] == "other" for r in rows):
        group_table("Per type: other", "other")
    group_table("Overall", None)

    # Candidates and versions
    L.append("## Candidates")
    L.append("")
    L.append("| Candidate | Build tool | T-states per output byte | Description |")
    L.append("|---|---|---:|---|")
    for c in cands:
        L.append(f"| {c.name} | {c.tool or '-'} | {c.tstates_per_byte:g} | "
                 f"{c.description} |")
    L.append("")
    L.append("## Pinned versions")
    L.append("")
    L.append("| Tool | Version | Pinned ref | Built commit | Repo |")
    L.append("|---|---|---|---|---|")
    for name, ver, ref, commit, repo in pinned_versions(settings.bin_dir):
        L.append(f"| {name} | {ver} | `{ref}` | `{commit}` | {repo} |")
    L.append("")
    with open(path, "w") as f:
        f.write("\n".join(L))


# --------------------------------------------------------------------------

def collect_files(specs):
    paths = []
    for spec in specs:
        if os.path.isdir(spec):
            paths += sorted(p for p in glob.glob(os.path.join(spec, "*"))
                            if os.path.isfile(p))
        else:
            hits = sorted(glob.glob(spec))
            paths += hits if hits else [spec]
    seen, out = set(), []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def run_all(files, cands, settings, log=sys.stderr):
    rows = []
    warned = set()
    total = len(files) * len(cands)
    n = 0
    for path in files:
        with open(path, "rb") as f:
            data = f.read()
        for c in cands:
            n += 1
            row = run_candidate(c, path, data, settings)
            if ((row["status"] == MISSING or "not built" in row["note"])
                    and c.name not in warned):
                print(f"warning: {c.name}: {row['note']}", file=log)
                warned.add(c.name)
            print(f"[{n}/{total}] {row['file']:14s} {c.name:14s} "
                  f"{row['status']:13s} {row['compressed_bytes']!s:>7s} "
                  f"{row['compress_s']:.2f}s", file=log, flush=True)
            rows.append(row)
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--files", nargs="+", default=[DEFAULT_FILES],
                    help="files, globs or directories (default: test_files/)")
    ap.add_argument("--baud", type=float, default=19200)
    ap.add_argument("--cpu-hz", type=float, default=8_000_000)
    ap.add_argument("--overhead", type=int, default=0,
                    help="per-file framing overhead in bytes (default 0)")
    ap.add_argument("--chunk", type=int, default=0,
                    help="compress each file as independent pieces of this "
                         "many bytes and sum the sizes (default: off)")
    ap.add_argument("--timeout", type=float, default=600,
                    help="seconds per tool run (default 600)")
    ap.add_argument("--mem-limit-mb", type=int, default=12288,
                    help="address-space limit per tool run, 0 = none "
                         "(default 12288; RIP needs ~4N^2 bytes)")
    ap.add_argument("--candidates", nargs="+",
                    help="only these candidates (default: all)")
    ap.add_argument("--bin-dir", default=DEFAULT_BIN)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--keep-packed", action="store_true",
                    help="save each packed output to <out>/packed/"
                         "<file>.<candidate>[.<piece>] for linesim.py")
    args = ap.parse_args(argv)

    try:
        cands = cand_mod.by_name(args.candidates)
    except KeyError as e:
        ap.error(f"unknown candidate(s): {e}")
    files = collect_files(args.files)
    missing = [p for p in files if not os.path.isfile(p)]
    if missing or not files:
        ap.error("no such file(s): " + ", ".join(missing or args.files))

    settings = Settings(baud=args.baud, cpu_hz=args.cpu_hz,
                        overhead=args.overhead, chunk=args.chunk,
                        timeout=args.timeout, mem_limit_mb=args.mem_limit_mb,
                        bin_dir=os.path.abspath(args.bin_dir),
                        keep_packed=(os.path.join(args.out, "packed")
                                     if args.keep_packed else None))
    if settings.keep_packed:
        # linesim.py needs the piece size and the estimate settings.
        os.makedirs(settings.keep_packed, exist_ok=True)
        with open(os.path.join(settings.keep_packed, "bench-settings.json"),
                  "w") as f:
            json.dump({"baud": settings.baud, "cpu_hz": settings.cpu_hz,
                       "overhead": settings.overhead, "chunk": settings.chunk,
                       "files": [os.path.abspath(p) for p in files]},
                      f, indent=2)
    rows = run_all(files, cands, settings)

    os.makedirs(args.out, exist_ok=True)
    csv_path = os.path.join(args.out, "results.csv")
    md_path = os.path.join(args.out, "report.md")
    write_csv(rows, csv_path)
    files_desc = ", ".join(os.path.relpath(p, PROJECT) for p in args.files)
    write_report(rows, cands, settings, md_path, files_desc)
    print(f"wrote {csv_path}\nwrote {md_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
