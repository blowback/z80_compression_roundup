# Z80 compression benchmark harness

Measures how well each candidate compressor packs the files in `test_files/`
and estimates how long each packed file takes to reach an 8 MHz Z80 over a
19200 baud serial line. Candidates, versions and decoder costs come from
`_bmad-output/research-z80-compression/research-z80-compression.md`.

Python standard library only. Tool builds need git, gcc/g++, make and cargo.
The line simulation (`linesim.py`, see "Line simulation") adds a pinned Z80
core, the pinned z80depacker decoder sources and the pinned sjasmplus
assembler, all built by `build_tools.py`.

## Build the tools

```sh
python3 harness/build_tools.py            # all tools
python3 harness/build_tools.py rip upkr   # only these
python3 harness/build_tools.py --force    # rebuild even if already built
```

Each tool in `harness/tools.json` is cloned into `harness/tools/src/<name>`,
reset to a clean checkout at its pinned ref (a tag, or a full commit SHA for
repos without tags: ZX0, RIP, OaPACK), patched if the manifest says so, built,
and its binaries copied to `harness/tools/bin`. A stamp file next to the
binaries records the ref and commit they came from. A tool is skipped when its
binaries exist and the stamp matches the manifest ref. Build logs go to
`harness/tools/build-<name>.log`. The script ends with a built/skipped/failed
summary and exits non-zero if any tool failed.

To bump a version, change `ref` (and `version`) in `tools.json` and rerun.

RIP and OaPACK ship only Visual Studio projects and use MSVC-only C++
(`std::exception("msg")`, `__int64`, `1i64` literals, a non-const reference
bound to a temporary). The manifest applies small regex patches to the clean
checkout and force-includes `harness/compat/msvc_compat.h`. A patch that
matches nothing fails the build, so a changed upstream source is caught.

## Run the benchmark

```sh
python3 harness/bench.py --out harness/results
python3 harness/bench.py --chunk 16384 --out harness/results-chunked
python3 harness/bench.py --files test_files/*.COM --candidates none zx0 lzsa2
```

Options:

| Option | Default | Meaning |
|---|---|---|
| `--files` | `test_files/` | files, globs or directories |
| `--baud` | 19200 | line speed; 8N1 so bytes/s = baud / 10 |
| `--cpu-hz` | 8000000 | Z80 clock |
| `--overhead` | 0 | framing bytes added to each file's line time |
| `--chunk` | off | compress each file as independent pieces of N bytes and sum the sizes |
| `--timeout` | 600 | seconds per compressor or decompressor run |
| `--mem-limit-mb` | 12288 | address-space limit per tool run, 0 = none |
| `--candidates` | all | subset of candidate names |
| `--out` | `harness/results` | where `results.csv` and `report.md` go |
| `--keep-packed` | off | save packed outputs to `<out>/packed/` for `linesim.py` |

Each run happens in its own temp directory. The compressor is timed, then the
host decompressor (where there is one) unpacks the result and it is compared
byte for byte with the input. Status per (file, candidate):

| Status | Meaning |
|---|---|
| `ok` | round trip checked |
| `ok-unverified` | no host decompressor (Shrinkler, or apultra not built for OaPACK); size counted and marked † |
| `mismatch` | decompressor failed or output differs; size discarded |
| `timeout` | a tool run went over `--timeout` |
| `error` | compressor exited non-zero, was killed, or wrote nothing |
| `missing` | binary not built; warned once per candidate |

One failing candidate never stops the run. In chunked mode the first failing
piece decides the row's status.

`results.csv` has one row per (file, candidate). Its time columns
`est_line_s`, `est_decode_s` and `est_transfer_s` are estimates from the model
below; `compress_s` is measured host time. The report labels them the same
way ("Est. line (s)", "Est. decode (s)", "Est. transfer (s)").

`report.md` contains: the winner per file, a table per file ranked by
estimated time, per-type tables (`.COM` = executable, `.vbm`/`.vbp` =
bitmap), an overall table, the candidate list with decoder costs, and the
pinned versions. Group totals only sum files where the status is `ok` or
`ok-unverified`; a candidate with any other status in a group is flagged
**incomplete** and ranked below the complete ones.

Candidates are defined in `harness/candidates.py`. A candidate can have
several command variants chosen by input size: LZSA2 uses a raw block (what
the Z80 decoders read) up to 64 KB, and the LZSA stream format above that,
because `lzsa -r` refuses larger inputs.

## Estimate model

```
line time   = (compressed bytes + overhead) / (baud / 10)
decode time = original bytes x T-states per output byte / CPU Hz
estimate    = max(line time, decode time)
limited by  = "Z80" if decode time > line time, else "line"
```

T-states per output byte are the fastest-decoder figures from the research's
"Measured performance" table (uniabis/z80depacker): ZX0 73.9 (also used for
salvador), LZSA2 59.0, aPLib 112 (OaPACK and apultra), Exomizer 255, RIP 504,
LZ4 36.8, upkr 3200, Shrinkler 9900.

Limits of the model:

- It assumes receiving and decoding overlap completely. Real decoders stall
  when they read ahead of the data that has arrived, so the true time is at
  least the estimate and can be more.
- Decoder cost per output byte is an average measured on 16 KB MSX ROM
  blocks with MSX wait states. It varies with the data: long runs and long
  matches are cheaper per byte than literals.
- UART receive cost, banking across 16 KB pages and per-file framing beyond
  `--overhead` are not modelled.
- No Z80 emulator or simulated line in the estimate itself. `linesim.py`
  (below) simulates both and reports the result beside the estimate.
- Host compression time is wall-clock time on this machine.

## Line simulation

`linesim.py` runs each candidate's real Z80 decoder in a cycle-counted
emulator against a simulated serial line, checks the output byte for byte
and reports the **simulated** transfer time next to the bench's estimate.
Simulated times come from the emulator and the model below. They are not
measured on hardware.

```sh
python3 harness/build_tools.py          # also builds linesim and the decoders
python3 harness/bench.py --keep-packed --out harness/results
python3 harness/linesim.py --results harness/results

python3 harness/bench.py --keep-packed --chunk 16384 --out harness/results-chunked
python3 harness/linesim.py --results harness/results-chunked

# banked decoders: every file whole, through the receive ring
python3 harness/linesim.py --mode banked --out harness/results-banked
```

`bench.py --keep-packed` saves every packed output to
`<out>/packed/<file>.<candidate>` (`.<piece>` appended in chunked runs) and
the run's settings to `<out>/packed/bench-settings.json`. The simulator only
uses those files; it never recompresses.

| Option | Default | Meaning |
|---|---|---|
| `--results` | `harness/results` | bench output made with `--keep-packed` |
| `--files` | the bench's file list | where the original files are |
| `--baud` | 19200 | line speed, 8N1 |
| `--cpu-hz` | 8000000 | Z80 clock |
| `--rx-cost` | 150 | T-states per receive interrupt |
| `--max-cycles` | 20e9 | T-state limit per run |
| `--candidates` | all | subset of candidates |
| `--jobs` | CPU count | emulator runs in parallel |
| `--mode` | `flat` | `flat`: unmodified decoders; `banked`: the banked ports (see "Banked mode") |
| `--out` | `--results` (banked: `--results` + `-banked`) | where `sim.csv` and `sim-report.md` go |

### Pieces

- `harness/sim/linesim.c`: the emulator driver, built against the pinned
  `superzazu/z80` core (MIT) by `build_tools.py` (manifest entry `z80`; it is
  rebuilt when `linesim.c` changes).
- `harness/decoders.py`: which decoder each candidate uses, its entry
  registers, defines and buffer addresses. Assembles the decoders from the
  pinned `uniabis/z80depacker` sources with the pinned `sjasmplus` into
  `harness/tools/decoders/`, and the banked ports from `harness/sim/banked/`
  into `harness/tools/decoders/banked/` (16 decoders; `build_tools.py`
  runs it every time; `python3 harness/decoders.py` runs it alone).

| Candidates | Decoder | Entry |
|---|---|---|
| zx0, salvador | `dzx0_mega` | HL = input, DE = output |
| lzsa2 | `unlzsa2_fast` | HL, DE |
| aplib-oapack, aplib-apultra | `unaplib_fast` | HL, DE |
| exomizer | `deexoopt_x3` with `-DOPTIMIZE_JUMP=1 -DPFLAG_CODE=39 -Dmapbase=0xE000 -Dliterals=1` | HL, DE |
| rip | `derip_fast` | IX = input, DE = output - 1 |
| lz4 | `unlz4_spke_fast` | HL, DE |
| upkr | `unpack_upkr_z80` with `-DUPKR_ALLOW_SELF_MODIFYING -DUPKR_UNPACK_SPEED -DUPKR_PROBS_ORIGIN=0xD000` | IX = input, DE' = output |
| shrinkler | `deshrinkler_np` | IX, DE |

The decoder sources are unchanged apart from the origin, buffer addresses and
defines. A generated wrapper sets `ORG 0xC000` and `INCLUDE`s a copy of the
source. In that copy, Shrinkler's `probs = 01000h` becomes `0D800h` and
RIP's `workarea=#0800` becomes `#E800`. RIP's `OUTPUT derip.C` line is
commented out, because it would send the code to another file.

### Model

- `T_byte = cpu_hz x 10 / baud` (4,166.7 T-states at the defaults). Byte *i*
  of the line (0-based) finishes on the wire at `(i + 1) x T_byte`. Its
  receive interrupt takes `rx_cost` T-states, so the byte is due at
  `(i + 1) x T_byte + rx_cost`.
- Flat mode (used for every candidate): the compressed stream sits in a
  linear buffer. When the decoder reads a buffer byte that is not due yet,
  it stalls and the clock jumps to the byte's due time. Interrupts that fall
  while the decoder is running add `rx_cost` to the clock. Interrupts that
  fall while it waits cost nothing extra, because it was idle anyway.
- The time is the T-state count when the decoder returns (to a sentinel
  `HALT`), divided by `cpu_hz`.
- Chunked runs: the pieces stream back to back on one line. The decoder is
  restarted for each piece as soon as the previous one returns, with a fresh
  memory image. The time runs to the last piece's return.
- `none` (baseline) is not emulated: its time is the last byte's due time,
  `n x T_byte + rx_cost`.

Columns in `sim.csv`: `sim_s` and `sim_cycles` (simulated time); `stall_t`
(T-states spent waiting for bytes, including interrupts that ran meanwhile);
`rx_t` (interrupt T-states taken from the running decoder); `rx_total_t`
(bytes received x `rx_cost`); `flow_pause_t` (ring mode only, see below);
`overread`; `stall_share` = `stall_t / sim_cycles`; `line_s` (packed bytes x
`T_byte` / `cpu_hz`); `limited_by` (`line` when stall is more than half the
simulated time, else `Z80`); `est_transfer_s` and `est_limited_by` (the
bench's estimate, from `results.csv`); `diff_s` and `diff_pct` (simulated
minus estimate). The bench's own columns are unchanged.

Statuses:

| Status | Meaning |
|---|---|
| `ok` | decoder output matches the original; time given |
| `too-large` | output + input do not fit between 0x0100 and 0xC000 |
| `mismatch` | decoder output differs from the original; no time |
| `error` | emulator stopped: cycle limit, PC left the decoder, or a `HALT` other than the sentinel |
| `skipped` | bench row failed, packed file missing, no decoder for the candidate, or linesim/decoders not built |

`sim-report.md` has a winner per file (simulated, with the estimate's
winner beside it), a table per file ranked by simulated time with stall
share, line time, estimate and difference, per-type and overall tables, and
the decoder list.

### Memory layout (flat mode)

| Logical address | Use |
|---|---|
| 0x0100 | output starts here |
| after the output | compressed input |
| 0xC000-0xCFFF | decoder code |
| 0xD000 | upkr probs (320 B, 256-aligned) |
| 0xD800-0xDFFF | Shrinkler probs (2 KB) |
| 0xE000 | Exomizer decrunch table (`mapbase`) |
| 0xE800 | RIP work area (0x5C2 B) |
| 0xFF00 | sentinel `HALT`, pushed as the return address |
| 0xFFF0 | initial stack top |

### Paging

The emulator follows the MicroBeast memory system (as `beastem`
`src/beast.cpp` does): 1 MB physical, ROM in pages 0x00-0x1F, RAM in pages
0x20-0x3F, four 16 KB windows. `OUT` to a port with `port & 0xF0 == 0x70`
sets window `port & 3` to the data byte when `port & 4` is 0, and turns
paging on or off with data bit 0 when `port & 4` is 1. With paging off the
logical address is the ROM address. Writes to ROM are ignored, and pages
above 0x3F read 0xFF. Flat mode starts with paging on and windows 0-3 on
RAM pages 0x20-0x23. The flat decoders never write to these ports; the
banked ports do (see "Banked mode").

### Ring mode

`linesim --mode ring --ring ADDR:SIZE:WP:RP` is the receive model banked
mode uses (`--mode banked` is ring mode with the banked start state). The simulated
interrupt writes each arriving byte at the address stored at `WP`, advances
that pointer inside the ring of `SIZE` bytes (a power of two) at `ADDR`, and
stores it back. The decoder keeps its read pointer at `RP` and waits on `WP`
itself, so there are no emulator stalls in this mode. When the ring holds
`SIZE - 1` unread bytes the line pauses (RTS/CTS) until the decoder frees a
slot; every later byte shifts by the pause (`flow_pause_t`).

### Banked mode

`linesim.py --mode banked` runs a banked port of each decoder
(`harness/sim/banked/<decoder>.asm`, sharing `harness/sim/banked/banked.inc`).
The ports read the compressed stream from the receive ring and page their
output through the MicroBeast windows with real `OUT` writes, so every file
runs whole whose output is at most 496 KB (RAM pages 0x20-0x3E) and whose
packed pieces are at most 65,535 bytes (the length is a 16-bit word),
`v80000.vbm` included. Chunked bench runs work too:
the pieces follow each other through the same ring.

Memory layout (logical addresses):

| Address | Use |
|---|---|
| 0x0000-0xBFFF | output, windows 0-2; starts at 0x0000 on RAM pages 0x20-0x22 |
| 0xC000-0xDFFF | 8 KB receive ring (window 3 = RAM page 0x3F throughout) |
| 0xE000-0xEFFF | code: `banked.inc` and the port (4 KB limit) |
| 0xF000-0xF7FF | the decoder's buffer (upkr probs, Shrinkler probs, Exomizer table or RIP work area) |
| 0xF800 / 0xF802 | ring write pointer (interrupt) / read pointer (decoder) |
| 0xF804 | stream length of the piece (`linesim --word 0xF804=@len`) |
| 0xF80E | 1 for the last piece (`--word 0xF80E=@last`) |
| 0xF810 / 0xF812 | window shifts / ring wraps so far (statistics for the tests) |
| 0xFF00 | sentinel `HALT`; stack top 0xFFF0 |

How a port works:

- Entry: `banked_entry` sets the windows to 0x20, 0x21, 0x22, 0x3F with
  `OUT` to ports 0x70-0x73, loads HL from RP (the piece's first byte in the
  ring) and DE = 0, and calls the port. After it returns, RP is moved to the
  end of the piece's stream, and the wrapper returns to the sentinel.
- Output: when the output pointer DE has reached 0xC000 and another byte is
  due, the decoder shifts the windows down one page (window 2 takes the
  next page), and subtracts 0x4000 from DE and from a live match source in
  HL. At least 32 KB of history stays mapped, so offsets up to 32,768 work.
  An offset beyond that, which the existing packed files could contain,
  shows up as `mismatch`. The output is read back from physical 0x80000 (page
  0x20) upwards and compared byte for byte.
- Input: the decoder reads straight from the ring. Before each byte it
  proves the byte has arrived. A counter (IYL, or a memory byte for
  Exomizer and Shrinkler, which use IY) holds the bytes already proven.
  When it runs out, `refill` wraps the pointer at 0xE000, stores RP, and
  counts the bytes available from WP, capped at the ring end, 255 and the
  bytes left in the stream. It never waits for more than the next byte,
  stores RP at least every 256 bytes and always before it waits, and waits
  in one loop (`ring_wait_loop`) whose address range linesim gets as
  `--wait`. These checks run in the simulated decoder, so their cost is in
  the simulated time.
- Bytes past the stream length count as available, so a decoder that reads
  a byte or two ahead does not wait for bytes that never come. Unread
  stream bytes are only waited for between pieces, where the next piece
  must start after them.
- Copies: every LDIR that copies a match (`copy_match`) or a literal run
  (`copy_lit`) is split at the 0xC000 output limit, the 0xE000 ring end and
  the proven count, with a window shift or a wait between the parts.
- Port rules: the decoder's algorithm is unchanged (no slower variant);
  only input reads, output writes, copies and buffer addresses change, and
  each port's header lists its edit sites, marked `BANKED` in the code.
  Where the added checks pushed a relative jump out of range it became an
  absolute one, and byte-counted jumps (`$+3`, `defb 250` tricks) became
  labels.

`linesim` options added for banked mode: `--mode banked` (ring mode with the
banked start state, re-set before each piece; output zeroed and read at
physical 0x80000), `--wait LO:HI` (T-states with PC in that range, and
interrupts taken there, count as `stall` rather than decoder time or `rx`)
and `--word ADDR=VALUE` (a 16-bit word stored before each piece; VALUE may
be `@len` or `@last`).

In the banked report, *Stall* is the time spent in the wait loop and
`flow_pause_t` is the time the line was held off (RTS/CTS) because the ring
was full. The report ends with a **Banked vs flat** table: each candidate's
banked total next to its flat total over the files that fit flat memory
(linesim.py also runs the flat simulation for it). Banked `too-large` means
output over 496 KB (pages 0x20-0x3E) or a packed piece over 65,535 bytes
(the length is a 16-bit word).

`harness/tests/test_linesim.py` covers banked mode with hand-assembled
probes (start state, physical output across a window shift, `--wait`,
`@len`/`@last`, too-large, runaway), two small programs built on
`banked.inc` (overread past the stream end, split copies) and a ~96 KB
torture file through every banked decoder: a 22,400-byte random block
repeated at offset 32,640, 10 KB of incompressible bytes and a 40 KB run
of one byte. Each torture run must match byte for byte with at least one
window shift and one ring wrap. lzsa2 (raw blocks stop at 64 KB) and RIP
(which needs about 12 GB and 45 s even at 64 KB) get the file with the run
cut to 10,000 bytes (65,040 bytes). Compressed torture streams are cached
in `harness/tools/torture-cache/`; tests whose tools are not built are
skipped.

### Limits

- Flat mode needs output + input below 0xC000 (48,896 bytes together), so
  the whole-file 256 KB `v80000.vbm` is `too-large` there. Banked mode and
  the chunked run (16 KB pieces) simulate it.
- The Z80 decoders count match lengths in 16 bits. The packed whole-file
  `v80000.vbm` streams from zx0, apultra, upkr and Shrinkler encode one
  match longer than 65,535 bytes (the file is a single byte repeated), so
  their banked rows are `mismatch`: the output is right up to byte 65,536
  and then wrong (upkr up to byte 65,537). This is the decoders' limit, not
  the banking: uniform runs of 65,000 bytes decode and 70,000 do not, and
  salvador, Exomizer and LZ4, whose compressors cap the length, decode the
  file. The mismatch reason names this and the other likely cause, a match
  offset beyond the 32 KB of history banked mode keeps mapped.
- Some decoders read a byte or two past the end of the stream (LZSA2 and
  Shrinkler do here). Those reads are treated as padding bytes that follow
  the stream on the line, so they can add a stall of a byte time or two.
  In chunked runs the padding goes on the line after that piece, so every
  later piece arrives that many bytes later. The count is in `overread` and
  the row's note. On hardware the host would have to send that padding. The
  overread window never covers the decoder code, so code fetches just after
  an input that ends at 0xC000 are not counted as overread or stalled.
- The core adds an instruction's whole T-state count before it executes, so
  a stall ends that instruction at the byte's due time. Each stall can
  under-count by up to one instruction's length (about 20 T-states, 0.5% of
  a byte time).
- The receive interrupt is a fixed cost, not emulated code, and no wait
  states are modelled. The MicroBeast's real UART and interrupt timing,
  framing overhead and host-side gaps between bytes are not modelled.
- The LZSA2 stream format (bench fallback above 64 KB) has no decoder here.
  Those rows are `too-large` or `skipped`.
