#!/usr/bin/env python3
"""Simulate each candidate's real Z80 decoder against the serial line.

Reads a bench run made with --keep-packed (results.csv plus packed/), lays
out memory for each (file, candidate), runs the cycle-counted emulator
(harness/tools/bin/linesim) and checks the decoder output against the
original file. Writes sim.csv and sim-report.md next to the bench results
(or to --out).

Two memory modes (--mode):

  flat    the unmodified decoders; output and input must fit below 0xC000
  banked  the banked ports (harness/sim/banked/): input from an 8 KB
          receive ring under RTS/CTS flow control, output paged through
          RAM pages 0x20-0x3E, so every file runs whole whose output is
          at most 496 KB and whose packed pieces are at most 65,535 bytes

Every time this script reports is simulated: it comes from the emulator and
the line model in harness/README.md ("Line simulation"), not from hardware.
The bench's figures are shown beside it as the harness estimate.
"""

import argparse
import csv
import json
import math
import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

HARNESS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HARNESS)

import bench  # noqa: E402
import decoders  # noqa: E402

LINESIM = os.path.join(bench.DEFAULT_BIN, "linesim")

OK = "ok"
TOO_LARGE = "too-large"
MISMATCH = "mismatch"
ERROR = "error"
SKIPPED = "skipped"

SIM_FIELDS = ["file", "type", "candidate", "decoder", "status",
              "original_bytes", "compressed_bytes", "pieces",
              "sim_s", "sim_cycles", "stall_t", "rx_t", "rx_total_t",
              "flow_pause_t", "overread", "stall_share", "line_s",
              "limited_by", "est_transfer_s", "est_limited_by",
              "diff_s", "diff_pct", "reason"]


FLAT = "flat"
BANKED = "banked"


class SimSettings:
    def __init__(self, baud=19200, cpu_hz=8_000_000, rx_cost=150,
                 max_cycles=20e9, linesim=LINESIM,
                 decoder_dir=None, timeout=None, mode=FLAT):
        if decoder_dir is None:
            decoder_dir = (decoders.BANKED_OUT_DIR if mode == BANKED
                           else decoders.OUT_DIR)
        self.mode = mode
        self.baud = baud
        self.cpu_hz = cpu_hz
        self.rx_cost = rx_cost
        self.max_cycles = max_cycles
        self.linesim = linesim
        self.decoder_dir = decoder_dir
        self.timeout = timeout

    @property
    def t_byte(self):
        return self.cpu_hz * 10.0 / self.baud


# --------------------------------------------------------------------------
# Layout and one simulation

def layout(piece_lens, packed_lens):
    """Flat-mode addresses per piece: output at 0x0100, input right after.

    Returns a list of (in_addr, out_addr) or None when a piece does not fit
    below the reserved area.
    """
    out = []
    for n_out, n_in in zip(piece_lens, packed_lens):
        in_addr = decoders.OUT_START + n_out
        if in_addr + n_in > decoders.RESERVED_START:
            return None
        out.append((in_addr, decoders.OUT_START))
    return out


def banked_fit(piece_lens, packed_lens):
    """Banked mode: None when every piece fits, else the reason."""
    for n_out, n_in in zip(piece_lens, packed_lens):
        if n_out > decoders.BANKED_OUT_MAX:
            return ("output %d B exceeds the %d B of RAM pages 0x%02X-0x%02X"
                    % (n_out, decoders.BANKED_OUT_MAX,
                       decoders.BANKED_FIRST_PAGE, decoders.BANKED_LAST_PAGE))
        if n_in > decoders.BANKED_STREAM_MAX:
            return ("packed stream %d B exceeds the 16-bit stream length"
                    % n_in)
    return None


def _exec(cmd, pieces, outs, settings):
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True,
                              timeout=settings.timeout)
    except subprocess.TimeoutExpired:
        return ERROR, None, f"linesim timed out after {settings.timeout} s"
    except OSError as e:
        return ERROR, None, f"cannot run linesim: {e}"
    try:
        res = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        err = " ".join(proc.stderr.split())[:200]
        return ERROR, None, f"linesim exit {proc.returncode}: {err}"
    if res["status"] != OK:
        return ERROR, res, res.get("reason") or "linesim error"
    for i, ((orig, _), out_path) in enumerate(zip(pieces, outs)):
        with open(out_path, "rb") as f:
            got = f.read()
        if got != orig:
            where = f"piece {i}: " if len(pieces) > 1 else ""
            at = next((j for j, (a, b) in enumerate(zip(got, orig)) if a != b),
                      min(len(got), len(orig)))
            hint = ""
            if len(orig) > 0x10000:
                hint = ("; the output is over 64 KB, so likely causes are "
                        "a match longer than 65,535 bytes, which the "
                        "decoder's 16-bit length count cannot hold, or "
                        "(banked) a match offset beyond the 32 KB of "
                        "history kept mapped")
            return MISMATCH, res, (where + f"decoder output differs from "
                                   f"byte {at}" + hint)
    return OK, res, ""


def _common_args(meta, settings):
    return ["--load", f"{meta['org']}:{meta['bin']}",
            "--entry", str(meta["entry"]),
            "--code", f"{meta['code_lo']}:{meta['code_hi']}",
            "--sp", str(decoders.STACK_TOP),
            "--sentinel", str(decoders.SENTINEL),
            "--baud", repr(float(settings.baud)),
            "--cpu-hz", repr(float(settings.cpu_hz)),
            "--rx-cost", str(int(settings.rx_cost)),
            "--max-cycles", repr(float(settings.max_cycles))]


def run_linesim_banked(meta, pieces, settings, workdir, extra=()):
    """Banked mode: the port reads the ring and pages its output itself.
    pieces: list of (original bytes, packed path); extra: more linesim
    arguments (the tests use --dump-phys)."""
    why = banked_fit([len(o) for o, _ in pieces],
                     [os.path.getsize(p) for _, p in pieces])
    if why:
        return TOO_LARGE, None, why
    cmd = [settings.linesim, "--mode", "banked"] + _common_args(meta, settings)
    cmd += ["--ring", f"{decoders.BANKED_RING}:{decoders.BANKED_RING_SIZE}:"
                      f"{decoders.BANKED_WP}:{decoders.BANKED_RP}",
            "--wait", f"{meta['wait_lo']}:{meta['wait_hi']}",
            "--word", f"{decoders.BANKED_LEN}=@len",
            "--word", f"{decoders.BANKED_LAST}=@last"]
    for name, value in meta["regs"].items():
        cmd += ["--reg", f"{name}={value}"]
    outs = []
    for i, (orig, packed) in enumerate(pieces):
        out_path = os.path.join(workdir, f"out{i}.bin")
        outs.append(out_path)
        cmd += ["--piece", f"0:{packed}:0:{len(orig)}:{out_path}"]
    cmd += list(extra)
    return _exec(cmd, pieces, outs, settings)


def run_linesim(meta, pieces, settings, workdir):
    """pieces: list of (original bytes, packed path). Returns
    (status, result dict or None, reason)."""
    if settings.mode == BANKED:
        return run_linesim_banked(meta, pieces, settings, workdir)
    spots = layout([len(o) for o, _ in pieces],
                   [os.path.getsize(p) for _, p in pieces])
    if spots is None:
        return TOO_LARGE, None, (
            "output + input exceed 0x%04X-byte area below the reserved "
            "0xC000-0xFFFF" % (decoders.RESERVED_START - decoders.OUT_START))
    cmd = [settings.linesim, "--mode", "flat",
           "--load", f"{meta['org']}:{meta['bin']}",
           "--entry", str(meta["entry"]),
           "--code", f"{meta['code_lo']}:{meta['code_hi']}",
           "--sp", str(decoders.STACK_TOP),
           "--sentinel", str(decoders.SENTINEL),
           "--baud", repr(float(settings.baud)),
           "--cpu-hz", repr(float(settings.cpu_hz)),
           "--rx-cost", str(int(settings.rx_cost)),
           "--max-cycles", repr(float(settings.max_cycles))]
    for name, value in meta["regs"].items():
        cmd += ["--reg", f"{name}={value}"]
    outs = []
    for i, ((orig, packed), (in_addr, out_addr)) in enumerate(
            zip(pieces, spots)):
        out_path = os.path.join(workdir, f"out{i}.bin")
        outs.append(out_path)
        cmd += ["--piece", f"{in_addr}:{packed}:{out_addr}:{len(orig)}:"
                           f"{out_path}"]
    return _exec(cmd, pieces, outs, settings)


# --------------------------------------------------------------------------
# Rows

def _blank(brow):
    row = {k: "" for k in SIM_FIELDS}
    row.update(file=brow["file"], type=brow["type"],
               candidate=brow["candidate"],
               original_bytes=int(brow["original_bytes"]),
               pieces=int(brow["pieces"] or 1))
    if brow["status"] in bench.GOOD:
        row["compressed_bytes"] = int(brow["compressed_bytes"])
        row["est_transfer_s"] = float(brow["est_transfer_s"])
        row["est_limited_by"] = brow["limited_by"]
    return row


def _finish(row, cycles, stall, rx, rx_total, flow, overread, settings):
    row["sim_cycles"] = cycles
    row["sim_s"] = round(cycles / settings.cpu_hz, 4)
    row["stall_t"] = stall
    row["rx_t"] = rx
    row["rx_total_t"] = rx_total
    row["flow_pause_t"] = flow
    row["overread"] = overread
    row["stall_share"] = round(stall / cycles, 4) if cycles else 0
    row["limited_by"] = "line" if stall > cycles / 2 else "Z80"
    if row["est_transfer_s"] != "":
        d = row["sim_s"] - row["est_transfer_s"]
        row["diff_s"] = round(d, 4)
        if row["est_transfer_s"]:
            row["diff_pct"] = round(100 * d / row["est_transfer_s"], 2)


def simulate_row(brow, original, packed_dir, chunk, settings):
    """One bench row -> one sim row."""
    row = _blank(brow)
    cand = brow["candidate"]
    if brow["status"] not in bench.GOOD:
        row.update(status=SKIPPED, reason=f"bench status {brow['status']}")
        return row
    if original is None:
        row.update(status=SKIPPED, reason="original file not found")
        return row
    pieces_orig = bench.split_chunks(original, chunk)
    if len(pieces_orig) != row["pieces"]:
        row.update(status=SKIPPED, reason=(
            f"bench row has {row['pieces']} pieces, chunk {chunk} gives "
            f"{len(pieces_orig)}"))
        return row
    nbytes = row["compressed_bytes"]
    row["line_s"] = round(nbytes * settings.t_byte / settings.cpu_hz, 4)

    if cand == "none":
        # No decoding: the transfer ends when the last byte's receive
        # interrupt finishes.
        # The CPU only waits, so all of it counts as stall.
        cycles = math.ceil(nbytes * settings.t_byte) + settings.rx_cost
        row.update(status=OK, decoder="-")
        _finish(row, cycles, cycles, 0, nbytes * settings.rx_cost, 0, 0,
                settings)
        return row

    dec_name = decoders.CANDIDATE_DECODER.get(cand)
    if dec_name is None:
        row.update(status=SKIPPED, reason="no Z80 decoder for this candidate")
        return row
    row["decoder"] = dec_name
    meta = decoders.load(dec_name, settings.decoder_dir)
    if meta is None:
        row.update(status=SKIPPED, reason=f"decoder {dec_name} not assembled")
        return row
    if not os.access(settings.linesim, os.X_OK):
        row.update(status=SKIPPED, reason="linesim not built")
        return row

    paths = []
    for i in range(len(pieces_orig)):
        p = os.path.join(packed_dir, bench.packed_name(
            brow["file"], cand, None if len(pieces_orig) == 1 else i))
        if not os.path.isfile(p):
            row.update(status=SKIPPED, reason=f"packed file missing: "
                                              f"{os.path.basename(p)}")
            return row
        paths.append(p)
    if "stream format" in brow.get("note", ""):
        # checked after layout so a 256 KB file still reads too-large
        if settings.mode == BANKED:
            why = banked_fit([len(o) for o in pieces_orig],
                             [os.path.getsize(p) for p in paths])
        else:
            why = None if layout([len(o) for o in pieces_orig],
                                 [os.path.getsize(p) for p in paths]) \
                else "does not fit flat memory"
        if why:
            row.update(status=TOO_LARGE, reason=why)
        else:
            row.update(status=SKIPPED, reason="LZSA stream format has no "
                                              "Z80 decoder here")
        return row

    with tempfile.TemporaryDirectory(prefix="linesim-") as tmp:
        status, res, reason = run_linesim(meta, list(zip(pieces_orig, paths)),
                                          settings, tmp)
    row.update(status=status, reason=reason)
    if status == OK:
        _finish(row, res["cycles"], res["stall"], res["rx"],
                res["bytes_received"] * settings.rx_cost, res["flow_pause"],
                res["overread"], settings)
        if res["overread"]:
            row["reason"] = (f"decoder read {res['overread']} B past the "
                             f"end of the stream (counted as padding)")
    return row


def load_bench(results_dir):
    with open(os.path.join(results_dir, "results.csv"), newline="") as f:
        rows = list(csv.DictReader(f))
    try:
        with open(os.path.join(results_dir, "packed",
                               "bench-settings.json")) as f:
            bset = json.load(f)
    except (OSError, ValueError):
        bset = None
    return rows, bset


def simulate_all(brows, originals, packed_dir, chunk, settings, jobs=None,
                 log=sys.stderr):
    def one(brow):
        return simulate_row(brow, originals.get(brow["file"]), packed_dir,
                            chunk, settings)
    with ThreadPoolExecutor(max_workers=jobs or os.cpu_count() or 1) as ex:
        rows = list(ex.map(one, brows))
    for r in rows:
        print(f"{r['file']:14s} {r['candidate']:14s} {r['status']:9s} "
              f"{r['sim_s']!s:>9s} {r['reason']}", file=log)
    return rows


# --------------------------------------------------------------------------
# Report

def rank_file(rows):
    good = sorted((r for r in rows if r["status"] == OK),
                  key=lambda r: (r["sim_s"], r["compressed_bytes"],
                                 r["candidate"]))
    bad = sorted((r for r in rows if r["status"] != OK),
                 key=lambda r: r["candidate"])
    return good, bad


def group_totals(rows, group=None):
    out = {}
    for r in rows:
        if group is not None and r["type"] != group:
            continue
        t = out.setdefault(r["candidate"], {
            "candidate": r["candidate"], "files": 0, "counted": 0,
            "compressed": 0, "original": 0, "sim_s": 0.0, "est_s": 0.0,
            "stall": 0, "cycles": 0, "z80_limited": 0, "incomplete": False,
            "failures": []})
        t["files"] += 1
        if r["status"] == OK:
            t["counted"] += 1
            t["compressed"] += r["compressed_bytes"]
            t["original"] += r["original_bytes"]
            t["sim_s"] += r["sim_s"]
            t["est_s"] += r["est_transfer_s"] or 0
            t["stall"] += r["stall_t"] or 0
            t["cycles"] += r["sim_cycles"]
            t["z80_limited"] += r["limited_by"] == "Z80"
        else:
            t["incomplete"] = True
            t["failures"].append(f"{r['file']}: {r['status']}")
    return sorted(out.values(),
                  key=lambda t: (t["incomplete"], t["sim_s"], t["candidate"]))


def _pct(x):
    return "" if x == "" else f"{x * 100:.1f}%"


def _diff(r):
    if r["diff_s"] == "":
        return ""
    pct = f" ({r['diff_pct']:+.1f}%)" if r["diff_pct"] != "" else ""
    return f"{r['diff_s']:+.3f}{pct}"


def write_csv(rows, path):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SIM_FIELDS)
        w.writeheader()
        w.writerows(rows)


MODE_TEXT = {
    FLAT: ("flat: the unmodified decoders, output at 0x0100 and the "
           "compressed input right after it, all below 0xC000"),
    BANKED: ("banked: the banked decoder ports read the stream from an "
             "8 KB receive ring at 0xC000 that the simulated interrupt fills "
             "under RTS/CTS flow control, and page the output through RAM "
             "pages 0x20-0x3E with OUT writes to ports 0x70-0x72; every file "
             "runs whole whose output is at most 496 KB and whose packed "
             "pieces are at most 65,535 bytes (a 16-bit length word)"),
}


def flat_vs_banked(banked_rows, flat_rows):
    """Per candidate: totals over files both modes simulated ok."""
    flat = {(r["file"], r["candidate"]): r for r in flat_rows}
    out = {}
    for r in banked_rows:
        f = flat.get((r["file"], r["candidate"]))
        if r["status"] != OK or f is None or f["status"] != OK:
            continue
        t = out.setdefault(r["candidate"], {"candidate": r["candidate"],
                                            "files": 0, "banked_s": 0.0,
                                            "flat_s": 0.0})
        t["files"] += 1
        t["banked_s"] += r["sim_s"]
        t["flat_s"] += f["sim_s"]
    return sorted(out.values(), key=lambda t: (t["banked_s"], t["candidate"]))


def write_report(rows, settings, path, results_dir, bset, flat_rows=None):
    fmt, cell = bench._fmt_s, bench._cell
    mode = getattr(settings, "mode", FLAT)
    L = ["# Z80 line simulation", ""]
    L.append(f"Memory mode: **{MODE_TEXT[mode]}**.")
    L.append("")
    L.append(f"Bench results: `{os.path.relpath(results_dir, bench.PROJECT)}`. "
             f"Simulation settings: {settings.baud:g} baud (8N1, T_byte = "
             f"{settings.t_byte:.1f} T-states), Z80 at "
             f"{settings.cpu_hz / 1e6:g} MHz, receive interrupt "
             f"{settings.rx_cost} T-states per byte, cycle limit "
             f"{settings.max_cycles:.3g}.")
    if bset:
        chunk = (f"{bset['chunk']} B pieces" if bset.get("chunk")
                 else "off")
        L.append(f"The bench estimates were made at {bset['baud']:g} baud, "
                 f"{bset['cpu_hz'] / 1e6:g} MHz, overhead "
                 f"{bset['overhead']} B, chunking {chunk}.")
    L.append("")
    waits = ("the decoder checks the ring's write pointer before each "
             "byte and waits in its own loop until the byte is there"
             if mode == BANKED else
             "the decoder stalls when it reads a byte that has not arrived")
    L.append("**Simulated times** come from running each candidate's real "
             "Z80 decoder in a cycle-counted emulator. Byte *i* of the "
             f"line is due at (i + 1) x T_byte + receive cost; {waits}, "
             "and each "
             "receive interrupt that falls while the decoder runs takes the "
             "receive cost from it. Output is checked byte for byte against "
             "the original. They are not measured on hardware. "
             "**Est.** columns are the bench's estimate, max(line, decode) "
             "from published per-byte averages, shown for comparison.")
    L.append("")
    too_large = ("output over 496 KB, or a packed piece over 65,535 B"
                 if mode == BANKED
                 else "output + input do not fit below 0xC000")
    if mode == BANKED:
        L.append("In banked mode the decoder waits for bytes in its own "
                 "code: *Stall* is the time it spent in its wait loop "
                 "(with the interrupts taken there), and *flow_pause_t* in "
                 "`sim.csv` is how long the line was held off because the "
                 "ring was full.")
        L.append("")
    L.append("Columns: *Stall* is the share of the simulated time the "
             "decoder spent waiting for bytes. *Limited by* is `line` when "
             "stall is more than half the simulated time, else `Z80`. "
             "*Diff* is simulated minus estimated. Statuses: `ok`, "
             f"`too-large` ({too_large}), "
             "`mismatch` (decoder output differs, no time given), `error` "
             "(emulator stopped: cycle limit, PC outside the decoder, "
             "stray HALT), `skipped` (bench row failed, no packed file, or "
             "no decoder).")
    L.append("")

    files = []
    for r in rows:
        if r["file"] not in files:
            files.append(r["file"])

    L.append("## Winner per file (simulated)")
    L.append("")
    L.append("| File | Type | Bytes | Winner | Packed | Simulated (s) | "
             "Limited by | Est. winner | Est. (s) | Baseline simulated (s) |")
    L.append("|---|---|---:|---|---:|---:|---|---|---:|---:|")
    for fn in files:
        fr = [r for r in rows if r["file"] == fn]
        good, _ = rank_file(fr)
        base = next((r for r in fr if r["candidate"] == "none"
                     and r["status"] == OK), None)
        base_s = fmt(base["sim_s"]) if base else "-"
        est = sorted((r for r in fr if r["est_transfer_s"] != ""),
                     key=lambda r: (r["est_transfer_s"], r["candidate"]))
        est_w = f"{est[0]['candidate']} | {fmt(est[0]['est_transfer_s'])}" \
            if est else "- | -"
        if good:
            w = good[0]
            L.append(f"| {fn} | {w['type']} | {w['original_bytes']} | "
                     f"{w['candidate']} | {w['compressed_bytes']} | "
                     f"{fmt(w['sim_s'])} | {w['limited_by']} | {est_w} | "
                     f"{base_s} |")
        else:
            L.append(f"| {fn} | {fr[0]['type']} | {fr[0]['original_bytes']} "
                     f"| (none simulated) | | | | {est_w} | {base_s} |")
    L.append("")

    L.append("## Per file (ranked by simulated time)")
    L.append("")
    for fn in files:
        fr = [r for r in rows if r["file"] == fn]
        good, bad = rank_file(fr)
        L.append(f"### {fn} ({fr[0]['type']}, {fr[0]['original_bytes']} B, "
                 f"{fr[0]['pieces']} piece{'s' if fr[0]['pieces'] != 1 else ''})")
        L.append("")
        L.append("| Rank | Candidate | Decoder | Status | Packed | "
                 "Simulated (s) | Stall | Line (s) | Est. (s) | Diff (s) | "
                 "Limited by | Est. limited by | Note |")
        L.append("|---:|---|---|---|---:|---:|---:|---:|---:|---:|---|---|---|")
        for i, r in enumerate(good, 1):
            win = " (winner)" if i == 1 else ""
            L.append(f"| {i} | {r['candidate']}{win} | {r['decoder']} | ok | "
                     f"{r['compressed_bytes']} | {fmt(r['sim_s'])} | "
                     f"{_pct(r['stall_share'])} | {fmt(r['line_s'])} | "
                     f"{fmt(r['est_transfer_s'])} | {_diff(r)} | "
                     f"{r['limited_by']} | {r['est_limited_by']} | "
                     f"{cell(r['reason'])} |")
        for r in bad:
            est = fmt(r["est_transfer_s"]) if r["est_transfer_s"] != "" else ""
            L.append(f"| - | {r['candidate']} | {r['decoder']} | "
                     f"**{r['status']}** | {r['compressed_bytes']} | | | | "
                     f"{est} | | | {r['est_limited_by']} | "
                     f"{cell(r['reason'])} |")
        L.append("")

    def group_table(title, group):
        totals = group_totals(rows, group)
        if not totals:
            return
        L.append(f"## {title}")
        L.append("")
        L.append("Sums over files the candidate simulated `ok`. "
                 "**incomplete** means at least one file in this group was "
                 "not simulated `ok`, so the row is not comparable with "
                 "complete rows.")
        L.append("")
        L.append("| Rank | Candidate | Files | Packed | Simulated (s) | "
                 "Est. (s) | Diff (s) | Stall | Z80-limited files | Flags |")
        L.append("|---:|---|---:|---:|---:|---:|---:|---:|---:|---|")
        rank = 0
        for t in totals:
            if t["incomplete"]:
                rk = "-"
                flags = ("**incomplete** ("
                         + cell(", ".join(t["failures"])) + ")")
            else:
                rank += 1
                rk, flags = str(rank), ""
            stall = _pct(t["stall"] / t["cycles"]) if t["cycles"] else ""
            L.append(f"| {rk} | {t['candidate']} | {t['counted']}/{t['files']} "
                     f"| {t['compressed']} | {fmt(t['sim_s'])} | "
                     f"{fmt(t['est_s'])} | {t['sim_s'] - t['est_s']:+.3f} | "
                     f"{stall} | {t['z80_limited']} | {flags} |")
        L.append("")

    group_table("Per type: executable (.COM)", "executable")
    group_table("Per type: bitmap (.vbm / .vbp)", "bitmap")
    if any(r["type"] == "other" for r in rows):
        group_table("Per type: other", "other")
    group_table("Overall", None)

    if flat_rows is not None:
        L.append("## Banked vs flat")
        L.append("")
        L.append("Per candidate, summed over the files that both modes "
                 "simulated `ok` (the files that fit flat memory). The "
                 "difference is what the ring reads, the window shifts and "
                 "the split copies cost, plus any change in waiting.")
        L.append("")
        L.append("| Candidate | Files | Banked (s) | Flat (s) | Diff (s) | "
                 "Diff |")
        L.append("|---|---:|---:|---:|---:|---:|")
        for t in flat_vs_banked(rows, flat_rows):
            d = t["banked_s"] - t["flat_s"]
            pct = f"{100 * d / t['flat_s']:+.2f}%" if t["flat_s"] else ""
            L.append(f"| {t['candidate']} | {t['files']} | "
                     f"{fmt(t['banked_s'])} | {fmt(t['flat_s'])} | "
                     f"{d:+.4f} | {pct} |")
        L.append("")

    L.append("## Decoders")
    L.append("")
    if mode == BANKED:
        L.append("Banked ports of the uniabis/z80depacker decoders "
                 "(`harness/sim/banked/`), assembled with sjasmplus at "
                 "0xE000 with the shared `banked.inc`. Only input reads, "
                 "output writes, copies and buffer addresses differ from the "
                 "published source; each port lists its edit sites in its "
                 "header.")
    else:
        L.append("From uniabis/z80depacker, assembled with sjasmplus at "
                 "0xC000. Changes from the published source: the origin, "
                 "buffer addresses and build defines, plus RIP's `OUTPUT` "
                 "directive commented out so the code goes to the raw "
                 "output file.")
    L.append("")
    L.append("| Candidates | Decoder | Source | Size (B) | Entry registers | "
             "Defines / rewrites |")
    L.append("|---|---|---|---:|---|---|")
    by_dec = {}
    for c, d in decoders.CANDIDATE_DECODER.items():
        by_dec.setdefault(d, []).append(c)
    for d, cands in by_dec.items():
        meta = decoders.load(d, settings.decoder_dir)
        if meta is None:
            L.append(f"| {', '.join(cands)} | {d} | | | | not assembled |")
            continue
        regs = ", ".join(f"{k} = {v}" for k, v in meta["regs"].items())
        if meta.get("banked"):
            regs = "banked_entry: HL = ring, DE = 0"
        extra = [f"`-D{x}`" for x in meta["defines"]]
        extra += [f"`{cell(a)}` -> `{cell(b)}`" for a, b in meta["rewrites"]]
        L.append(f"| {', '.join(cands)} | {d} | {meta['source']} | "
                 f"{meta['size']} | {regs} | {'; '.join(extra) or '-'} |")
    L.append("")
    with open(path, "w") as f:
        f.write("\n".join(L))


# --------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results", default=bench.DEFAULT_OUT,
                    help="bench output directory made with --keep-packed")
    ap.add_argument("--files", nargs="+",
                    help="where the original files are (default: the "
                         "files the bench run recorded, else test_files/)")
    ap.add_argument("--baud", type=float, default=19200)
    ap.add_argument("--cpu-hz", type=float, default=8_000_000)
    ap.add_argument("--rx-cost", type=int, default=150,
                    help="T-states per receive interrupt (default 150)")
    ap.add_argument("--max-cycles", type=float, default=20e9,
                    help="T-state limit per run (default 20e9)")
    ap.add_argument("--candidates", nargs="+",
                    help="only these candidates (default: all in the bench)")
    ap.add_argument("--jobs", type=int, default=0,
                    help="parallel emulator runs (default: CPU count)")
    ap.add_argument("--mode", choices=[FLAT, BANKED], default=FLAT,
                    help="flat (default): unmodified decoders, everything "
                         "below 0xC000; banked: the banked ports with the "
                         "receive ring and paged output")
    ap.add_argument("--out", help="output directory (default: --results; "
                                  "banked mode: --results with -banked "
                                  "appended)")
    args = ap.parse_args(argv)
    if args.baud <= 0 or args.cpu_hz <= 0 or args.rx_cost < 0:
        ap.error("--baud and --cpu-hz must be > 0, --rx-cost >= 0")

    try:
        brows, bset = load_bench(args.results)
    except OSError as e:
        ap.error(f"cannot read bench results: {e}")
    if bset is None:
        print("warning: no packed/bench-settings.json; was the bench run "
              "with --keep-packed? Assuming no chunking.", file=sys.stderr)
    chunk = (bset or {}).get("chunk", 0)
    if args.candidates:
        brows = [r for r in brows if r["candidate"] in args.candidates]

    specs = args.files or (bset or {}).get("files") or [bench.DEFAULT_FILES]
    originals = {}
    for p in bench.collect_files(specs):
        if os.path.isfile(p):
            with open(p, "rb") as f:
                originals.setdefault(os.path.basename(p), f.read())

    kw = dict(baud=args.baud, cpu_hz=args.cpu_hz, rx_cost=args.rx_cost,
              max_cycles=args.max_cycles)
    settings = SimSettings(mode=args.mode, **kw)
    packed_dir = os.path.join(args.results, "packed")
    rows = simulate_all(brows, originals, packed_dir, chunk, settings,
                        jobs=args.jobs or None)
    flat_rows = None
    if args.mode == BANKED:
        # Same rows in flat mode, for the banked-vs-flat table only.
        print("flat runs for the comparison table:", file=sys.stderr)
        flat_rows = simulate_all(brows, originals, packed_dir, chunk,
                                 SimSettings(mode=FLAT, **kw),
                                 jobs=args.jobs or None)

    out = args.out or (args.results.rstrip("/") + "-banked"
                       if args.mode == BANKED else args.results)
    os.makedirs(out, exist_ok=True)
    csv_path = os.path.join(out, "sim.csv")
    md_path = os.path.join(out, "sim-report.md")
    write_csv(rows, csv_path)
    write_report(rows, settings, md_path, args.results, bset, flat_rows)
    print(f"wrote {csv_path}\nwrote {md_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
