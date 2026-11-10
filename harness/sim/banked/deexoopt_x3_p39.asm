; -----------------------------------------------------------------------------
; Banked port of deexoopt_x3.asm (Exomizer 3 Z80 decoder) from
; uniabis/z80depacker, for harness/linesim --mode banked.
;
;Exomizer 3 Z80 decoder
;Copyright (C) 2008-2018 by Jaime Tejedor Gomez (Metalbrain)
;
;Optimized by Antonio Villena and Urusergi
;
;Fixed a bug, and optimized, and added support for all -P options by uniabis
;
;Compression algorithm by Magnus Lind
;
;   This depacker is free software; you can redistribute it and/or
;   modify it under the terms of the GNU Lesser General Public
;   License as published by the Free Software Foundation; either
;   version 2.1 of the License, or (at your option) any later version.
;
;   This library is distributed in the hope that it will be useful,
;   but WITHOUT ANY WARRANTY; without even the implied warranty of
;   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
;   Lesser General Public License for more details.
;
; ALTERED SOURCE. Only input reads, output writes and copies changed (helpers
; in harness/sim/banked/banked.inc). This copy is the configuration the
; harness builds, with its IF branches resolved: -P39 (PFLAG_BITS_ORDER_BE,
; PFLAG_BITS_COPY_GT_7, PFLAG_IMPL_1LITERAL, PFLAG_REUSE_OFFSET), literals=1,
; forward (back=0), OPTIMIZE_JUMP, Z80 (no HD64180). The map_ofs test still
; depends on mapbase, so it stays. decoders.py passes the same defines as
; the flat build plus RING_COUNT_MEM: IY is the decoder's table pointer, so
; the ring counter lives in memory (V_COUNT).
;
; The input pointer is HL in the main loop and DE inside the offset/length
; reading (after "ex de,hl" at gbmc). Edit sites, each marked "BANKED":
;   1.  gb4: ld a,(hl) -> RCHK_HL first; carry survives for adc a,a.
;   2.  gbi: same.
;   3.  gbm: same.
;   4.  litcop: ldi (one literal) -> LDI_LIT.
;   5.  useofs: ldir (match) -> copy_match.
;   6.  litcat: ld b,(hl) -> RCHK_HL.
;   7.  litcat: ld c,(hl) -> RCHK_HL.
;   8.  litcat: ldir (literal run) -> copy_lit.
;   9.  checkreuse: ld a,(de) -> RCHK_DE; carry survives for adc a,a.
;   10. getbits: "defb 250" (JP M,nnnn, never taken after SRL B, used to skip
;       the 2-byte ld a,(de) : inc de of xopy) -> jp lee16. Same flags.
;   11. xopy: ld a,(de) -> RCHK_DE.
;   12. gby: ld c,(hl) (after ex de,hl) -> RCHK_HL.
;   13. copy: ld a,(de) -> RCHK_DE.
; -----------------------------------------------------------------------------

        INCLUDE "banked.inc"

  DEFINE PFLAG_BITS_ORDER_BE (1<<0)
  DEFINE PFLAG_BITS_COPY_GT_7 (1<<1)
  DEFINE PFLAG_IMPL_1LITERAL (1<<2)
  DEFINE PFLAG_BITS_ALIGN_START (1<<3)
  DEFINE PFLAG_4_OFFSET_TABLES (1<<4)
  DEFINE PFLAG_REUSE_OFFSET (1<<5)

  ASSERT PFLAG_CODE == 39 && literals == 1      ; the only configuration ported

  DEFINE map_len (16*3+4)
  IF  ((low mapbase)<256+(128-16)-128) && ((low mapbase)>(128-16)-map_len*2+127)
    DEFINE map_ofs 0
  ELSE
    DEFINE map_ofs (128-16)
  ENDIF
map_iyh equ (high (mapbase-map_ofs+128))
map_disp_bit equ ((low (mapbase-map_ofs+128))-128)
map_disp_lo equ (map_disp_bit+map_len*1)
map_disp_hi equ (map_disp_bit+map_len*2)

banked_decoder:
        ld      iy, map_iyh*256+map_ofs

        cp      a       ;set ZF
        ex      af, af';'

        ld      bc, map_len * 256 + 16

        or      a       ;reset CF

gb4     RCHK_HL                 ; BANKED (1): keeps carry
        ld      a, (hl)
        inc     hl
get4    adc     a, a
get4l   jr      z, gb4
        rl      c
        jr      nc, get4

        ex      af, af';'
        ld      a, c

        exx

        ld      hl, 1
        jr      nz, .skp1
        ld      d, h
        ld      e, l
        ld      c, 16
.skp1   ld      (iy+map_disp_lo), e
        ld      (iy+map_disp_hi), d

        rrca
        inc     a

        ld      (iy+map_disp_bit), a

        jr      nc, get5
        sub     128-8+1
setbit  add     hl, hl
get5    dec     a
        jr      nz, setbit

        add     hl, de
        ex      de, hl

        inc     iyl
        dec     c
        ex      af, af';'
        exx

        ld      c, 16
        or      a       ;reset CF
        djnz    get4

        ld      c, b    ;BC=0

        jr      litcop

gbi     RCHK_HL                 ; BANKED (2): keeps carry
        ld      a, (hl)
        inc     hl
        adc     a, a

        jp      gbic

gbm     RCHK_HL                 ; BANKED (3): keeps carry
        ld      a, (hl)
        inc     hl
        adc     a, a
        jr      nc, gbmc

litcop:
        LDI_LIT                 ; BANKED (4): was ldi
mloop:
        add     a, a
mloopl  jr      z, gbm
        jr      c, litcop

gbmc:
        push    bc

        ld      c, (map_ofs-1) & 255
getind  add     a, a
        jr      z, gbi
gbic    inc     c
        jr      nc, getind
    IF  map_ofs==0
        bit     4, c
        jr      nz, litcat
    ELSE
        jp      m, litcat
    ENDIF

        ex      de, hl
        ex      (sp), hl
        push    hl

        ld      iyl, c
        ld      c, 0
        or      a

        ld      b, (iy+map_disp_bit)
        dec     b
        call    nz, getbits
        ld      l, (iy+map_disp_lo)
        ld      h, (iy+map_disp_hi)
        add     hl, bc

        ;--------
        ex      af, af';'
        ;--------
        pop     bc
        ;--------
        ld      a, b
        or      c
        ;--------
        jr      z, checkreuse
        ;--------
        ex      af, af';'
        ;--------
readofs:

        push    hl
        inc     h
        dec     h
        jr      nz, fixhi
        ld      bc, 2*256+(map_ofs+48)/4
        dec     l
        jr      z, goit
        dec     l
fixhi   ld      bc, 4*256+(map_ofs+32)/16
        jr      z, goit
        dec     c

goit    call    lee8

        ld      iyl, c
        ld      c, b

        ld      b, (iy+map_disp_bit)
        dec     b
        call    nz, getbits
        ld      l, (iy+map_disp_lo)
        ld      h, (iy+map_disp_hi)
        add     hl, bc

        push    hl
        pop     ix
useofs:

        ex      de, hl
        pop     bc

        ex      (sp), hl
        push    hl
        sbc     hl, de
        pop     de
        call    copy_match      ; BANKED (5): was ldir
        pop     hl

        ;--------
        inc     c; BC=1
        ;--------

        jr      mloop

litcat:
      IF  map_ofs==0
        bit     0, c
        jr      z, reuse_exit
      ELSE
        jp      pe, reuse_exit
      ENDIF

        RCHK_HL                 ; BANKED (6)
        ld      b, (hl)
        inc     hl
        RCHK_HL                 ; BANKED (7)
        ld      c, (hl)
        inc     hl
        call    copy_lit        ; BANKED (8): was ldir

        ;--------
        pop     bc
        dec     bc
        ;--------

        jp      mloop

        ;--------
reuse_exit:
        pop     bc
        ret

checkreuse:
        ex      af, af';'

        add     a, a
        jr      nz, .skip

        RCHK_DE                 ; BANKED (9): keeps carry
        ld      a, (de)
        inc     de
        adc     a, a
.skip:
        jr      nc, readofs

        push    ix
        ex      (sp), hl

        or      a ;clear CF

        jr      useofs
        ;--------

getbits jp      p, lee8
        sla     b
        jr      z, gby
        srl     b
        jp      lee16           ; BANKED (10): was defb 250 (JP M,nnnn)
xopy    RCHK_DE                 ; BANKED (11): keeps carry
        ld      a, (de)
        inc     de

lee16   adc     a, a
        jr      z, xopy
        rl      c
        djnz    lee16
        ld      b, c

gby     ex      de, hl
        RCHK_HL                 ; BANKED (12)
        ld      c, (hl)
        inc     hl
        ex      de, hl
        ret

copy    RCHK_DE                 ; BANKED (13): keeps carry
        ld      a, (de)
        inc     de
lee8    adc     a, a
        jr      z, copy
        rl      c
        djnz    lee8
        ret
