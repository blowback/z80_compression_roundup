# Digest: streaming decompression on the Z80 (r1-1)

Accessed 2026-11-08. Budget used: about 12 sources, about 22 tool calls.

**Answer first.** Every LZ-family Z80 decoder whose source I read (ZX0, LZSA1/2, aPLib/apultra, Exomizer 3, upkr, Shrinkler) reads its input strictly forwards, and each has a "backwards" build option that you simply leave off. None of them needs random access to the compressed data. So all of them can be made to stream, and the real question is speed.

On the uniabis benchmark (262,144 bytes of MSX ROM, MSX timings), the two entropy coders cost far more per compressed byte than an 8 MHz Z80 has to spare at 1920 B/s (about 4,167 T-states per byte):
- upkr: about 6,400 T-states per compressed byte.
- Shrinkler: about 17,000 T-states per compressed byte.

So the decoder, not the serial link, would set the total time. The LZ decoders (Exomizer about 420, ZX0 about 170, LZSA2 about 90, LZ4 about 50 T-states per compressed byte) all fit easily. Of those, Exomizer, aPLib and ZX0 give the smallest output.

Derived total-time estimate for that corpus. It assumes reception and decoding overlap fully and ignores protocol overhead. Decode time is the benchmark's clock count divided by 8 MHz.

| Scheme | Compressed bytes | Transfer at 1920 B/s | Decode at 8 MHz (MSX-timed) | Expected total (the larger of the two) |
|---|---:|---:|---:|---:|
| none | 262,144 | 136.5 s | 0 | ~137 s |
| upkr (--z80, speed unpacker) | 151,192 | 78.7 s | 120.4 s | **~120 s (limited by the decoder)** |
| Shrinkler (deshrinkler_p1) | 153,136 | 79.8 s | 325.8 s | ~326 s (limited by the decoder) |
| Exomizer 3.1 -P39 (deexoopt_x3) | 159,597 | 83.1 s | 8.4 s | **~83 s** |
| aPLib (unaplib_fast) | 161,422 | 84.1 s | 3.7 s | ~84 s |
| ZX0 (dzx0_standard) | 162,051 | 84.4 s | 3.4 s | ~84 s |
| MegaLZ (unmegalz_fast) | ~167,609 | 87.3 s | 2.5 s | ~87 s |
| LZSA2 (unlzsa2_fast) | 167,410 | 87.2 s | 1.9 s | ~87 s |
| LZ4 (unlz4_spke_fast) | 191,240 | 99.6 s | 1.2 s | ~100 s |

Caveats:
- The corpus is MSX BIOS ROM, not CP/M files.
- The clocks are MSX timings, which include M1 wait states (LDIR works out to about 23 T-states per byte here). Plain Z80 timings would be somewhat lower, but not by the roughly 35% upkr would need.
- The UART receive cost is not included.
- The entropy coders cost a roughly constant amount per *output* byte (upkr about 3,670 T-states). Their cost per *input* byte therefore gets worse as the data compresses better.

---

## Per-decoder tables

### ZX0 (Einar Saukas)
| Attribute | Value |
|---|---|
| Can it stream? | Yes, with edits |
| Direction | Forward by default. A backwards variant is optional (`-b` compressor option plus a separate decoder) |
| Input access | `dzx0_standard`: HL is the source pointer. It reads input in three places: `ldir` copies literals straight from the source; `ld c,(hl) / inc hl` reads the offset low byte; `ld a,(hl) / inc hl` reloads 8 bits for the interlaced Elias-gamma codes. HL is swapped with the offset through `ex (sp),hl`. Access is strictly sequential. To stream, replace the literal `ldir` with a loop that calls get-byte, or make sure N bytes are already buffered |
| Window / offset limit | Compressor `MAX_OFFSET_ZX0 32640`. Quick mode (`-q`) uses `MAX_OFFSET_ZX7 2176` |
| RAM needs | Uses the stack only (2 words). Matches are copied from the output with `ldir` (output minus offset) |
| Decode cost | 4.53x LDIR (standard, 68 bytes); 3.21x LDIR (mega, 673 bytes). About 169 T-states per compressed byte (derived) |

### LZSA1 / LZSA2 (Emmanuel Marty; Z80 code by introspec and uniabis)
| Attribute | Value |
|---|---|
| Can it stream? | Yes, with edits |
| Direction | Forward. `BACKWARD_DECOMPRESS` is an optional define |
| Input access | Reads use `ld x,(hl) : NEXT_HL` throughout, and literals are copied with `ldir`. In LZSA2 the nibble read peeks `ld a,(hl)` and then reads the same byte again with `NEXT_HL` (`unlzsa2_small.asm` lines 179-180). That is still sequential, but a get-byte routine needs a one-byte "peek" cache |
| Window / offset limit | 16-bit offsets, stored negated. The format docs say "up to 64 Kb of back-references"; LZSA2 has 5-, 9-, 13- and 16-bit offsets. The stream format is made of frames that each expand to at most 64 KB, with a 3-byte length header and a 3-byte zero end-of-data frame |
| RAM needs | None beyond registers and the stack |
| Decode cost | LZSA1 fast 1.60x LDIR; LZSA2 fast 2.56x LDIR. About 92 T-states per compressed byte for LZSA2 (derived) |

### aPLib / apultra (Z80 `unaplib_small`)
| Attribute | Value |
|---|---|
| Can it stream? | Yes, with edits |
| Direction | Forward. `BackwardDecompression` is optional |
| Input access | Bit reload `ReloadByte: ld a,(hl) : NEXT_HL`; literals use `ldi` / `ldir`. The EOF check peeks with `ld c,(hl) : rr c : ret z` and then advances (`NEXT_HL`). Sequential throughout |
| Window / offset limit | Not confirmed this run |
| RAM needs | Registers plus IX for the previous offset |
| Decode cost | unaplib_fast 4.86x LDIR; about 182 T-states per compressed byte (derived) |

### Exomizer 3 (Magnus Lind; Z80 `deexoopt` by Metalbrain and Antonio Villena)
| Attribute | Value |
|---|---|
| Can it stream? | Yes. The author ships streaming decoders, but not for the Z80 |
| Direction | Forward (`_fN`) or backward (`_bN`) builds |
| Input access | Literals are copied with `ldir` / `ldi` from the source. A table is built from the stream header at the start, which is still sequential |
| Window / offset limit | The C streaming decoder has `#define MAX_OFFSET 65535` and `exo_decrunch_new(unsigned short max_offset, exo_read_crunched_byte *read_byte, void *read_data)`, so the window is chosen at runtime |
| RAM needs | Z80: a `mapbase` table of about 156 bytes plus 2 more (`mapbase+156`). The C and 6502 streaming decoders use a circular buffer sized to the offset window |
| Decode cost | 11.08x to 12.20x LDIR; about 419 T-states per compressed byte (derived) |

### upkr (exoticorn; Z80 unpacker by Ped7g)
| Attribute | Value |
|---|---|
| Can it stream? | **Yes. This is the cleanest case** |
| Direction | Forward. `BACKWARDS_UNPACK` is optional |
| Input access | **Exactly one read point**: `ld a,(ix)` / `inc ix` inside `decode_bit`, once every 8 bits. Uses rANS with a bitstream (`--z80`; only the bitstream variant is supported) |
| Window / offset limit | NUMBER_BITS = 16+15 contexts, so 16-bit offsets. The packer has a `--max-offset N` option |
| RAM needs | A 320-byte probability array, 256-byte aligned; 10 bytes of stack |
| Decode cost | 159.70x LDIR (speed build), 139.32x (702-byte "minusquare" build). **About 6,369 T-states per compressed byte (derived), which is over the 4,167 budget** |

### Shrinkler (Aske Simon Christensen; Z80 port, deshrinkler_p1 in uniabis' collection)
| Attribute | Value |
|---|---|
| Can it stream? | The input pattern allows it (input via `ld a,(ix)`), but it is too slow |
| Direction | Forward |
| Input access | `ld a,(ix)` (line 115 of `deshrinkler_p1.asm`). I did not confirm the `inc ix` line |
| Window / offset limit | Not confirmed this run |
| RAM needs | **2.5 KB** of probabilities ("256-byte aligned free buffer (size 2.5 Kilobytes)") |
| Decode cost | About 432x LDIR; about 17,020 T-states per compressed byte (derived) |

### LZ4
| Attribute | Value |
|---|---|
| Can it stream? | Very likely (it is a byte-aligned format); I did not read the Z80 source this run |
| Direction | Forward |
| Window / offset limit | "The maximum `offset` value is 65535" (LZ4 block format) |
| Decode cost | unlz4_spke_fast 1.60x LDIR; about 50 T-states per compressed byte (derived) |

### ZX7, Pletter, MegaLZ
| Attribute | Value |
|---|---|
| Can it stream? | Source not read this run (the ZX7 repo path returned 404) |
| Decode cost | ZX7 dzx7_lom_v1p1 3.65x; Pletter 4.29x to 4.77x; MegaLZ unmegalz_fast 3.29x LDIR |
| Compression | ZX7 64.96%, Pletter 63.89%, MegaLZ 63.93% (all worse than ZX0's 61.82%) |

---

## Findings

1. **ZX0's standard Z80 decoder reads input only through HL, moving forwards, in three places (literal `ldir`, offset low byte, bit reload).** There is no random access. Source: https://raw.githubusercontent.com/einar-saukas/ZX0/main/z80/dzx0_standard.asm. Publisher: Einar Saukas (GitHub). Date: n/d. Confidence: high. Class: feasibility.
2. **ZX0's maximum offset is 32,640 (2,176 in quick mode).** Source: https://raw.githubusercontent.com/einar-saukas/ZX0/main/src/zx0.c. Publisher: Einar Saukas. Date: n/d. Confidence: high. Class: format.
3. **The ZX0 README lists a 6502 "(stream)" decompressor by xxl,** so a streaming adaptation of the format already exists, though not on the Z80. Source: https://github.com/einar-saukas/ZX0 (README). Publisher: Einar Saukas. Date: n/d. Confidence: high. Class: feasibility.
4. **LZSA reads input sequentially through HL (`NEXT_HL`).** The LZSA2 nibble reader reads the same byte twice before advancing. Sources: https://raw.githubusercontent.com/emmanuel-marty/lzsa/master/asm/z80/unlzsa2_small.asm and unlzsa1_small.asm. Publisher: Emmanuel Marty, introspec. Date: n/d. Confidence: high. Class: feasibility.
5. **LZSA supports up to 64 KB of back-references, and its stream format uses frames of at most 64 KB uncompressed, each with a 3-byte length header.** Sources: https://github.com/emmanuel-marty/lzsa (README.md, StreamFormat.md, BlockFormat_LZSA2.md). Publisher: Emmanuel Marty. Date: n/d. Confidence: high. Class: format.
6. **upkr's Z80 unpacker has a single input read point (`ld a,(ix)` / `inc ix`), a 320-byte probability array, and 10 bytes of stack.** Source: https://raw.githubusercontent.com/exoticorn/upkr/HEAD/z80_unpacker/unpack.asm. Publisher: exoticorn / Ped7g. Date: n/d. Confidence: high. Class: feasibility.
7. **The upkr author claims its Z80 unpacker is "about twice as fast and smaller than the Shrinkler unpacker" with compression "on par with Shrinkler".** Source: https://github.com/exoticorn/upkr (README). Publisher: exoticorn. Date: n/d. Confidence: high (that the claim is made); the benchmark supports roughly 2.3 to 2.7x. Class: performance.
8. **Exomizer ships a C forward streaming decoder with a `read_crunched_byte` callback and a runtime `max_offset` (at most 65535).** Source: https://bitbucket.org/magli143/exomizer/raw/master/rawdecrs/exodecrunch.h. Publisher: Magnus Lind. Date: n/d. Confidence: high. Class: format, feasibility.
9. **Exomizer also ships 6502 streaming forward decoders.** They use a circular buffer and a get_byte-style interface, and require data crunched with `-c` (no literal sequences). Source: https://generic.aminet.net/misc/emu/exo20b4_aos4.readme (Exomizer 2.0b4 readme). Publisher: Magnus Lind / Aminet. Date: about 2007 (version 2.0 beta 4; not confirmed). Confidence: high. Class: feasibility.
10. **The Z80 Exomizer 3 decoders come in forward and backward builds at speed levels 0 to 4, and need a `mapbase` table of about 156 bytes.** Sources: https://bitbucket.org/magli143/exomizer/raw/master/rawdecrs/z80/readme.txt and deexoopt_f0.asm. Publisher: Metalbrain, Antonio Villena. Date: n/d. Confidence: high (direction); medium (exact table size). Class: format.
11. **The uniabis benchmark gives measured decode clocks for each decoder on a 262,144-byte corpus.** Results: upkr speed build 962,952,195 clocks (159.70x LDIR); Shrinkler 2,606,417,804 (432.28x); Exomizer 3.1 66,823,568 (11.08x); aPLib fast 29,350,495 (4.86x); ZX0 standard 4.53x; LZSA2 fast 2.56x; LZ4 1.60x. Source: https://github.com/uniabis/z80depacker (readme.md). Publisher: uniabis. Date: n/d (lists OaPACK 2026.04.09, so updated in 2026). Confidence: high (measurement); medium (that it transfers to an 8 MHz CP/M Z80, because these are MSX timings). Class: performance.
12. **Derived from finding 11: upkr needs about 6,370 T-states and Shrinkler about 17,000 T-states per compressed byte, against about 4,167 available at 8 MHz and 1920 B/s.** Both would limit the total time; the LZ decoders need 50 to 420 T-states. Source: calculation from https://github.com/uniabis/z80depacker. Date: n/a. Confidence: medium. Class: performance.
13. **Shrinkler on the Z80 needs a 2.5 KB, 256-byte-aligned probability buffer and reads input through `(ix)`.** Source: https://raw.githubusercontent.com/uniabis/z80depacker/master/deshrinkler_p1.asm. Publisher: uniabis collection. Date: n/d. Confidence: high (RAM); medium (input pattern). Class: format.
14. **LZ4's maximum offset is 65535.** Source: https://raw.githubusercontent.com/lz4/lz4/dev/doc/lz4_Block_format.md. Publisher: LZ4 project. Date: n/d. Confidence: high. Class: format.
15. **Ranking by compressed size on the uniabis corpus:** upkr 57.68%, Shrinkler 58.42%, RIP 58.95%, Exomizer 3.1 60.88%, aPLib 61.58%, ZX0 61.82%, LZSA2 63.86%, LZ4 72.95%. Source: https://github.com/uniabis/z80depacker. Publisher: uniabis. Date: n/d. Confidence: high. Class: performance.
16. **upkr's Z80 readme says packed and unpacked data may need more than 7 bytes of overlap margin in the worst case.** This matters for in-place decoding, not for a stream. Source: https://raw.githubusercontent.com/exoticorn/upkr/HEAD/z80_unpacker/readme.txt. Publisher: Ped7g. Date: n/d. Confidence: high. Class: format.

## Leads worth chasing
- **uniabis/z80depacker per-file rows.** The per-file clocks show how cost per compressed byte varies with compressibility; Shrinkler ranges from 204x to 573x LDIR by file. Re-run the benchmark on a sample of CP/M .COM and text files with standard (non-MSX) Z80 timings to firm up the upkr verdict.
- **upkr "minusquare" / "difave" builds (702 and 720 bytes, 139x to 142x LDIR).** Even at 139x that is about 5,560 T-states per compressed byte under MSX timings, still over budget. Check whether standard Z80 timings, or a 10+ MHz clock, close the gap.
- **xxl's ZX0 stream decompressor for the 6502** (https://xxl.atari.pl/zx0-decompressor/) is a template for how a streaming ZX0 handles literals.
- **Exomizer `exostreamdecr1.s` / `exostreamdecr2.s`** (bitbucket exodecrs/) are reference streaming decoders with a circular window.
- **"lz4stream for Z80"** (http://mydocuments.g2.xrea.com/html/p6/randd.html, linked from uniabis) is a possible existing Z80 streaming LZ4 decoder.
- **Design idea (my reasoning, not from a source).** With 512 KB of RAM, the simplest way to stream is to receive the whole compressed file into a large linear buffer, using an interrupt-driven receiver. The decoder then works on it unchanged, except that before each read it checks that the receive pointer is ahead of HL by at least one byte, or by the literal length when copying literals with `ldir`. This avoids rewriting get-byte points and keeps the `ldir` literal copies.
- **Banked output (my reasoning, unverified).** A window of at most 32,640 bytes (ZX0) or a capped Exomizer/upkr `max_offset` fits in two 16 KB pages. Mapping 3 or 4 consecutive pages into a linear region and sliding the mapping at page boundaries keeps match `ldir` copies contiguous.

## Looked for, could not find
- Any source giving the T-state cost of polled-UART or interrupt-driven ring-buffer receive at 19200 baud on a Z80 (question 5). Unverified belief: a polled status-and-data read is a few dozen T-states per byte, and an interrupt handler with a ring buffer is roughly 100 to 200 T-states per byte. Either is small next to the 4,167 available.
- Documented Z80 decompress-while-loading examples (tape, disk or XMODEM). The search found only ZX0-on-Spectrum forum hints that data is normally decompressed after loading, and zxctl's backward-compressed blocks.
- Sources describing how Z80 LZ decoders handle matches that cross 16 KB bank boundaries.
- The ZX7 Z80 source (the repo path returned 404), plus Pletter and MegaLZ decoder sources, so their input access patterns are unverified.
- The aPLib/apultra maximum offset and the Shrinkler maximum offset, which were not confirmed from sources.
