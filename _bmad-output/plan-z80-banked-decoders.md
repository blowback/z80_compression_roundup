---
title: 'Banked Z80 decoders and banked line-simulation mode'
type: feature
ticket: ''
created: '2026-11-09'
status: built
baseline_revision: 'NO_VCS'
route: full
route_source: auto
risk: medium
review: quick
review_source: pinned
lenses_ran: [quick]
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/plan-z80-line-simulation.md'
  - '{project-root}/harness/README.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The flat line simulation needs output and input to fit below 0xC000, so `v80000.vbm` (256 KB) is `too-large` for every candidate, and the decoders never pay for paging or for reading a real receive ring.

**Approach:** Port all eight decoders to a banked memory model that uses real OUT writes to ports 0x70–0x72 and reads input from an 8 KB ring that the simulated interrupt fills under RTS/CTS flow control. Add a `banked` mode to linesim and linesim.py that runs every file whole.

## Boundaries & Constraints

**Always:**
- Accepted layout. The output is written in windows 0–2, starting at logical 0x0000 on RAM pages 0x20–0x22. When the output pointer reaches 0xC000, the decoder shifts the windows down one page with OUT writes, takes the next page into window 2 and subtracts 0x4000 from the output pointer and any live match source. That keeps 32 KB or more of history, so offsets can be up to 32,768. Window 3 is page 0x3F: the ring at 0xC000–0xDFFF, code at 0xE000–0xEFFF, one decoder buffer at 0xF000–0xF7FF, ring pointers and variables at 0xF800–0xF8FF, sentinel at 0xFF00 and stack top at 0xFFF0.
- The decoder proves each input byte is available from WP before reading it, never waits for a byte beyond the one it needs next, and stores RP at least every 256 bytes and before every wait. These checks are part of the measured time.
- Every LDIR that copies a match or a literal run is split at the 0xC000 output limit, the 0xE000 ring end and the available count.
- Ports keep the source decoder's algorithm. They change only input reads, output writes, copies and buffer addresses, and each port records its edit sites in a header comment.
- The round trip stays byte for byte: the output is read from physical pages 0x20 upwards and compared with the original.
- Flat mode and its results stay as they are.

**Never:**
- Changing decoder algorithms, or switching to a slower variant of a decoder.
- Reading the ring through the flat-mode stall in `cb_read`. In banked mode the decoder waits in its own code.
- Editing the z80depacker clone in place.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Whole corpus | 12 files, all candidates with a decoder, `--mode banked` | every row `ok`, output identical, `v80000.vbm` included, except where the stream holds a match longer than 65,535 bytes | those rows are `mismatch`, with the 16-bit length limit as the reason |
| Torture file | ~100 KB: a random block repeated at offset 32,640, a 10 KB incompressible run, a 40 KB run of one byte | every decoder reproduces it | mismatch → `mismatch` row |
| Slow decoder | Shrinkler/upkr fall behind the line | ring fills, line pauses, `flow_pause_t` > 0 | — |
| Fast decoder | LZ4 on a `.COM` | waits counted as `stall_t`, limited by `line` | — |
| Overread past stream end | decoder reads ahead of the last byte | no deadlock: bytes past the stream length count as available | — |
| Too-large banked | output > 496 KB (pages 0x20–0x3E) | `too-large` | reason names the page limit |
| Deadlock / runaway | decoder spins without progress | `error`, cycle limit | reason in row |

## Decisions (2026-11-09)

- Offsets: reuse the existing packed files, with no capped bench set. The byte-for-byte check turns any offset beyond the 32 KB window into `mismatch`. The torture file keeps its repeats at 32,640 or less, so every compressor's stream stays in range.
- Plan size: keep the full plan as one build.
- `v80000.vbm` length limit (decided after implementation): zx0, aPLib (apultra), upkr and Shrinkler encode this file as one match longer than 65,535 bytes, which their 16-bit Z80 length counts cannot hold. Those rows stay `mismatch` as a reported finding, and the ports are not extended.

</frozen-after-approval>

## Code Map

- `harness/sim/linesim.c`:
  - `reset_memory` sets the flat start state.
  - `cb_read` holds the flat stall. It must not apply in banked mode.
  - `service_line` already implements the ring, WP/RP and the RTS/CTS pause. Reuse it.
  - `cb_out` already handles paging.
  - Per-piece output is dumped with `peek` at logical addresses (main loop near line 580).
- `harness/linesim.py`:
  - `layout`, `run_linesim` and `simulate_row` build the flat command line.
  - Add the banked path alongside them.
  - Statuses and CSV fields are unchanged; `mode` is added to the report header.
- `harness/decoders.py`: `Decoder`, `assemble` and the layout constants. Add a banked layout and a banked decoder set assembled from `harness/sim/banked/`.
- `harness/tools/src/z80depacker/*.asm` are the sources to copy. Edit-site counts from the survey:
  - dzx0_mega: about 31 sites, repeated 8 ways; use macros.
  - unlzsa2_fast: about 19 sites. Needs peek and consume reads. The reads done by `add (hl)` and `or (hl)` must keep their flags.
  - unaplib_fast: about 15 sites, including the swapped write in CASE111. Define `SupportLongOffsets`.
  - deexoopt_x3: about 12 sites. The input pointer moves between HL and DE. Rewrite the `defb 250` trick at line 487 as a plain jump.
  - derip_fast: 4 sites. Check DE before the throwaway write at line 66. Fix the byte-counted jumps `$+3` and `$-8`.
  - unlz4_spke_fast: 7 sites. Carry must survive between `sub (hl)` and `sbc (hl)` at lines 103–104.
  - unpack_upkr_z80: 3 sites. Rebank inside its `exx` block, and keep the carry going into `sbc hl,bc` at line 203.
  - deshrinkler_np: 3 sites. Mind the `exx` sets and the `$+4` jump at line 116. IY is in use.
- `harness/tests/test_linesim.py` has the hand-assembled helpers (`run_raw`, `ring_reader`). Add the banked tests here.
- `harness/README.md` has a "Line simulation" section; extend it.

## Tasks & Acceptance

**Execution:**
- [x] `harness/sim/linesim.c`:
  - Add `--mode banked`. It is ring mode with a start state of windows 0x20, 0x21, 0x22, 0x3F.
  - Read piece output from physical 0x80000.
  - Add `--wait LO:HI`: cycles with PC in that range count as `stall`.
  - Add `--word ADDR=VALUE` with `@len` (piece stream length), poked before each piece.
  - Rationale: the existing ring model is reused, and the waits are measured.
- [x] `harness/sim/banked/banked.inc`:
  - The entry wrapper: set pages, set DE = 0, call the decoder, store RP and return.
  - The rebank routine, with a page counter kept in memory.
  - Ring-read macros (consume and peek), which keep the flags where a site needs them.
  - The chunked-copy helper `min(BC, 0xC000−DE, 0xE000−HL, available)`.
  - A single wait loop whose address range linesim gets as `--wait`.
  - Bytes past `@len` count as available.
  - Rationale: shared code keeps the eight ports small and consistent.
- [x] `harness/sim/banked/*.asm` -- one banked copy of each of the eight decoders, with an attribution and edit-site header -- the ports.
- [x] `harness/decoders.py` -- add the banked layout constants, a `BANKED` decoder set (sources from `sim/banked/`, buffers at 0xF000, 4 KB code limit), and assemble both sets in `build_all` -- one build for both modes.
- [x] `harness/linesim.py` -- add `--mode flat|banked` (default flat). In banked mode, use whole-file or chunked pieces, pass `--ring 0xC000:8192:WP:RP`, `--wait` and `--word`, and set `too-large` above 496 KB. The report states the mode -- runs every file whole.
- [x] `harness/tests/test_linesim.py` -- add banked start state, output across a rebank read from physical pages, `--wait` accounting, `@len`, no deadlock on overread, and the torture file through each banked decoder (skipped when the tools are not built) -- covers the matrix.
- [x] `harness/README.md` -- document banked mode, the layout and the port rules.

**Acceptance Criteria:**
- Given built tools, when `python3 harness/linesim.py --mode banked --out harness/results-banked` runs on the bench results, then every row with a decoder is `ok`, `v80000.vbm` included, and none is `too-large`. The exception is the `v80000.vbm` rows the Decisions name, which are `mismatch` with the 16-bit length reason.
- Given the torture file, when each banked decoder runs, then its output matches byte for byte and at least one rebank and one ring wrap happened.
- Given the banked results, then the report shows each candidate's banked total next to its flat total for the files that fit flat.

## Design Notes

Ring-read fast path for an HL input pointer, as an example; the counter is in IY or memory, depending on what the decoder leaves free:

```
        dec  iyl             ; bytes known available
        call z,ring_refill   ; slow: wrap at 0xE000, store RP, recount or wait
        ld   a,(hl)
        inc  hl
```

`ring_refill` counts the bytes available up to the ring end. When there are none, it waits in the `--wait` loop until WP moves.

## Verification

**Commands:**
- `python3 harness/build_tools.py` -- expected: 16 decoders assembled.
- `python3 -m unittest discover -s harness/tests` -- expected: all pass.
- `python3 harness/linesim.py --mode banked --out harness/results-banked` -- expected: no `error`, `mismatch` or `too-large`.

## Implementation Notes

- **Acceptance gap (needs a human decision):** the banked run on `harness/results` gives 123 `ok`, 5 `skipped` (bench failures, LZSA2 stream format) and 4 `mismatch`, all on `v80000.vbm`: zx0, aplib-apultra, upkr and shrinkler. Their packed streams encode one match longer than 65,535 bytes (the file is one byte repeated), and every Z80 decoder counts lengths in 16 bits. Checked: uniform runs of 65,000 bytes decode through the banked zx0 and upkr ports and 70,000 do not; the output is right up to byte 65,536 (upkr up to byte 65,537). The copy helpers copy 65,536 bytes for BC = 0, as the LDIR they replace does. salvador, Exomizer and LZ4 decode the file, because their compressors cap the length. Fixing it needs either a capped bench set (Decisions rule it out) or a decoder algorithm change (Never). The mismatch reason names the likely causes (a length over 65,535, or an offset beyond the 32 KB window).
- **Shared code** (`harness/sim/banked/banked.inc`): entry wrapper, `refill_hl/ix/de`, `ring_wait` (the `--wait` range is `ring_wait_loop..ring_wait_exit`), `rebank`/`rebank_hl`, `copy_lit`, `copy_match`, macros `RCHK_*`, `OCHK`, `OCHK_HL`, `LDI_LIT`. Counter in IYL; Exomizer and Shrinkler use IY, so they define `RING_COUNT_MEM` and keep it at 0xF80B. Rebanks are lazy: the windows shift when a byte is due at DE = 0xC000.
- **Additions not in the plan:** `linesim --word` also takes `@last`, poked at 0xF80E. Between pieces the wrapper waits for stream bytes the decoder never read, so the next piece starts at its first byte. After the last piece it does not wait, so no decoder waits for a byte it does not need. The wrapper keeps statistics words at 0xF810 (rebanks) and 0xF812 (ring wraps) for the tests. `linesim.py --mode banked` also runs the flat simulation in the same invocation for the "Banked vs flat" table, and defaults `--out` to `<results>-banked`. Mismatch reasons now give the first differing byte.
- **Ports:** aPLib (4), LZSA2 (3) had `jr` pushed out of range by the checks, now `jp`. RIP's `$+3`, `$-8` and `$+4`, Shrinkler's `$+4` and Exomizer's `defb 250` are labels or a plain `jp`. LZ4, aPLib, LZSA2 and Exomizer are copies with the unused IF branches dropped; upkr and Shrinkler keep the original text with inserted edits. Sizes: dzx0_mega 1217, unlzsa2_fast 720, unaplib_fast 782, deexoopt_x3_p39 800, derip_fast 691, unlz4_spke_fast 553, unpack_upkr_z80_speed 658, deshrinkler_np 681 B (banked.inc included).
- **Torture file:** 96,000 B (22,400 B random block, 10,240 B random, the block again at offset 32,640, 40,960 B run). lzsa2 raw blocks refuse inputs over 64 KB and RIP needs about 12 GB at 64 KB (it is killed above that here), so those two get a 65,040 B cut (run 10,000 B). Every decoder reproduces its file with at least one window shift and three or more ring wraps. Compressed streams are cached in `harness/tools/torture-cache/`.
- **Results:** banked vs flat over the files that fit flat: the LZ decoders are within +0.01 % (lzsa2 -0.03 %: no padding stall for its overread), upkr +1.4 %, Shrinkler +0.75 %. Only Shrinkler fills the ring (`flow_pause_t` > 0 on ANTFORTH.COM and BBCBASIC.COM). upkr never falls 8 KB behind on this corpus, so it never pauses the line. LZ4 on ANTFORTH.COM, `too-large` in flat, simulates 9.69 s in banked mode. Chunked banked run (`results-chunked`): 129 `ok`, 3 `skipped`.
- **Verification:** `build_tools.py` assembles 16 decoders. 78 tests pass (18 new: 7 linesim-level banked tests, 2 on banked.inc helpers, 8 torture runs, 1 report test). Flat `sim.csv` from a rerun is identical to `harness/results/sim.csv` and `harness/results-chunked/sim.csv`; the flat report only gains its "Memory mode" line.


## Review Triage Log

**Pass 1** (quick lens). Verdicts: 0 high, 1 medium, 4 low, 2 false, 0 maybe-false. Routed: 4 patch, 3 rejected, 0 deferred.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | Frozen block edited without a recorded human decision; Implementation Notes still say a decision is needed | false | rejected | The user chose "Accept as a finding" in this session (2026-11-09) before the frozen edit. The stale note and Verification line are plan text, and a fix that edits this plan is rejected. |
| 2 | `copy_match`/`copy_lit` copy nothing for BC = 0, where LDIR copies 65,536; dzx0's `inc bc` turned 0xFFFF into 0 | medium | patch | Confirmed in `banked.inc`; it changed the source decoders' behaviour and moved upkr's failure to byte 1. The helpers now copy 65,536 for BC = 0. The dzx0, aPLib and LZSA2 call sites now match the source's `ldir/inc c/ldi` and `ldi/ldir` for every BC. upkr now fails at byte 65,537, the other three at 65,536. |
| 3 | Banked `too-large` also rejects packed pieces over 65,535 B, but the docs say every file up to 496 KB runs whole | low | patch | `decoders.BANKED_STREAM_MAX` is checked in `banked_fit`. `MODE_TEXT`, the docstring and the README now state both limits. |
| 4 | Mismatch hint names only the 16-bit length | low | patch | The hint is added to every mismatch with output over 64 KB. It now also names an offset beyond the 32 KB window. |
| 5 | Matrix "Slow decoder" row is not shown for upkr | false | rejected | The row's behaviour (ring fills, line pauses) is tested with Shrinkler in `test_deshrinkler_np_pauses_the_line`. upkr never falls 8 KB behind the line on the corpus or the torture file, so the row's precondition does not hold for it. |
| 6 | lzsa2 and RIP get a 65,040 B torture variant, not the ~100 KB file | low | rejected | LZSA2 raw blocks cannot hold more than 64 KB, and RIP's compressor runs out of memory above it. The variant still has the repeat at offset 32,640, crosses 48 KB (rebank) and wraps the 8 KB ring. The defect is unlikely to matter, and no fix in code exists. |
| 7 | The LZSA stream-format banked `too-large` reason drops the limit | low | patch | `simulate_row` ignored `banked_fit`'s reason. It is now used. |
