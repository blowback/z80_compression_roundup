"""Candidate compressors for the benchmark.

Each candidate names the build tool it comes from (a key in tools.json), the
command templates to compress and to decompress, and the Z80 decoder cost in
T-states per output byte.

Template tokens:
  {in}        input file
  {out}       output file
  {bin:NAME}  binary NAME in the tools bin directory

T-states per output byte come from the "Measured performance" table in
_bmad-output/research-z80-compression/research-z80-compression.md (uniabis
z80depacker, fastest decoder per format). salvador writes the ZX0 v2 format,
so it uses the ZX0 figure; apultra and OaPACK both write aPLib, so they use
the aPLib figure.
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Variant:
    """Command templates used for inputs up to max_bytes (None = no limit)."""
    compress: List[str]
    decompress: Optional[List[str]]
    max_bytes: Optional[int] = None
    note: str = ""


@dataclass
class Candidate:
    name: str
    tool: Optional[str]          # build tool in tools.json; None = built in
    tstates_per_byte: float
    variants: List[Variant] = field(default_factory=list)
    description: str = ""

    @property
    def is_baseline(self):
        return not self.variants

    def variant_for(self, size):
        for v in self.variants:
            if v.max_bytes is None or size <= v.max_bytes:
                return v
        return None


CANDIDATES = [
    Candidate(
        name="none", tool=None, tstates_per_byte=0,
        description="no compression (baseline)"),
    Candidate(
        name="zx0", tool="zx0", tstates_per_byte=73.9,
        description="ZX0 v2, optimal compressor zx0",
        variants=[Variant(
            ["{bin:zx0}", "-f", "{in}", "{out}"],
            ["{bin:dzx0}", "-f", "{in}", "{out}"])]),
    Candidate(
        name="salvador", tool="salvador", tstates_per_byte=73.9,
        description="ZX0 v2 format, fast compressor salvador",
        variants=[Variant(
            ["{bin:salvador}", "{in}", "{out}"],
            ["{bin:salvador}", "-d", "{in}", "{out}"])]),
    Candidate(
        name="lzsa2", tool="lzsa", tstates_per_byte=59.0,
        description="LZSA2 raw block (stream format above 64 KB)",
        variants=[
            # Raw blocks are what the Z80 decoders read, but lzsa only
            # accepts them for inputs up to 64 KB.
            Variant(["{bin:lzsa}", "-f", "2", "-r", "{in}", "{out}"],
                    ["{bin:lzsa}", "-d", "-f", "2", "-r", "{in}", "{out}"],
                    max_bytes=65536, note="raw block"),
            Variant(["{bin:lzsa}", "-f", "2", "{in}", "{out}"],
                    ["{bin:lzsa}", "-d", "-f", "2", "{in}", "{out}"],
                    note="stream format (input over 64 KB)"),
        ]),
    Candidate(
        name="aplib-oapack", tool="oapack", tstates_per_byte=112,
        description="aPLib, optimal packer OaPACK, checked with apultra -d",
        variants=[Variant(
            ["{bin:oapack}", "{in}", "{out}"],
            ["{bin:apultra}", "-d", "{in}", "{out}"])]),
    Candidate(
        name="aplib-apultra", tool="apultra", tstates_per_byte=112,
        description="aPLib, packer apultra",
        variants=[Variant(
            ["{bin:apultra}", "{in}", "{out}"],
            ["{bin:apultra}", "-d", "{in}", "{out}"])]),
    Candidate(
        name="exomizer", tool="exomizer", tstates_per_byte=255,
        description="Exomizer 3.1.2 raw -P39",
        variants=[Variant(
            ["{bin:exomizer}", "raw", "-q", "-P39", "{in}", "-o", "{out}"],
            ["{bin:exomizer}", "raw", "-q", "-d", "-P39", "{in}",
             "-o", "{out}"])]),
    Candidate(
        name="rip", tool="rip", tstates_per_byte=504,
        description="RIP 2023.03.30",
        variants=[Variant(
            ["{bin:rip}", "{in}", "{out}"],
            ["{bin:rip}", "-d", "{in}", "{out}"])]),
    Candidate(
        name="lz4", tool="lz4ultra", tstates_per_byte=36.8,
        description="LZ4 raw block, optimal packer lz4ultra",
        variants=[Variant(
            ["{bin:lz4ultra}", "-r", "{in}", "{out}"],
            ["{bin:lz4ultra}", "-d", "-r", "{in}", "{out}"])]),
    Candidate(
        name="upkr", tool="upkr", tstates_per_byte=3200,
        description="upkr 0.2.2 --z80 (reference: decoder-bound)",
        variants=[Variant(
            ["{bin:upkr}", "--z80", "{in}", "{out}"],
            ["{bin:upkr}", "-d", "--z80", "{in}", "{out}"])]),
    Candidate(
        name="shrinkler", tool="shrinkler", tstates_per_byte=9900,
        description="Shrinkler 4.7 data mode -d -b (reference: "
                    "decoder-bound; no host decompressor)",
        variants=[Variant(
            ["{bin:Shrinkler}", "-d", "-b", "-p", "{in}", "{out}"],
            None)]),
]


def by_name(names=None):
    if not names:
        return list(CANDIDATES)
    table = {c.name: c for c in CANDIDATES}
    unknown = [n for n in names if n not in table]
    if unknown:
        raise KeyError(", ".join(unknown))
    return [table[n] for n in names]
