; -----------------------------------------------------------------------------
; banked.inc: shared support for the banked Z80 decoder ports
; (harness/sim/banked/*.asm). See harness/README.md, "Banked mode".
;
; Memory model (MicroBeast paging, ports 0x70-0x73 = windows 0-3):
;
;   0x0000-0xBFFF  output, windows 0-2. The output starts at logical 0x0000
;                  with windows 0-2 on RAM pages 0x20-0x22. When the output
;                  pointer DE reaches 0xC000 and another byte is due, the
;                  windows shift down one page (window 2 takes the next
;                  page) and 0x4000 is subtracted from DE and from a live
;                  match source in HL. At least 32 KB of history stays
;                  mapped, so match offsets up to 32,768 work.
;   window 3 = RAM page 0x3F:
;   0xC000-0xDFFF  8 KB receive ring, filled by the simulated interrupt
;   0xE000-0xEFFF  code (this file, then the decoder)
;   0xF000-0xF7FF  the decoder's buffer
;   0xF800-0xF8FF  ring pointers and variables (below)
;   0xFF00         sentinel HALT, 0xFFF0 stack top
;
; Input: the decoder reads the stream straight from the ring. Before each
; byte it proves the byte has arrived. A counter (IYL, or V_COUNT in memory
; when the port defines RING_COUNT_MEM) holds 1 + the number of bytes
; already proven. The fast path is
;
;       dec  iyl            ; or the memory form, see RCHK_*
;       call z,refill_hl    ; slow path: wrap, store RP, recount or wait
;       ld   a,(hl)
;       inc  hl
;
; refill counts the bytes available from the read pointer, capped at the
; ring end, 255 and the bytes left in this piece's stream (V_REM). It stores
; RP before counting and so before any wait, and a count never exceeds 255,
; so RP is stored at least every 256 bytes. When nothing has arrived it waits
; in ring_wait_loop..ring_wait_exit, the range linesim gets as --wait.
; Bytes past the end of the stream (V_LEN) count as available: decoders that
; read a byte or two ahead do not wait for bytes that never come.
;
; Every helper preserves every register it does not document as changed.
; -----------------------------------------------------------------------------

RING_LO         EQU     0xC000
RING_HI         EQU     0xE000
RING_MASK       EQU     0x1FFF
OUT_LIMIT       EQU     0xC000
PAGE_FIRST      EQU     0x20            ; first output page (physical 0x80000)
PAGE_RING       EQU     0x3F

V_WP            EQU     0xF800          ; ring write pointer (interrupt)
V_RP            EQU     0xF802          ; ring read pointer (decoder)
V_LEN           EQU     0xF804          ; stream length of this piece (linesim --word @len)
V_REM           EQU     0xF806          ; stream bytes not yet counted as available
V_PSTART        EQU     0xF808          ; ring address of this piece's first byte
V_PAGE          EQU     0xF80A          ; RAM page in window 0
V_COUNT         EQU     0xF80B          ; counter when RING_COUNT_MEM is defined
V_ENDSEEN       EQU     0xF80C          ; RP was stored at the stream end
V_LAST          EQU     0xF80E          ; 1 when this is the last piece (linesim --word @last)
V_REBANKS       EQU     0xF810          ; number of window shifts (statistics)
V_WRAPS         EQU     0xF812          ; number of ring wraps (statistics)

; ---- counter access ---------------------------------------------------------

        IFDEF RING_COUNT_MEM

        MACRO COUNT_SET_E               ; counter = E; changes A
        ld      a,e
        ld      (V_COUNT),a
        ENDM

        MACRO COUNT_GET                 ; A = counter
        ld      a,(V_COUNT)
        ENDM

        MACRO COUNT_SUB_B               ; counter -= B; changes A, flags
        ld      a,(V_COUNT)
        sub     b
        ld      (V_COUNT),a
        ENDM

        MACRO COUNT_INC                 ; counter += 1; changes flags
        push    hl
        ld      hl,V_COUNT
        inc     (hl)
        pop     hl
        ENDM

        ; Prove the byte at the input pointer is available, then the caller
        ; reads it. Changes flags except carry.
        MACRO RCHK_HL
        push    hl
        ld      hl,V_COUNT
        dec     (hl)
        pop     hl
        call    z,refill_hl
        ENDM

        MACRO RCHK_DE
        push    hl
        ld      hl,V_COUNT
        dec     (hl)
        pop     hl
        call    z,refill_de
        ENDM

        MACRO RCHK_IX
        push    hl
        ld      hl,V_COUNT
        dec     (hl)
        pop     hl
        call    z,refill_ix
        ENDM

        ELSE

        MACRO COUNT_SET_E
        ld      iyl,e
        ENDM

        MACRO COUNT_GET
        ld      a,iyl
        ENDM

        MACRO COUNT_SUB_B
        ld      a,iyl
        sub     b
        ld      iyl,a
        ENDM

        MACRO COUNT_INC
        inc     iyl
        ENDM

        MACRO RCHK_HL
        dec     iyl
        call    z,refill_hl
        ENDM

        MACRO RCHK_DE
        dec     iyl
        call    z,refill_de
        ENDM

        MACRO RCHK_IX
        dec     iyl
        call    z,refill_ix
        ENDM

        ENDIF

        ; Same as RCHK_HL, but keeps every flag.
        MACRO RCHK_HL_KEEPF
        push    af
        RCHK_HL
        pop     af
        ENDM

; ---- output ------------------------------------------------------------------

        ; Before writing one byte at (DE): shift the windows when DE = 0xC000.
        ; Keeps A and carry; changes the other flags.
        MACRO OCHK
        bit     6,d
        call    nz,ochk_slow
        ENDM

        ; Same for a byte copied from a match source in HL (LDI): HL is
        ; shifted with DE.
        MACRO OCHK_HL
        bit     6,d
        call    nz,ochk_slow_hl
        ENDM

        ; One literal byte from the ring to the output: ldi with both checks.
        ; Changes flags except carry (then ldi sets P/V, H, N as usual).
        MACRO LDI_LIT
        RCHK_HL
        OCHK
        ldi
        ENDM

; ---- entry wrapper -----------------------------------------------------------
; linesim enters here with the stack holding the sentinel. The port defines
; banked_decoder, entered with HL = first input byte in the ring, DE = 0
; (output start), and returning when the stream is decoded.

banked_entry:
        ld      a,PAGE_FIRST
        ld      (V_PAGE),a
        out     (0x70),a
        inc     a
        out     (0x71),a
        inc     a
        out     (0x72),a
        ld      a,PAGE_RING
        out     (0x73),a
        ld      hl,(V_LEN)
        ld      (V_REM),hl
        xor     a
        ld      (V_ENDSEEN),a
        ld      e,1
        COUNT_SET_E                     ; nothing proven yet
        ld      hl,(V_RP)
        ld      (V_PSTART),hl
        ld      de,0
        call    banked_decoder

; After the decoder: move RP to the end of this piece's stream, so the next
; piece starts at its first byte. Stream bytes the decoder never needed are
; waited for only when another piece follows (they must be read past).
banked_finish:
        ld      hl,(V_PSTART)
        ld      de,(V_LEN)
        add     hl,de
        ld      de,(V_REM)
        or      a
        sbc     hl,de                   ; HL = end of the bytes counted so far
        call    ring_norm
        ld      a,(V_LAST)
        or      a
        jr      z,.loop
        ld      de,0                    ; last piece: nothing to read past
.loop:
        ld      (V_RP),hl
        ld      a,d
        or      e
        jr      z,.done
        push    de
        call    ring_wait
        call    ring_avail              ; DE = available (>= 1)
        pop     bc                      ; BC = still to skip
        call    min_de_bc               ; DE = bytes to skip now
        add     hl,de
        call    ring_norm
        push    hl
        ld      h,b
        ld      l,c
        or      a
        sbc     hl,de
        ex      de,hl                   ; DE = still to skip
        pop     hl
        jr      .loop
.done:
        ret

; HL = RING_LO + ((HL - RING_LO) & RING_MASK). Changes A.
ring_norm:
        ld      a,h
        sub     high RING_LO
        and     high RING_MASK
        add     a,high RING_LO
        ld      h,a
        ret

; DE = min(DE, BC). Changes A, flags.
min_de_bc:
        ld      a,e
        sub     c
        ld      a,d
        sbc     a,b
        ret     c
        ld      d,b
        ld      e,c
        ret

; DE = (WP - HL) & RING_MASK, the bytes available from HL. Changes A, flags.
ring_avail:
        ld      de,(V_WP)
        ld      a,e
        sub     l
        ld      e,a
        ld      a,d
        sbc     a,h
        and     high RING_MASK
        ld      d,a
        ret

; Wait until WP differs from HL (at least one byte available at HL).
; Changes A, flags. Only ring_wait_loop..ring_wait_exit counts as waiting.
ring_wait:
        push    de
        ld      de,(V_WP)
        ld      a,e
        cp      l
        jr      nz,ring_wait_exit
        ld      a,d
        cp      h
        jr      nz,ring_wait_exit
ring_wait_loop:
        ld      de,(V_WP)
        ld      a,e
        cp      l
        jr      nz,ring_wait_exit
        ld      a,d
        cp      h
        jr      z,ring_wait_loop
ring_wait_exit:
        pop     de
        ret

; ---- refill: the counter just reached 0 for the byte at the pointer ---------
; Wraps the pointer at the ring end, then sets the counter to the number of
; bytes available from it (1..255), this byte included. Keeps all other
; registers and flags.

refill_ix:
        push    hl
        push    ix
        pop     hl
        call    refill_hl
        push    hl
        pop     ix
        pop     hl
        ret

refill_de:
        ex      de,hl
        call    refill_hl
        ex      de,hl
        ret

refill_hl:
        push    af
        push    bc
        push    de
        ld      a,h
        cp      high RING_HI
        jr      nz,.inring
        ld      h,high RING_LO          ; L is 0: counts never cross the end
        push    hl
        ld      hl,(V_WRAPS)
        inc     hl
        ld      (V_WRAPS),hl
        pop     hl
.inring:
        ld      bc,(V_REM)
        ld      a,b
        or      c
        jr      z,.past_end
        ld      (V_RP),hl               ; consumed up to here; before any wait
        call    ring_wait
        call    ring_avail              ; DE = available, >= 1
        call    min_de_bc               ; .. capped by the stream bytes left
        call    .cap_end_255            ; .. by the ring end and 255: E = count
        push    hl
        ld      hl,(V_REM)
        or      a
        sbc     hl,de
        ld      (V_REM),hl
        pop     hl
        jr      .set
.past_end:
        ; Past the stream end: the bytes count as available. RP is stored
        ; once, at the exact end, so it never runs ahead of WP.
        ld      a,(V_ENDSEEN)
        or      a
        jr      nz,.seen
        ld      (V_RP),hl
        inc     a
        ld      (V_ENDSEEN),a
.seen:
        ld      de,255
        call    .cap_end_255
.set:
        COUNT_SET_E
        pop     de
        pop     bc
        pop     af
        ret

; DE = min(DE, RING_HI - HL, 255), with HL < RING_HI and DE >= 1. Changes A,
; flags, BC.
.cap_end_255:
        ld      a,low RING_HI
        sub     l
        ld      c,a
        ld      a,high RING_HI
        sbc     a,h
        ld      b,a
        call    min_de_bc
        ld      a,d
        or      a
        ret     z
        ld      de,255
        ret

; ---- output window shift -----------------------------------------------------

ochk_slow:                              ; D has bit 6 set: is DE = 0xC000?
        bit     7,d
        ret     z
        jr      rebank

ochk_slow_hl:
        bit     7,d
        ret     z
        jr      rebank_hl

; Shift windows 0-2 down one page and DE (rebank_hl: and HL) down 0x4000.
; Keeps all other registers and flags.
rebank_hl:
        push    af
        ld      a,h
        sub     0x40
        ld      h,a
        jr      rebank_go
rebank:
        push    af
rebank_go:
        ld      a,d
        sub     0x40
        ld      d,a
        ld      a,(V_PAGE)
        inc     a
        ld      (V_PAGE),a
        out     (0x70),a
        inc     a
        out     (0x71),a
        inc     a
        out     (0x72),a
        push    hl
        ld      hl,(V_REBANKS)
        inc     hl
        ld      (V_REBANKS),hl
        pop     hl
        pop     af
        ret

; ---- copies --------------------------------------------------------------------

; Match copy: LDIR of BC bytes from HL (earlier output) to DE, split at the
; output limit with a window shift between the parts. Ends like LDIR: BC = 0,
; HL and DE past the copy. Keeps AF. BC = 0 copies 65,536 bytes, as LDIR
; does (two passes of 32,768).
copy_match:
        push    af
        ld      a,b
        or      c
        jr      nz,.go
        ld      b,0x80                  ; BC = 0: first 32,768 ..
        call    .body
        ld      b,0x80                  ; .. then 32,768 more
.go:
        call    .body
        pop     af
        ret
.body:
        ld      a,b
        or      c
        ret     z
        ld      a,d
        cp      high OUT_LIMIT
        call    z,rebank_hl
        push    hl                      ; [src]
        ld      hl,OUT_LIMIT
        or      a
        sbc     hl,de                   ; HL = room before the limit (>= 1)
        sbc     hl,bc                   ; no borrow above, so CF = 0 here
        jr      nc,.all                 ; BC fits
        add     hl,bc                   ; HL = room
        push    bc                      ; [len, src]
        ld      b,h
        ld      c,l                     ; BC = room
        pop     hl                      ; HL = len
        or      a
        sbc     hl,bc                   ; HL = len - room
        ex      (sp),hl                 ; HL = src, [rest]
        ldir
        pop     bc
        jr      .body
.all:
        pop     hl
        ldir
        ret

; Literal copy: LDIR of BC bytes from the ring at HL to DE, split at the
; output limit, the ring end and the bytes proven available; it waits for
; more input between the parts as a read would. Ends like LDIR. Keeps AF.
; BC = 0 copies 65,536 bytes, as LDIR does (two passes of 32,768).
copy_lit:
        push    af
        ld      a,b
        or      c
        jr      nz,.go
        ld      b,0x80                  ; BC = 0: first 32,768 ..
        call    .body
        ld      b,0x80                  ; .. then 32,768 more
.go:
        call    .body
        pop     af
        ret
.body:
        ld      a,b
        or      c
        ret     z
        ld      a,d
        cp      high OUT_LIMIT
        call    z,rebank
        COUNT_GET
        dec     a                       ; A = bytes proven at HL
        jr      nz,.have
        call    refill_hl               ; counter = count g from HL (>= 1)
        COUNT_INC                       ; nothing consumed yet: g + 1 (mod 256)
        COUNT_GET
        dec     a                       ; A = g
.have:
        inc     b                       ; n = min(A, BC)
        dec     b
        jr      nz,.n1
        cp      c
        jr      c,.n1
        ld      a,c
.n1:
        push    bc                      ; [len]
        ld      b,a
        ld      a,h                     ; n = min(n, RING_HI - HL)
        cp      high RING_HI - 1
        jr      nz,.n2
        xor     a
        sub     l
        jr      z,.n2
        cp      b
        jr      nc,.n2
        ld      b,a
.n2:
        ld      a,d                     ; n = min(n, OUT_LIMIT - DE)
        cp      high OUT_LIMIT - 1
        jr      nz,.n3
        xor     a
        sub     e
        jr      z,.n3
        cp      b
        jr      nc,.n3
        ld      b,a
.n3:
        COUNT_SUB_B                     ; counter stays >= 1
        ld      c,b
        ld      b,0
        ex      (sp),hl                 ; HL = len, [src]
        or      a
        sbc     hl,bc
        ex      (sp),hl                 ; HL = src, [len - n]
        ldir
        pop     bc
        jr      .body
