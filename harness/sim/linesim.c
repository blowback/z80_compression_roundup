/*
 * linesim: run a Z80 decoder against a simulated 8N1 serial line.
 *
 * Built against the superzazu/z80 core (MIT) by harness/build_tools.py.
 * Driven by harness/linesim.py; see harness/README.md ("Line simulation").
 *
 * Model
 *   T_byte = cpu_hz * 10 / baud. Byte g of the line (0-based, counted over
 *   all pieces) finishes on the wire at (g + 1) * T_byte. Its receive
 *   interrupt then takes rx_cost T-states, so the byte is due at
 *   (g + 1) * T_byte + rx_cost. Interrupts that fall while the decoder is
 *   running add rx_cost to the clock ("rx"). Interrupts that fall while the
 *   decoder is waiting for a byte cost nothing extra.
 *
 *   flat mode: each piece's stream sits in a linear buffer at its input
 *   address. Reading a buffer byte that is not yet due stalls: the clock
 *   jumps to its due time ("stall"). Reads up to 256 bytes past the end of
 *   the stream are counted as "overread" and treated as padding bytes that
 *   follow the stream on the line: later pieces shift down the line by
 *   that many bytes, and line_bytes includes them. The window never
 *   reaches the --code range.
 *
 *   ring mode: the simulated interrupt writes each byte into a ring of
 *   SIZE bytes at ADDR and stores the new write pointer (a 16-bit address)
 *   at WP. The decoder keeps its read pointer at RP. When the ring is full
 *   (SIZE - 1 bytes unread) the line pauses (RTS/CTS) until the decoder
 *   frees a slot; later bytes shift by that pause ("flow_pause").
 *
 *   Memory follows the MicroBeast: 1 MB physical, ROM pages 0x00-0x1F,
 *   RAM pages 0x20-0x3F, four 16 KB windows. OUT to a port with
 *   (port & 0xF0) == 0x70 sets window (port & 3) when (port & 4) == 0, and
 *   turns paging on or off with data bit 0 when (port & 4) != 0. With paging
 *   off the logical address is the ROM address. Flat start state: paging on,
 *   windows 0-3 = RAM pages 0x20-0x23. Writes to ROM are ignored.
 *
 *   banked mode: ring mode with the banked start state: windows 0-2 on RAM
 *   pages 0x20-0x22 and window 3 on page 0x3F. Each piece's output is
 *   written from logical 0x0000 by a banked decoder that pages windows 0-2
 *   itself, so it is read back (and zeroed before the piece) at physical
 *   0x80000 (page 0x20) upwards. --wait LO:HI names the decoder's wait
 *   loop: T-states spent with PC in that range, and interrupts taken there,
 *   count as "stall" instead of decoder time or "rx".
 *
 *   --word ADDR=VALUE stores a 16-bit word before each piece (after the
 *   memory image is loaded). VALUE is a number, @len (the piece's stream
 *   length) or @last (1 for the last piece, else 0).
 *
 *   Each piece runs from the entry point with a fresh stack whose return
 *   address is a sentinel HALT. The run ends when that HALT executes. PC
 *   outside the --code range (other than the sentinel), a HALT elsewhere or
 *   more than --max-cycles T-states is an error.
 *
 * Output: the output region of each piece is written to its file, then one
 * JSON line goes to stdout.
 */

#include <errno.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "z80.h"

#define PHYS_SIZE (1u << 20)
#define RAM_FIRST_PAGE 0x20
#define OVERREAD_WINDOW 256
#define MAX_ITEMS 512
#define BANKED_OUT_PHYS ((uint32_t)RAM_FIRST_PAGE << 14)
#define BANKED_OUT_MAX ((uint32_t)(0x3F - RAM_FIRST_PAGE) << 14)

typedef struct {
  uint16_t addr;
  char *file;
} Load;

typedef struct {
  uint16_t in_addr;
  char *stream_file;
  uint16_t out_addr;
  uint32_t out_len;
  char *out_file;
  uint8_t *data;
  long len;
  long base;            /* line index of this piece's first byte */
  int last;             /* 1 for the last piece */
  /* results */
  uint64_t start, end, stall, rx, flow_pause;
  long overread;
} Piece;

typedef struct {
  char name[4];
  char *value;
} RegSet;

typedef struct {
  uint32_t addr, len;
  char *file;
} Dump;

typedef struct {
  uint16_t addr;
  char *value;
} Word;

typedef struct {
  z80 cpu;
  uint8_t *phys;
  uint8_t page[4];
  int paging;

  int ring_mode;
  int banked;
  long wait_lo, wait_hi;  /* PC range of the decoder's wait loop */
  int in_wait;
  double t_byte;
  uint64_t rx_cost;
  uint64_t max_cycles;

  uint8_t *line;
  long line_len;
  long next_isr;          /* next line byte whose interrupt is not done */

  /* flat mode: current piece */
  Piece *cur;
  long code_lo;           /* the overread window stops here */

  /* ring mode */
  uint16_t ring_addr, ring_size, wp_addr, rp_addr;
  int tx_active, paused;
  double tx_end, pause_since;

  uint64_t stall, rx, flow_pause;
} Sim;

static void die_usage(const char *msg) {
  fprintf(stderr, "linesim: %s\n", msg);
  fprintf(stderr,
      "usage: linesim --load ADDR:FILE --entry ADDR --code LO:HI\n"
      "         --sp ADDR --sentinel ADDR [--mode flat|ring|banked]\n"
      "         --piece IN:STREAM:OUT:LEN:OUTFILE ... [--reg NAME=VALUE ...]\n"
      "         [--ring ADDR:SIZE:WP:RP] [--wait LO:HI] [--word ADDR=VALUE ...]\n"
      "         [--baud N] [--cpu-hz N] [--rx-cost N] [--max-cycles N]\n"
      "         [--dump-phys ADDR:LEN:FILE]\n"
      "VALUE is a number or @in/@out/@len/@last with an optional +N/-N.\n");
  exit(2);
}

static long parse_num(const char *s) {
  char *end;
  errno = 0;
  long v = strtol(s, &end, 0);
  if (errno || *end || end == s) {
    fprintf(stderr, "linesim: bad number '%s'\n", s);
    exit(2);
  }
  return v;
}

static int split(char *s, char **parts, int max) {
  int n = 0;
  parts[n++] = s;
  for (char *p = s; *p && n < max; p++) {
    if (*p == ':') {
      *p = 0;
      parts[n++] = p + 1;
    }
  }
  return n;
}

static uint8_t *read_file(const char *path, long *len) {
  FILE *f = fopen(path, "rb");
  if (!f) {
    fprintf(stderr, "linesim: cannot open %s: %s\n", path, strerror(errno));
    exit(2);
  }
  fseek(f, 0, SEEK_END);
  long n = ftell(f);
  fseek(f, 0, SEEK_SET);
  uint8_t *buf = malloc(n > 0 ? n : 1);
  if (n > 0 && fread(buf, 1, n, f) != (size_t)n) {
    fprintf(stderr, "linesim: cannot read %s\n", path);
    exit(2);
  }
  fclose(f);
  *len = n;
  return buf;
}

/* ---- memory and ports ------------------------------------------------ */

static uint32_t phys_addr(Sim *s, uint16_t addr) {
  if (!s->paging)
    return addr;
  return ((uint32_t)(s->page[addr >> 14] & 0x3F) << 14) | (addr & 0x3FFF);
}

static uint8_t peek(Sim *s, uint16_t addr) {
  if (s->paging && (s->page[addr >> 14] & 0xC0))
    return 0xFF;                         /* not memory (e.g. video) */
  return s->phys[phys_addr(s, addr)];
}

static void poke(Sim *s, uint16_t addr, uint8_t v) {
  if (!s->paging)
    return;                              /* flat ROM */
  uint8_t pg = s->page[addr >> 14];
  if ((pg & 0xE0) != RAM_FIRST_PAGE)
    return;                              /* ROM or not memory */
  s->phys[phys_addr(s, addr)] = v;
}

static uint64_t isr_time(Sim *s, long g) {
  return (uint64_t)ceil((double)(g + 1) * s->t_byte);
}

static long p_in_end(const Piece *p) {
  return p ? (long)p->in_addr + p->len : 0;
}

static uint8_t cb_read(void *ud, uint16_t addr) {
  Sim *s = ud;
  Piece *p = s->cur;
  long win_end = (long)p_in_end(p) + OVERREAD_WINDOW;
  if (p && s->code_lo >= p->in_addr && win_end > s->code_lo)
    win_end = s->code_lo;              /* never cover the decoder code */
  if (!s->ring_mode && p && addr >= p->in_addr && addr < win_end) {
    long i = addr - p->in_addr;
    if (i >= p->len && i - p->len + 1 > p->overread)
      p->overread = i - p->len + 1;
    long g = p->base + i;
    uint64_t due = isr_time(s, g) + s->rx_cost;
    if (s->cpu.cyc < due) {
      uint64_t wait = due - s->cpu.cyc;
      s->stall += wait;
      p->stall += wait;
      s->cpu.cyc = due;
      /* interrupts up to this byte ran while the decoder waited */
      if (s->next_isr <= g)
        s->next_isr = g + 1 < s->line_len ? g + 1 : s->line_len;
    }
  }
  return peek(s, addr);
}

static void cb_write(void *ud, uint16_t addr, uint8_t v) {
  poke(ud, addr, v);
}

static uint8_t cb_in(z80 *z, uint8_t port) {
  (void)z;
  (void)port;
  return 0xFF;
}

static void cb_out(z80 *z, uint8_t port, uint8_t v) {
  Sim *s = z->userdata;
  if ((port & 0xF0) == 0x70) {
    if ((port & 0x04) == 0)
      s->page[port & 0x03] = v;
    else
      s->paging = (v & 1) != 0;
  }
}

static void reset_pages(Sim *s) {
  s->paging = 1;
  for (int i = 0; i < 4; i++)
    s->page[i] = RAM_FIRST_PAGE + i;
  if (s->banked)
    s->page[3] = 0x3F;
}

static void reset_memory(Sim *s) {
  memset(s->phys, 0, PHYS_SIZE);
  reset_pages(s);
}

/* ---- receive model ----------------------------------------------------- */

static uint16_t rd16(Sim *s, uint16_t a) {
  return peek(s, a) | (peek(s, (uint16_t)(a + 1)) << 8);
}

static void wr16(Sim *s, uint16_t a, uint16_t v) {
  poke(s, a, v & 0xFF);
  poke(s, (uint16_t)(a + 1), v >> 8);
}

static int ring_full(Sim *s) {
  uint16_t wp = rd16(s, s->wp_addr), rp = rd16(s, s->rp_addr);
  return ((uint16_t)(wp - rp) & (s->ring_size - 1)) == s->ring_size - 1;
}

static void charge_rx(Sim *s) {
  s->cpu.cyc += s->rx_cost;
  if (s->in_wait) {
    /* the decoder was only waiting: the interrupt is part of the wait */
    s->stall += s->rx_cost;
    if (s->cur)
      s->cur->stall += s->rx_cost;
    return;
  }
  s->rx += s->rx_cost;
  if (s->cur)
    s->cur->rx += s->rx_cost;
}

static void service_line(Sim *s) {
  if (!s->ring_mode) {
    while (s->next_isr < s->line_len
           && s->cpu.cyc >= isr_time(s, s->next_isr)) {
      charge_rx(s);
      s->next_isr++;
    }
    return;
  }
  for (;;) {
    if (s->tx_active) {
      if ((double)s->cpu.cyc < s->tx_end)
        return;
      /* byte complete: the interrupt stores it and moves the write pointer */
      charge_rx(s);
      uint16_t wp = rd16(s, s->wp_addr);
      poke(s, wp, s->line[s->next_isr]);
      uint16_t off = (uint16_t)(wp - s->ring_addr + 1) & (s->ring_size - 1);
      wr16(s, s->wp_addr, s->ring_addr + off);
      s->next_isr++;
      s->tx_active = 0;
      if (s->next_isr >= s->line_len)
        return;
      if (ring_full(s)) {
        s->paused = 1;
        s->pause_since = s->tx_end;
        return;
      }
      s->tx_end += s->t_byte;          /* next byte follows back to back */
      s->tx_active = 1;
      continue;
    }
    if (s->next_isr >= s->line_len || ring_full(s))
      return;
    /* paused and a slot is free again: the host resumes now */
    double now = (double)s->cpu.cyc;
    uint64_t pause = (uint64_t)llround(now - s->pause_since);
    s->flow_pause += pause;
    if (s->cur)
      s->cur->flow_pause += pause;
    s->paused = 0;
    s->tx_end = now + s->t_byte;
    s->tx_active = 1;
    return;
  }
}

/* ---- registers --------------------------------------------------------- */

static uint16_t reg_value(const char *v, Piece *p, Sim *s) {
  long base, off = 0;
  const char *rest;
  if (strncmp(v, "@in", 3) == 0) {
    base = s->ring_mode ? s->ring_addr : p->in_addr;
    rest = v + 3;
  } else if (strncmp(v, "@out", 4) == 0) {
    base = p->out_addr;
    rest = v + 4;
  } else if (strncmp(v, "@len", 4) == 0) {
    base = p->len;
    rest = v + 4;
  } else if (strncmp(v, "@last", 5) == 0) {
    base = p->last;
    rest = v + 5;
  } else {
    return (uint16_t)parse_num(v);
  }
  if (*rest)
    off = parse_num(rest);
  return (uint16_t)(base + off);
}

static void set_reg(z80 *z, const char *name, uint16_t v) {
  uint8_t hi = v >> 8, lo = v & 0xFF;
  if (!strcmp(name, "BC")) { z->b = hi; z->c = lo; }
  else if (!strcmp(name, "DE")) { z->d = hi; z->e = lo; }
  else if (!strcmp(name, "HL")) { z->h = hi; z->l = lo; }
  else if (!strcmp(name, "A")) { z->a = lo; }
  else if (!strcmp(name, "IX")) { z->ix = v; }
  else if (!strcmp(name, "IY")) { z->iy = v; }
  else if (!strcmp(name, "BC'")) { z->b_ = hi; z->c_ = lo; }
  else if (!strcmp(name, "DE'")) { z->d_ = hi; z->e_ = lo; }
  else if (!strcmp(name, "HL'")) { z->h_ = hi; z->l_ = lo; }
  else {
    fprintf(stderr, "linesim: unknown register %s\n", name);
    exit(2);
  }
}

/* ---- main -------------------------------------------------------------- */

static void print_json(Sim *s, const char *status, const char *reason,
                       Piece *pieces, int npieces) {
  long overread = 0;
  for (int i = 0; i < npieces; i++)
    overread += pieces[i].overread;
  printf("{\"status\": \"%s\", \"reason\": \"%s\", \"cycles\": %lu, "
         "\"stall\": %llu, \"rx\": %llu, \"flow_pause\": %llu, "
         "\"overread\": %ld, \"line_bytes\": %ld, \"bytes_received\": %ld, "
         "\"t_byte\": %.4f, \"pieces\": [",
         status, reason, s->cpu.cyc, (unsigned long long)s->stall,
         (unsigned long long)s->rx, (unsigned long long)s->flow_pause,
         overread, s->line_len, s->next_isr, s->t_byte);
  for (int i = 0; i < npieces; i++) {
    Piece *p = &pieces[i];
    printf("%s{\"bytes\": %ld, \"start\": %llu, \"end\": %llu, "
           "\"stall\": %llu, \"rx\": %llu, \"flow_pause\": %llu, "
           "\"overread\": %ld}",
           i ? ", " : "", p->len, (unsigned long long)p->start,
           (unsigned long long)p->end, (unsigned long long)p->stall,
           (unsigned long long)p->rx, (unsigned long long)p->flow_pause,
           p->overread);
  }
  printf("]}\n");
}

int main(int argc, char **argv) {
  static Load loads[MAX_ITEMS];
  static Piece pieces[MAX_ITEMS];
  static RegSet regs[32];
  static Dump dumps[MAX_ITEMS];
  static Word words[32];
  int nloads = 0, npieces = 0, nregs = 0, ndumps = 0, nwords = 0;
  long entry = -1, sp = -1, sentinel = -1, code_lo = -1, code_hi = -1;
  double baud = 19200, cpu_hz = 8000000;
  double max_cycles = 20e9;
  long rx_cost = 150;
  static Sim sim;
  Sim *s = &sim;

  for (int i = 1; i < argc; i++) {
    const char *a = argv[i];
    if (i + 1 >= argc)
      die_usage("missing option value");
    char *v = argv[++i];
    char *parts[6];
    if (!strcmp(a, "--load")) {
      if (split(v, parts, 2) != 2 || nloads >= MAX_ITEMS)
        die_usage("--load ADDR:FILE");
      loads[nloads].addr = (uint16_t)parse_num(parts[0]);
      loads[nloads++].file = parts[1];
    } else if (!strcmp(a, "--piece")) {
      if (split(v, parts, 5) != 5 || npieces >= MAX_ITEMS)
        die_usage("--piece IN:STREAM:OUT:LEN:OUTFILE");
      Piece *p = &pieces[npieces++];
      p->in_addr = (uint16_t)parse_num(parts[0]);
      p->stream_file = parts[1];
      p->out_addr = (uint16_t)parse_num(parts[2]);
      p->out_len = (uint32_t)parse_num(parts[3]);
      p->out_file = parts[4];
    } else if (!strcmp(a, "--reg")) {
      char *eq = strchr(v, '=');
      if (!eq || eq - v > 3 || nregs >= 32)
        die_usage("--reg NAME=VALUE");
      memcpy(regs[nregs].name, v, eq - v);
      regs[nregs].name[eq - v] = 0;
      regs[nregs++].value = eq + 1;
    } else if (!strcmp(a, "--ring")) {
      if (split(v, parts, 4) != 4)
        die_usage("--ring ADDR:SIZE:WP:RP");
      s->ring_addr = (uint16_t)parse_num(parts[0]);
      long size = parse_num(parts[1]);
      if (size < 2 || size > 32768 || (size & (size - 1)))
        die_usage("ring size must be a power of two, 2..32768");
      s->ring_size = (uint16_t)size;
      s->wp_addr = (uint16_t)parse_num(parts[2]);
      s->rp_addr = (uint16_t)parse_num(parts[3]);
    } else if (!strcmp(a, "--word")) {
      char *eq = strchr(v, '=');
      if (!eq || nwords >= 32)
        die_usage("--word ADDR=VALUE");
      *eq = 0;
      words[nwords].addr = (uint16_t)parse_num(v);
      words[nwords++].value = eq + 1;
    } else if (!strcmp(a, "--wait")) {
      if (split(v, parts, 2) != 2)
        die_usage("--wait LO:HI");
      s->wait_lo = parse_num(parts[0]);
      s->wait_hi = parse_num(parts[1]);
    } else if (!strcmp(a, "--dump-phys")) {
      if (split(v, parts, 3) != 3 || ndumps >= MAX_ITEMS)
        die_usage("--dump-phys ADDR:LEN:FILE");
      dumps[ndumps].addr = (uint32_t)parse_num(parts[0]);
      dumps[ndumps].len = (uint32_t)parse_num(parts[1]);
      dumps[ndumps++].file = parts[2];
    } else if (!strcmp(a, "--code")) {
      if (split(v, parts, 2) != 2)
        die_usage("--code LO:HI");
      code_lo = parse_num(parts[0]);
      code_hi = parse_num(parts[1]);
    } else if (!strcmp(a, "--mode")) {
      if (!strcmp(v, "ring")) s->ring_mode = 1;
      else if (!strcmp(v, "banked")) s->ring_mode = s->banked = 1;
      else if (strcmp(v, "flat")) die_usage("--mode flat|ring|banked");
    } else if (!strcmp(a, "--entry")) entry = parse_num(v);
    else if (!strcmp(a, "--sp")) sp = parse_num(v);
    else if (!strcmp(a, "--sentinel")) sentinel = parse_num(v);
    else if (!strcmp(a, "--baud")) baud = atof(v);
    else if (!strcmp(a, "--cpu-hz")) cpu_hz = atof(v);
    else if (!strcmp(a, "--rx-cost")) rx_cost = parse_num(v);
    else if (!strcmp(a, "--max-cycles")) max_cycles = atof(v);
    else die_usage("unknown option");
  }
  if (entry < 0 || sp < 0 || sentinel < 0 || code_lo < 0 || !npieces)
    die_usage("--entry, --sp, --sentinel, --code and --piece are required");
  if (s->ring_mode && !s->ring_size)
    die_usage("ring and banked modes need --ring");
  for (int i = 0; i < npieces; i++)
    if (s->banked && pieces[i].out_len > BANKED_OUT_MAX)
      die_usage("banked output is limited to RAM pages 0x20-0x3E (496 KB)");
  if (baud <= 0 || cpu_hz <= 0 || rx_cost < 0)
    die_usage("baud and cpu-hz must be > 0, rx-cost >= 0");

  s->t_byte = cpu_hz * 10.0 / baud;
  s->rx_cost = (uint64_t)rx_cost;
  s->max_cycles = (uint64_t)max_cycles;
  s->phys = malloc(PHYS_SIZE);
  s->code_lo = code_lo;

  /* the line: all pieces back to back */
  for (int i = 0; i < npieces; i++) {
    pieces[i].data = read_file(pieces[i].stream_file, &pieces[i].len);
    pieces[i].base = s->line_len;
    pieces[i].last = i == npieces - 1;
    s->line_len += pieces[i].len;
  }
  s->line = malloc(s->line_len ? s->line_len : 1);
  for (int i = 0; i < npieces; i++)
    memcpy(s->line + pieces[i].base, pieces[i].data, pieces[i].len);

  long load_len[MAX_ITEMS];
  uint8_t *load_data[MAX_ITEMS];
  for (int i = 0; i < nloads; i++)
    load_data[i] = read_file(loads[i].file, &load_len[i]);

  z80 *z = &s->cpu;
  z80_init(z);
  z->read_byte = cb_read;
  z->write_byte = cb_write;
  z->port_in = cb_in;
  z->port_out = cb_out;
  z->userdata = s;

  const char *status = "ok";
  char reason[160] = "";

  for (int pi = 0; pi < npieces; pi++) {
    Piece *p = &pieces[pi];
    if (pi == 0 || !s->ring_mode) {
      /* flat: every piece gets a fresh memory image */
      reset_memory(s);
      for (int i = 0; i < nloads; i++)
        for (long j = 0; j < load_len[i]; j++)
          poke(s, (uint16_t)(loads[i].addr + j), load_data[i][j]);
      if (!s->ring_mode)
        for (long j = 0; j < p->len; j++)
          poke(s, (uint16_t)(p->in_addr + j), p->data[j]);
      else {
        wr16(s, s->wp_addr, s->ring_addr);
        wr16(s, s->rp_addr, s->ring_addr);
        s->tx_active = 1;              /* byte 0 starts at T-state 0 */
        s->tx_end = s->t_byte;
      }
    }
    if (s->banked) {
      reset_pages(s);                  /* banked start state per piece */
      memset(s->phys + BANKED_OUT_PHYS, 0, p->out_len);
    } else {
      for (uint32_t j = 0; j < p->out_len; j++)
        poke(s, (uint16_t)(p->out_addr + j), 0);
    }
    poke(s, (uint16_t)sentinel, 0x76);  /* HALT */
    for (int w = 0; w < nwords; w++)
      wr16(s, words[w].addr, reg_value(words[w].value, p, s));

    unsigned long cyc = z->cyc;
    z80_init(z);
    z->read_byte = cb_read;
    z->write_byte = cb_write;
    z->port_in = cb_in;
    z->port_out = cb_out;
    z->userdata = s;
    z->cyc = cyc;
    for (int r = 0; r < nregs; r++)
      set_reg(z, regs[r].name, reg_value(regs[r].value, p, s));
    z->sp = (uint16_t)(sp - 2);
    wr16(s, z->sp, (uint16_t)sentinel);
    z->pc = (uint16_t)entry;

    s->cur = p;
    p->start = z->cyc;
    for (;;) {
      uint16_t pc = z->pc;
      if (pc != sentinel && (pc < code_lo || pc >= code_hi)) {
        status = "error";
        snprintf(reason, sizeof reason, "PC left the decoder at 0x%04X", pc);
        break;
      }
      s->in_wait = pc >= s->wait_lo && pc < s->wait_hi;
      unsigned long before = z->cyc;
      z80_step(z);
      if (s->in_wait) {
        s->stall += z->cyc - before;
        p->stall += z->cyc - before;
      }
      if (z->halted) {
        if (z->pc == (uint16_t)(sentinel + 1))
          break;
        status = "error";
        snprintf(reason, sizeof reason, "HALT at 0x%04X",
                 (uint16_t)(z->pc - 1));
        break;
      }
      service_line(s);
      s->in_wait = 0;
      if (z->cyc > s->max_cycles) {
        status = "error";
        snprintf(reason, sizeof reason, "cycle limit %.3g exceeded",
                 (double)s->max_cycles);
        break;
      }
    }
    p->end = z->cyc;
    if (strcmp(status, "ok"))
      break;

    /* Overread bytes are padding sent after this piece, so every later
       piece starts that many bytes further down the line. */
    if (!s->ring_mode && p->overread > 0 && pi + 1 < npieces) {
      long o = p->overread, at = p->base + p->len;
      uint8_t *nl = malloc(s->line_len + o);
      memcpy(nl, s->line, at);
      memset(nl + at, 0, o);
      memcpy(nl + at + o, s->line + at, s->line_len - at);
      free(s->line);
      s->line = nl;
      s->line_len += o;
      for (int q = pi + 1; q < npieces; q++)
        pieces[q].base += o;
    }

    FILE *f = fopen(p->out_file, "wb");
    if (!f) {
      fprintf(stderr, "linesim: cannot write %s\n", p->out_file);
      return 2;
    }
    if (s->banked)
      fwrite(s->phys + BANKED_OUT_PHYS, 1, p->out_len, f);
    else
      for (uint32_t j = 0; j < p->out_len; j++)
        fputc(peek(s, (uint16_t)(p->out_addr + j)), f);
    fclose(f);
  }
  s->cur = NULL;

  for (int i = 0; i < ndumps; i++) {
    FILE *f = fopen(dumps[i].file, "wb");
    if (!f) {
      fprintf(stderr, "linesim: cannot write %s\n", dumps[i].file);
      return 2;
    }
    for (uint32_t j = 0; j < dumps[i].len; j++)
      fputc(s->phys[(dumps[i].addr + j) & (PHYS_SIZE - 1)], f);
    fclose(f);
  }

  print_json(s, status, reason, pieces, npieces);
  return strcmp(status, "ok") ? 1 : 0;
}
