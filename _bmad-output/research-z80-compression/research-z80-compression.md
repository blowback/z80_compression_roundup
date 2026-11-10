---
title: 'technical research: Z80 compression for serial transfer'
type: 'technical'
topic: 'z80 compression'
decision: 'Which compression scheme (or none) minimizes total host-to-Z80 transfer time over 19200 baud serial with streaming decode on an 8 MHz Z80'
source: 'native run'
status: complete
preset: 'standard'
validation: 'normal'
created: '2026-11-08'
updated: '2026-11-08'
claims_verified: 2
claims_unverified: 6
---

# Technical research: Z80 compression for serial transfer

**Decision this research serves:** which compression scheme (or none) gives the shortest total transfer time from a modern host to a Z80 CP/M target. The link is 19200 baud 8N1 (about 1,920 B/s). The 8 MHz Z80 decodes front-to-back as bytes arrive. The decoder is already resident on the target, and up to 512 KB of RAM is available in 16 KB banks.

## Executive summary

**Benchmark Exomizer 3.1, RIP, aPLib (packed with OaPACK), ZX0 v2, LZSA2 and LZ4, with "no compression" as the baseline. Keep upkr and Shrinkler as reference points only.** On published data, the decoders that keep up with the line finish within about 5% of each other. The ranking among them is decided by compressed size, so the harness's main job is to measure ratios on your own files.

What drives this:

1. **At 19200 baud the line, not the Z80, sets the pace for every LZ-family format.** An 8 MHz Z80 has about 4,170 T-states per received byte. Measured LZ decoders cost 50–450 T-states per *compressed* byte [1], so they spend most of their time waiting for data. Total time is about compressed size ÷ 1,920 B/s.
2. **The entropy coders (upkr, Shrinkler) compress best but cannot keep up.** On a 256 KB ROM benchmark, upkr costs about 5,560 T-states per compressed byte and Shrinkler about 17,000 [1]. Both are above the budget, so the Z80 becomes the limit. Their cost is roughly fixed per *output* byte, so data that compresses better makes them worse per received byte.
3. **The best LZ ratios are close together.** On that benchmark, compressed sizes are RIP 58.95%, Exomizer 3.1 60.88%, aPLib/OaPACK 61.58% and ZX0 61.82% [1]. A 2017 benchmark on a different corpus that includes text also put Exomizer ahead of aPLib [2]. That benchmark predates ZX0, RIP, LZSA and upkr.

**Biggest caveat:** no published benchmark uses CP/M executables or bitmap data like yours. The main source also compressed sixteen 16 KB blocks separately [1], so it never tests windows larger than 16 KB. Your 256 KB `.vbm` file could reorder the field: ZX0's maximum offset of about 32,640 bytes (derived from the format description [3]) may lose to formats that can reach 64 KB back [5][14].

## Candidate facts

The candidate's own repo is the source unless another is marked. "Decodes from the front" means the Z80 decoder reads the compressed bytes in order, start to end, which streaming needs.

| Candidate | Compressor (version, date) | Licence | Z80 decoders | Decodes from the front | Max offset |
|---|---|---|---|---|---|
| ZX0 v2 | einar-saukas/ZX0, no tags, last commit 2021-12-17; uniabis lists v2.2 [3][1] | BSD-3; decoders free with credit [3] | standard 68 B, turbo 126 B, fast 187 B, mega 673 B [3][1] | yes; backward builds optional [3] | about 32,640 (derived from the format: 255 × 128) [3] |
| salvador (ZX0 format) | emmanuel-marty/salvador 1.4.2, 2023-03-23 [4] | Zlib [4] | bundled Z80 decoder is v1 format only; use `-classic` [4] | yes | 31.5 KB [4] |
| LZSA1 / LZSA2 | emmanuel-marty/lzsa 1.4.1, 2023-02-27 [5] | Zlib [5] | unlzsa1 fast/small, unlzsa2 fast/small [1][5] | yes [16] | 64 KB [5] |
| aPLib | apultra 1.4.8, 2022-10-11 [6]; OaPACK 2026.04.09, the optimal packer [1][7] | apultra Zlib [6]; aPLib not checked | unaplib_fast 235 B, unaplib_small 139 B [1][20] | yes [20] | not found |
| Exomizer 3.1 | magli143/exomizer 3.1.2, 2023-11-25 [10] | LGPL per [1]; not checked at the source | deexoopt_x3_p39 and others by uniabis [1] | yes; forward and backward builds [10] | not found for Z80; the C streaming decoder accepts up to 65,535 [11] |
| RIP | eugene77/rip v2023.03.30 [12] | custom licence for the compressor; Z80 decoders "published without license" [12] | derip_fast 231 B, derip_small 228 B [1] | not checked | `--max-offset` option; default not found [12] |
| upkr | exoticorn/upkr v0.2.2, 2023-08-25 [8] | Unlicense [8] | z80_unpacker (Ped7g) plus uniabis variants [9][1] | yes; one input-read point [9] | `--max-offset N` option [8]; default not stated in the README (unverified: unlimited) |
| Shrinkler | askeksa/Shrinkler v4.7, 2022-02-22 [13] | not stated on GitHub [13] | deshrinkler_np (uniabis) for `-b` streams [1] | yes, via `(ix)`; needs a 2.5 KB probability buffer (one source) | not found |
| LZ4 | lz4ultra / smallz4 (optimal LZ4 packers), dormant since 2020 | various | unlz4_spke / fast / small [1] | yes | 65,535 [14] |

**Cut from the benchmark:**

- **ZX7:** its author says it is superseded by ZX0 [3].
- **ZX1, ZX2, ZX5:** worse ratio or no better than ZX0 on this corpus. ZX5 is marked experimental and has been reported to need extreme compression times [1][18].
- **Pletter, MegaLZ, Bitbuster, LZEe:** worse than ZX0 on ratio at similar or lower decode speed [1].
- **hrust:** 61.88%, no better than ZX0 and slower to decode [1].
- **LZSA1:** keep only if you want a fast tier. LZSA2 beats it on ratio, and decode speed is irrelevant here [1].

## Measured performance

The table below is from uniabis/z80depacker [1]. The corpus is the DEOCM-PLD-CV BIOS: 16 MSX ROM blocks of 16 KB each, 262,144 bytes in total, each block compressed separately. The decoders were timed memory-to-memory using MSX timings. On those timings LDIR costs about 23 T-states per byte, which suggests wait states are included; that is an inference.

Times assume an 8 MHz Z80 and a 1,920 B/s line, with receiving and decoding fully overlapped. The time columns are this report's arithmetic from [1], not published numbers.

| Format | Packed size [1] | T-states per output byte (fastest decoder) [1] | T-states per compressed byte | Line time | Decode time | Limited by |
|---|---:|---:|---:|---:|---:|---|
| none | 100% | 0 | 0 | 136.5 s | 0 | line |
| upkr `--z80` | 57.68% | about 3,200 | about 5,560 | 78.7 s | about 105 s | **Z80** |
| Shrinkler `-b` | 58.10% | about 9,900 | about 17,000 | 79.3 s | about 325 s | **Z80** |
| RIP | 58.95% | about 504 | about 855 | 80.5 s | 16.5 s | line |
| Exomizer 3.1 | 60.88% | about 255 | about 420 | 83.1 s | about 8 s | line |
| aPLib (OaPACK) | 61.58% | about 112 | about 182 | 84.1 s | 3.7 s | line |
| ZX0 v2 (mega) | 61.82% | 73.9 | 119.5 | 84.4 s | 2.4 s | line |
| LZSA2 | 63.86% | 59.0 | 92.4 | 87.2 s | 1.9 s | line |
| LZ4 | 72.95% | 36.8 | 50.5 | 99.6 s | 1.2 s | line |

I spot-checked the ZX0, RIP, Exomizer and aPLib rows against the uniabis readme directly. For example, ZX0 mega decodes the whole corpus in 19,360,287 T-states [1].

**The 2017 benchmark [2]:** introspec on HYPE, using a 1.23 MB corpus that includes Calgary and Canterbury text. Results:

| Format | Packed size | T-states per output byte |
|---|---:|---:|
| Exomizer | 48.31% | 248–287 |
| aPLib | 49.18% | 100–165 |
| ZX7 | 52.99% | 69–107 |
| LZ4 | 58.55% | 33 |

The order of the formats both benchmarks cover matches [1], though [2] has no ZX0, RIP, LZSA or upkr.

**Authors' figures against measurements:**

- upkr's README says its Z80 decoder is about twice as fast as Shrinkler's [8]; [1] measures it 2.3–3.1× faster.
- The LZSA README puts LZSA2 at about 75% of LZ4's decode speed [5]; [1] measures about 62%.

**Host compression speed:** no measured timings were found.

- ZX0's optimal mode "can take a few seconds", and `-q` is near-instant [3].
- salvador compresses "several orders of magnitude faster" than zx0 with output within 0.02% [4].
- RIP's compressor needs up to 4N² bytes of RAM and is "unlikely to swallow files larger than 100 kB" [12].
- ZX5 ran for 84 hours on a 33 KB file before running out of memory [18].

The harness has to measure all of these.

## Streaming and banking

**Input access.** The decoder sources for ZX0 [15], LZSA2 [16], aPLib [20] and upkr [9] all read the compressed data in order, without jumping around. upkr has exactly one input-read point. ZX0 reads input in three places: the literal `ldir`, the low offset byte, and the bit-buffer reload. LZSA2's nibble reader peeks a byte before advancing past it [16].

**Existing streaming decoders:**

- None found for the Z80 on these formats.
- A streaming LZSA2 decoder exists for x86 [17].
- Exomizer ships a C streaming decoder that takes a `read_byte` callback and a runtime window of up to 65,535 [11].
- xxl wrote a 6502 streaming ZX0 decoder [19].

**The simplest streaming design** is this report's reasoning; no source describes it. With 512 KB available:

1. Receive the whole compressed file into one linear buffer under interrupts.
2. Run the stock decoder on that buffer.
3. Before each input read, wait until the receive pointer is ahead of the read pointer. A literal run copied with `ldir` needs its full length to have arrived first.

This keeps the published decoders almost unchanged.

**Banking.** No source covers match copies that cross 16 KB bank boundaries. A 32 KB window (ZX0) spans at most three 16 KB banks. Mapping consecutive banks into the address space and sliding that mapping at bank boundaries would keep the decoder's copies contiguous. This is unverified reasoning.

**UART receive cost.** No source gives a T-state cost for receiving a byte at 19200 baud. A rough estimate, unverified, is a few dozen T-states polled or 100–200 with an interrupt-driven ring buffer. Even at 200, every LZ candidate stays far inside the ~4,170 T-state budget. upkr would get further out of reach.

## Cross-dimension insights

- **This link makes the choice depend almost only on ratio.** Every LZ decoder is idle most of the time, so the ranking criteria you set (ratio, then decode speed, then host speed) collapse to ratio. Decode speed only adds a tail after the last byte arrives, and the slowest LZ candidate (RIP) costs under 1 ms per KB of output.
- **The best compressors on paper are wrong for this link.** upkr has the best ratio measured [1], but its fixed cost per output byte puts it above the line budget. That is the reason it sits outside the main candidates. The gap widens on more compressible data: per received byte, upkr's cost grows as the ratio improves.
- **Window size matters more for your files than the benchmarks show.** [1] compressed 16 KB blocks separately. For your 256 KB file, a format that can reach further back (LZSA2 and LZ4 at 64 KB, upkr configurable, Exomizer up to 65,535 in its C decoder [11]) may gain on ZX0's roughly 32,640. Your 16 KB banks also limit how far back a decoder can copy cheaply. The two effects pull in opposite directions, and only your own data will settle it.

## Recommendations

These feed the `bmad-build` benchmark harness.

1. **Candidates to benchmark:**
   - None (the baseline).
   - Exomizer 3.1.2 `raw -P39`.
   - RIP v2023.03.30.
   - aPLib via OaPACK.
   - ZX0 v2 via zx0 and, for host speed, salvador.
   - LZSA2 1.4.1.
   - LZ4 via lz4ultra or smallz4.
   - upkr 0.2.2 `--z80` and Shrinkler 4.7, as decoder-bound reference points.

   Confidence: medium. The pick rests on one independent benchmark [1] whose format order matches an older one [2].
2. **Simulate the line, not just memory-to-memory decoding.** In the emulator, make compressed byte *i* available at T-state *i* × 4,167 plus a fixed receive cost. Stall the decoder whenever it reads ahead of arrival. The total time is then measured, including stalls and the tail. This also gives the break-even point where no compression wins on small files, once your framing overhead is included.
3. **Use the wait-for-input check rather than rewriting the decoders** (see Streaming and banking). It is the least work for using the published decoders unchanged. This is unverified reasoning, so the harness should confirm it.
4. **Report results per file and per file type** (`.COM` against `.vbm`/`.vbp`). The best format may differ between them. If so, the transfer tool can pick a format per file, since the host compresses every candidate cheaply except RIP and ZX5.
5. **Check RIP's licence before you adopt it.** Its compressor has a custom licence and a file-size ceiling of about 100 KB [12], and its Z80 decoders are published without any licence. If it wins, confirm you can use it and that it handles your largest file.

## Open questions

| Question | How to answer it |
|---|---|
| Ratios on CP/M `.COM` and bitmap data | The harness, on `test_files/` |
| Maximum offsets for Exomizer (Z80 decoder), aPLib and RIP | Read their format docs, or measure with crafted inputs |
| UART receive cost on your board | Count the T-states in your receive routine |
| A faster upkr Z80 decoder under 4,170 T-states per compressed byte | None found; a Deepen run, or accept that upkr is out |
| Host compression times | The harness |
| introspec's 2021 ZX0 comparison | cpcwiki.eu returned 403; try the Wayback Machine or a browser |

## Source appendix

| [n] | Supports | Publisher | Pub date | Accessed | Confidence |
|---|---|---|---|---|---|
| [1] | Packed sizes and Z80 decode T-states for all candidates; 16×16 KB MSX ROM corpus | [uniabis/z80depacker readme](https://github.com/uniabis/z80depacker) | 2026-09-30 | 2026-11-08 | high (spot-checked) |
| [2] | 2017 Spectrum compressor comparison on a mixed corpus | [introspec, HYPE](https://hype.retroscene.org/blog/dev/740.html) | 2017-09-08 | 2026-11-08 | medium (old, pre-ZX0) |
| [3] | ZX0 format, decoders, max offset (about 32,640, derived), licence, speed claims; ZX7 superseded | [Einar Saukas, ZX0 repo](https://github.com/einar-saukas/ZX0) | 2021-12-17 | 2026-11-08 | high |
| [4] | salvador 1.4.2, Zlib, output within 0.02% of zx0, 31.5 KB window | [Emmanuel Marty, salvador](https://github.com/emmanuel-marty/salvador) | 2023-03-23 | 2026-11-08 | high |
| [5] | LZSA 1.4.1, Zlib, 64 KB window, Calgary ratios, LZSA2 speed claim | [Emmanuel Marty, lzsa](https://github.com/emmanuel-marty/lzsa) | 2023-02-27 | 2026-11-08 | high |
| [6] | apultra 1.4.8, Zlib | [Emmanuel Marty, apultra](https://github.com/emmanuel-marty/apultra) | 2022-10-11 | 2026-11-08 | high |
| [7] | OaPACK optimal aPLib packer, active | [eugene77, OaPACK](https://gitlab.com/eugene77/oapack) | 2026-04-09 | 2026-11-08 | medium |
| [8] | upkr v0.2.2, Unlicense, `--max-offset` option, speed vs Shrinkler | [exoticorn, upkr](https://github.com/exoticorn/upkr) | 2023-08-25 | 2026-11-08 | high |
| [9] | upkr Z80 decoder reads input at a single point | [upkr z80_unpacker/unpack.asm](https://raw.githubusercontent.com/exoticorn/upkr/HEAD/z80_unpacker/unpack.asm) | n.d. | 2026-11-08 | high |
| [10] | Exomizer 3.1.2 latest tag; Z80 forward and backward decoders | [Magnus Lind, exomizer](https://bitbucket.org/magli143/exomizer) | 2023-11-25 | 2026-11-08 | high |
| [11] | Exomizer C streaming decoder, `read_byte` callback, max offset 65,535 | [exodecrunch.h](https://bitbucket.org/magli143/exomizer/raw/master/rawdecrs/exodecrunch.h) | n.d. | 2026-11-08 | high |
| [12] | RIP v2023.03.30, custom licence, 4N² RAM, ~100 KB file ceiling | [eugene77, RIP](https://gitlab.com/eugene77/rip) | 2023-03-30 | 2026-11-08 | medium |
| [13] | Shrinkler v4.7 | [askeksa, Shrinkler](https://github.com/askeksa/Shrinkler) | 2022-02-22 | 2026-11-08 | medium |
| [14] | LZ4 maximum offset 65,535 | [LZ4 block format](https://raw.githubusercontent.com/lz4/lz4/dev/doc/lz4_Block_format.md) | n.d. | 2026-11-08 | high |
| [15] | ZX0 standard decoder input access pattern | [dzx0_standard.asm](https://raw.githubusercontent.com/einar-saukas/ZX0/main/z80/dzx0_standard.asm) | n.d. | 2026-11-08 | high |
| [16] | LZSA2 Z80 decoder reads in order; the nibble reader peeks before advancing | [unlzsa2_small.asm](https://raw.githubusercontent.com/emmanuel-marty/lzsa/master/asm/z80/unlzsa2_small.asm) | n.d. | 2026-11-08 | high |
| [17] | Streaming LZSA2 decoder (x86) | [C. Masloch, inicomp lzsa2.asm](https://hg.ulukai.org/ecm/inicomp/file/c1a1f9bd4382/lzsa2.asm) | n.d. | 2026-11-08 | medium |
| [18] | ZX0/ZX5 compression-time reports | [spectrumcomputing forum](https://www.spectrumcomputing.co.uk/forums/viewtopic.php?p=81584) | 2021 | 2026-11-08 | low (forum) |
| [19] | 6502 streaming ZX0 decoder | [xxl, zx0-decompressor](https://xxl.atari.pl/zx0-decompressor/) | n.d. | 2026-11-08 | medium |
| [20] | aPLib Z80 decoder reads in order | [unaplib_small.asm](https://raw.githubusercontent.com/emmanuel-marty/apultra/master/asm/Z80/unaplib_small.asm) | n.d. | 2026-11-08 | high |

## Staleness map

Computed with `recon_kit.py staleness`. The windows come from the technical research pack: versions 1 month, ecosystem 6 months, performance 24 months.

| Claim | Class | Dated | Re-check by |
|---|---|---|---|
| uniabis benchmark sizes and cycles [1] | performance | 2026-09-30 | 2028-09-30 |
| OaPACK still active [7] | ecosystem | 2026-04-09 | 2026-10-09 (already due) |
| ZX0, LZSA, Exomizer, upkr latest versions [3][5][10][8] | version | release dates 2021–2023 | 2026-12-08 (one month from this run, when they were confirmed latest) |
| introspec 2017 ordering [2] | performance | 2017-09-08 | stale; kept only as a cross-check |

The script measures from publication date, so it marks every version claim stale. Those releases are old, but this run confirmed each was still the latest on 2026-11-08. The real re-check date is therefore one month after access: **2026-12-08, the earliest**. The harness pins exact versions, which makes this a check that nothing newer has been released, not a correctness risk. A choice made from this report should be re-checked within two quarters (by 2027-05).
