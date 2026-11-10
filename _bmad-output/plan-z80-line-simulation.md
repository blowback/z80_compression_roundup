---
title: 'Z80 line simulation for compression candidates'
type: feature
ticket: ''
created: '2026-11-09'
status: built
baseline_revision: 'NO_VCS'
route: full
route_source: auto
risk: low
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/research-z80-compression/research-z80-compression.md'
  - '{project-root}/_bmad-output/plan-z80-compression-benchmark-harness.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The harness estimates transfer time as max(line, decode) from published per-byte averages. On our corpus that model is unreliable. For example, it marks every candidate Z80-limited on the uniform 256 KB bitmap, and it ignores decoder stalls and UART receive cost.

**Approach:** Run each candidate's real Z80 decoder in a cycle-counted emulator. Feed it the compressed stream as it would arrive over the serial line, and stall the decoder whenever it reads a byte that hasn't arrived yet. Measure the total time to the decoder's return per file and candidate, check the output against the original, and report it next to the harness's estimate. The emulator also models the MicroBeast's page mapping and a receive ring with flow control, so the follow-up build can add bank-aware decoders.

**Decisions (2026-11-09):**
- The user chose to model banking with real `OUT` writes for all eight decoders. That work is split off as build 2 (`deferred-work.md`).
- This build provides the emulator support build 2 needs: ports `0x70`–`0x73`, and the ring and flow-control receive mode.
- Accepted layout for build 2:
  - Output in windows 0–2, with the output pointer kept in window 2. That gives 32 KB of history, so offsets are capped at 32,768.
  - Window 3 holds an 8 KB receive ring plus the code, buffers and stack.
- Until build 2, whole-file runs mark the 256 KB file `too-large`. Chunked runs simulate it.

## Boundaries & Constraints

**Always:**
- Arrival model:
  - Byte *i* (0-based) is due at T-state `(i + 1) × T_byte + rx_cost`, where `T_byte = cpu_hz × 10 ÷ baud` (4,166.7 at the defaults).
  - Each byte's receive interrupt also takes `rx_cost` T-states from the decoder when that byte arrives.
  - Defaults: 19200 baud, 8 MHz, `rx_cost` 150. All three can be set on the command line.
- Flat mode:
  - The compressed stream sits in a linear buffer.
  - A stall happens only when the decoder reads a buffer address whose byte hasn't arrived. The clock then jumps to that byte's arrival time.
  - Decoders are unmodified, apart from the origin, buffer addresses and the defines in uniabis's `build.bat`.
- Paging follows the MicroBeast:
  - There are four 16 KB windows over 1 MB of physical memory, RAM being pages `0x20`–`0x3F`.
  - `OUT` to a port where `port & 0xF0 == 0x70` sets window `port & 3` when `port & 4` is 0, and switches paging on or off with bit 0 when `port & 4` is 1. This matches `beastem` `src/beast.cpp` around line 1575.
  - Flat mode maps windows 0–3 to RAM pages `0x20`–`0x23`.
- Ring mode (for build 2):
  - The simulated interrupt writes each arriving byte into an 8 KB ring at a fixed address and advances a write pointer kept in memory.
  - The decoder keeps its read pointer in memory too.
  - When the ring is full, the line pauses (RTS/CTS), and the remaining arrivals shift later.
- Decoder output must equal the original file byte for byte. If it doesn't, the row is `mismatch` and gets no time.
- Label simulated times "simulated", never "estimated" or "measured on hardware".
- Pin every new dependency in `harness/tools.json`.

**Never:**
- No decoder changes beyond the origin, buffers and defines. The banked decoder ports are build 2.
- Don't recompress in the simulator. It uses the packed files the bench run saved.
- Don't change the harness's estimate model or its existing CSV columns.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | Packed files from a bench run | Simulated time, stall and receive T-states, and limiting factor per (file, candidate) | No error expected |
| Line-bound decoder | ZX0 on a `.COM` file | Simulated time at least the line time and within 10% of it | No error expected |
| Decoder-bound | upkr or Shrinkler | Simulated time well above line time | No error expected |
| Doesn't fit | Output plus input plus the reserved area exceeds 64 KB | Status `too-large` | Continue |
| Wrong output | Decoder output differs | Status `mismatch` | Continue |
| Runaway | Cycle limit exceeded (default 20 × 10⁹ T) or PC leaves the decoder | Status `error` with a reason | Continue |
| No packed file | The bench row failed or the file is missing | Status `skipped` | Continue |
| Chunked bench run | Pieces saved per file | Pieces stream back to back on one line. The decoder restarts per piece, and the time runs to the last piece's return | Same rules per piece |
| Baseline | `none` | Line time plus receive cost; no emulation | No error expected |
| Paging | A test program writes to `0x70`–`0x73` | Later accesses hit the selected physical pages | No error expected |
| Ring full | A test ring decoder that reads slowly | The line pauses and later arrivals shift; the bytes received are unchanged | No error expected |

</frozen-after-approval>

## Code Map

- `harness/bench.py` -- add `--keep-packed`, which saves each packed output to `<out>/packed/<file>.<candidate>[.<piece>]`. Reuse `run_cmd`, `expand`, `collect_files` and `Settings`. Don't change the estimate maths or the existing columns.
- `harness/candidates.py` -- the candidate names that the decoder specs key on.
- `harness/tools.json` and `harness/build_tools.py` -- add three manifest entries:
  - `superzazu/z80`, MIT, pinned to `d64fe10a2274e5e40019b1086bf7d8990cbc5f23`;
  - `uniabis/z80depacker`, pinned to `d57ec2ec878b9b66f4ede7f6ebdd59af913cda24`;
  - `z00m128/sjasmplus`, tag `v1.24.0`.

  The existing patches, `{compat}` and build steps are enough.
- superzazu/z80 API: `read_byte`, `write_byte`, `port_in` and `port_out` callbacks, plus `userdata`, a public `cyc` counter and `z80_step`. Add stall and receive cycles to `cyc` in the callbacks or the step loop.
- Decoder calling conventions, per each `.asm` header (check each before wiring it up):
  - ZX0, LZSA2, aPLib, LZ4 and Exomizer: HL = source, DE = destination.
  - RIP: IX = source.
  - upkr: IX = source, shadow DE' = destination, with a 320 B probs table 256-aligned via `UPKR_PROBS_ORIGIN`.
  - Shrinkler: a 2 KB `probs` buffer, default `01000h`, which must be relocated.
  - Exomizer: a decrunch table at `mapbase`.
- The decoder `.asm` files have no `ORG`. Assemble a generated wrapper (`ORG`, the defines, `INCLUDE`) with `sjasmplus --raw`.
- The decoder build defines are in z80depacker `build.bat`. The ones needed:
  - `deexoopt_x3`: `-DOPTIMIZE_JUMP=1 -DPFLAG_CODE=39 -Dmapbase=… -Dliterals=1`.
  - upkr: `-DUPKR_ALLOW_SELF_MODIFYING -DUPKR_UNPACK_SPEED`, with `UPKR_PROBS_ORIGIN` placed in the reserved area.

## Tasks & Acceptance

**Execution:**
- [x] `harness/tools.json` -- add the z80 core, z80depacker and sjasmplus entries, and a build step for `harness/sim/linesim.c` against the core -- pinned, reproducible build
- [x] `harness/sim/linesim.c` -- CLI with flat and ring modes and MicroBeast paging. Loads a decoder binary, the stream and the entry registers, then runs the arrival, stall and receive model to a sentinel `HALT`. Writes the output and one JSON line (cycles, stall, receive, flow-pause, status) -- the fast part, written in C
- [x] `harness/decoders.py` -- per candidate: decoder source, defines, buffer placement and entry registers. Assembles the decoders into `harness/tools/decoders/` -- decoder details kept in one place
- [x] `harness/bench.py` -- `--keep-packed` -- the simulator reuses the bench's output
- [x] `harness/linesim.py` -- CLI (`--results DIR`, `--baud`, `--cpu-hz`, `--rx-cost`, `--max-cycles`, `--out`). Lays out memory, runs `linesim`, and writes `sim.csv` and `sim-report.md`. The report has per-file tables (simulated time, stall share, estimate, difference), per-type and overall tables, and a winner per file -- the deliverable
- [x] `harness/tests/test_linesim.py` -- every matrix row, using tiny hand-assembled test programs (a flat copier, a slow ring reader, a paging probe) rather than the real decoders -- covers the matrix
- [x] `harness/README.md` -- simulation section: model, memory layout, paging, ring mode, limits

**Acceptance Criteria:**
- Given a bench run with `--keep-packed`, when `linesim.py` runs, then every decoder, including Shrinkler, reproduces the original on every file that fits.
- Given the simulation results, when the report is read, then each row shows the simulated time and the harness estimate side by side.

## Design Notes

Flat-mode memory layout:
- Reserved area `0xC000–0xFFFF`: decoder code, relocated buffers, stack, and a sentinel return address that executes `HALT`.
- Output starts at `0x0100`.
- The input goes straight after the output's end.
- If output plus input doesn't fit below `0xC000`, the status is `too-large`.

"Limited by" is `line` when stall cycles are more than half of the decoder's total, otherwise `Z80`.

## Verification

**Commands:**
- `python3 -m unittest discover -s harness/tests` -- expected: all tests pass
- `python3 harness/build_tools.py` -- expected: every tool built, including linesim and the assembled decoders
- `python3 harness/bench.py --keep-packed --out harness/results && python3 harness/linesim.py --results harness/results` -- expected: `sim.csv` and `sim-report.md` written. Every bench `ok`/`ok-unverified` row that fits is simulated `ok`
- `python3 harness/bench.py --keep-packed --chunk 16384 --out harness/results-chunked && python3 harness/linesim.py --results harness/results-chunked` -- expected: `v80000.vbm` simulated `ok` for every candidate whose bench row is good

## Implementation Notes

- **Decoders (fastest per format in the z80depacker readme):** `dzx0_mega` (zx0, salvador), `unlzsa2_fast`, `unaplib_fast` (both aPLib packers), `deexoopt_x3` with the `-P39` defines, `derip_fast`, `unlz4_spke_fast`, `unpack_upkr_z80` speed build, and `deshrinkler_np` (Shrinkler `-b`). The assembled sizes match the readme: 673, 207, 235, 244, 231, 87, 195 and 206 B.
- **Source rewrites beyond `-D` defines:** Shrinkler `probs = 01000h` and RIP `workarea=#0800` are plain assignments, not defines, so the copy that gets assembled rewrites them to 0xD800 and 0xE800. RIP also has `OUTPUT derip.C`, which would send the code to a different file than `--raw`. It is commented out. Each rewrite must match exactly once or the assembly fails.
- **sjasmplus is built with `USE_LUA=0`.** Its LuaBridge submodule is not cloned by `build_tools.py`, and the decoders don't need Lua.
- **New manifest key `sources`:** the `z80` entry lists `sim/linesim.c`, and the tool is rebuilt when that file is newer than its stamp. Without it, edits to `linesim.c` would need `--force`. `build_tools.py` also assembles the decoders on every run, which takes under a second, and lists them in the summary.
- **Additions not in the plan's CLI lists:** `linesim.py --files` (the originals; by default the file list the bench recorded), `--candidates` and `--jobs`. `bench.py --keep-packed` also writes `packed/bench-settings.json`, because `results.csv` has no chunk size and linesim must split the originals the same way the bench did.
- **Overread:** LZSA2 and Shrinkler read 1-2 bytes past the end of the stream. Those reads stall as if padding bytes followed on the line and are reported in `overread`. The output is correct regardless (the memory there is zero).
- **Stall granularity:** the core adds an instruction's whole T-state count before it executes, so a stall ends that instruction at the byte's due time. Each stall can under-count by up to one instruction (about 20 T, 0.5% of a byte time). This is documented in the README.
- **Whole-file run (`harness/results/`):** 132 rows. Every bench `ok`/`ok-unverified` row that fits simulated `ok`, Shrinkler included. `too-large`: lz4 on ANTFORTH.COM (30,720 + 18,203 B exceeds the 48,896 B area) and all of `v80000.vbm`. `skipped`: the four bench failures (OaPACK and RIP on v48000/v80000). On v80000.vbm the only simulated row is the baseline, so the report names `none` as its winner. Read that file from the chunked run.
- **Chunked run (`harness/results-chunked/`):** `v80000.vbm` simulated `ok` for every candidate whose bench row is good (all except OaPACK, which timed out). Every candidate finishes in 0.69-0.91 s and is Z80-limited. The estimate gave 1.2-324 s for it (upkr 104.9 s simulated 0.75 s, Shrinkler 324 s simulated 0.91 s), because the published per-byte averages don't apply to one long run of a single byte.
- **Line-bound check:** ZX0 on ANTFORTH.COM simulates 7.918 s against a line time of 7.918 s. The `.COM` LZ rows sit within 0.1% of the line time, with stall shares of 82-98%. upkr and Shrinkler are Z80-limited on every file. On `.COM` files their simulated times range from 0.43x to 1.32x the estimate (BBCBASIC.COM is slower than estimated, ANTVANIA.COM much faster), so the per-byte averages are not a reliable guide for them in either direction.
- **Tests:** 59 pass. That is 31 existing tests, 5 new `--keep-packed` tests in `test_bench.py`, and 23 in `test_linesim.py` that cover every matrix row with hand-assembled copiers, a spin loop, a jump out of the decoder, paging probes (windows 0-3, and paging off) and fast/slow ring readers. The `test_linesim.py` tests need the `linesim` binary and are skipped if it isn't built.

## Plan Change Log

## Review Triage Log

**Pass 1 (quick lens, 2026-11-09):** 3 findings. high 0, medium 0, low 3, false 0, maybe-false 0. 2 patched, 1 rejected under the plan-edit rule.

| # | Finding | Verdict | Route | Evidence / action |
|---|---|---|---|---|
| 1 | The `cb_read` overread window can cover decoder code, so opcode fetches stall when the input ends within 256 B of `0xC000` | low | patch | Confirmed in the code. None of the real runs is affected: every `ok` single-piece row has more than 256 B of headroom, checked against `sim.csv`. The test layout does trigger it. The fix is a direct correction: clip the window at the code range. |
| 2 | An overread past a non-last piece reads the next piece's bytes instead of inserted padding, so later pieces don't shift | low | patch | Happens in every chunked run. Overread totals on `v80000.vbm`: LZSA2 32 B, Shrinkler 16 B; on the `.COM` files: 3–4 B. The README and the C header describe padding. The fix: shift the base of later pieces by each piece's overread. |
| 3 | Commenting out RIP's `OUTPUT derip.C` goes beyond the frozen "origin, buffers, defines" | low | rejected (fix is a plan edit) | The change doesn't alter the decoder code, and without it there is no raw output. It is recorded in the README and the report. Raised with the human at presentation. |
