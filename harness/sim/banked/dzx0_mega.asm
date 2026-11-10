; -----------------------------------------------------------------------------
; Banked port of dzx0_mega.asm (ZX0 decoder by Einar Saukas, "Mega" version)
; from uniabis/z80depacker, for harness/linesim --mode banked.
; Altered source: only input reads, output writes and copies changed
; (helpers in harness/sim/banked/banked.inc). The decoder logic is unchanged.
;
; Edit sites (input pointer HL in the ring, counter IYL), 31 in all, each
; marked "BANKED" below and written as one of these macros:
;   GET_A             x15  ld a,(hl) : inc hl (bit-buffer reload). The check
;                          keeps carry, which some sites shift in later.
;   GET_C             x4   ld c,(hl) : inc hl (offset LSB). Carry survives
;                          for the rr b : rr c that follows.
;   COPY_LITERALS     x4   ldir from the ring -> copy_lit (keeps A, flags).
;   COPY_MATCH        x4   ldir from earlier output -> copy_match.
;   COPY_MATCH_PLUS1  x4   ldir : inc c : ldi -> copy_match : inc c :
;                          OCHK_HL : ldi (the same bytes for every BC).
; -----------------------------------------------------------------------------

        INCLUDE "banked.inc"

        MACRO GET_A
        RCHK_HL
        ld      a, (hl)
        inc     hl
        ENDM

        MACRO GET_C
        RCHK_HL
        ld      c, (hl)
        inc     hl
        ENDM

        MACRO COPY_LITERALS
        call    copy_lit
        ENDM

        MACRO COPY_MATCH
        call    copy_match
        ENDM

        MACRO COPY_MATCH_PLUS1
        call    copy_match
        inc     c
        OCHK_HL
        ldi
        ENDM

banked_decoder:
; -----------------------------------------------------------------------------
; ZX0 decoder by Einar Saukas
; "Mega" version (673 bytes, 28% faster)
; -----------------------------------------------------------------------------
; Parameters:
;   HL: source address (compressed data)
;   DE: destination address (decompressing)
; -----------------------------------------------------------------------------

dzx0_mega:
        ld      bc, $ffff               ; preserve default offset 1
        ld      (dzx0m_last_offset+1), bc
        inc     bc
        jr      dzx0m_literals0

dzx0m_new_offset6:
        ld      c, $fe                  ; prepare negative offset
        add     a, a                    ; obtain offset MSB
        jp      c, dzx0m_new_offset5
        add     a, a
        rl      c
        add     a, a
        jp      c, dzx0m_new_offset3
        add     a, a
        rl      c
        add     a, a
        jp      c, dzx0m_new_offset1
dzx0m_elias_offset1:
        add     a, a
        rl      c
        rl      b
        GET_A                           ; BANKED: was ld a,(hl) : inc hl
        add     a, a
        jp      nc, dzx0m_elias_offset7
dzx0m_new_offset7:
        inc     c
        ret     z                       ; check end marker
        ld      b, c
        GET_C                           ; BANKED: was ld c,(hl) : inc hl
        rr      b                       ; last offset bit becomes first length bit
        rr      c
        ld      (dzx0m_last_offset+1), bc ; preserve new offset
        ld      bc, 1
        jp      c, dzx0m_length7        ; obtain length
        add     a, a
        rl      c
        add     a, a
        jp      c, dzx0m_length5
        add     a, a
        rl      c
        add     a, a
        jp      c, dzx0m_length3
dzx0m_elias_length3:
        add     a, a
        rl      c
        rl      b
        add     a, a
        jp      nc, dzx0m_elias_length1
dzx0m_length1:
        push    hl                      ; preserve source
        ld      hl, (dzx0m_last_offset+1)
        add     hl, de                  ; calculate destination - offset
        COPY_MATCH_PLUS1                ; BANKED: was ldir : inc c : ldi
        pop     hl                      ; restore source
        add     a, a                    ; copy from literals or new offset?
        jr      c, dzx0m_new_offset0
dzx0m_literals0:
        inc     c
        GET_A                           ; BANKED: was ld a,(hl) : inc hl
        add     a, a                    ; obtain length
        jp      c, dzx0m_literals7
        add     a, a
        rl      c
        add     a, a
        jp      c, dzx0m_literals5
        add     a, a
        rl      c
        add     a, a
        jp      c, dzx0m_literals3
dzx0m_elias_literals3:
        add     a, a
        rl      c
        rl      b
        add     a, a
        jp      nc, dzx0m_elias_literals1
dzx0m_literals1:
        COPY_LITERALS                   ; BANKED: was ldir
        add     a, a                    ; copy from last offset or new offset?
        jr      c, dzx0m_new_offset0
        inc     c
        GET_A                           ; BANKED: was ld a,(hl) : inc hl
        add     a, a                    ; obtain length
        jp      c, dzx0m_reuse7
        add     a, a
        rl      c
        add     a, a
        jp      c, dzx0m_reuse5
        add     a, a
        rl      c
        add     a, a
        jp      c, dzx0m_reuse3
dzx0m_elias_reuse3:
        add     a, a
        rl      c
        rl      b
        add     a, a
        jp      nc, dzx0m_elias_reuse1
dzx0m_reuse1:
        push    hl                      ; preserve source
        ld      hl, (dzx0m_last_offset+1)
        add     hl, de                  ; calculate destination - offset
        COPY_MATCH                      ; BANKED: was ldir
        pop     hl                      ; restore source
        add     a, a                    ; copy from literals or new offset?
        jr      nc, dzx0m_literals0

dzx0m_new_offset0:
        ld      c, $fe                  ; prepare negative offset
        GET_A                           ; BANKED: was ld a,(hl) : inc hl
        add     a, a                    ; obtain offset MSB
        jp      c, dzx0m_new_offset7
        add     a, a
        rl      c
        add     a, a
        jp      c, dzx0m_new_offset5
        add     a, a
        rl      c
        add     a, a
        jp      c, dzx0m_new_offset3
dzx0m_elias_offset3:
        add     a, a
        rl      c
        rl      b
        add     a, a
        jp      nc, dzx0m_elias_offset1
dzx0m_new_offset1:
        inc     c
        ret     z                       ; check end marker
        ld      b, c
        GET_C                           ; BANKED: was ld c,(hl) : inc hl
        rr      b                       ; last offset bit becomes first length bit
        rr      c
        ld      (dzx0m_last_offset+1), bc ; preserve new offset
        ld      bc, 1
        jp      c, dzx0m_length1        ; obtain length
        add     a, a
        rl      c
        GET_A                           ; BANKED: was ld a,(hl) : inc hl
        add     a, a
        jp      c, dzx0m_length7
        add     a, a
        rl      c
        add     a, a
        jp      c, dzx0m_length5
dzx0m_elias_length5:
        add     a, a
        rl      c
        rl      b
        add     a, a
        jp      nc, dzx0m_elias_length3
dzx0m_length3:
        push    hl                      ; preserve source
        ld      hl, (dzx0m_last_offset+1)
        add     hl, de                  ; calculate destination - offset
        COPY_MATCH_PLUS1                ; BANKED: was ldir : inc c : ldi
        pop     hl                      ; restore source
        add     a, a                    ; copy from literals or new offset?
        jr      c, dzx0m_new_offset2
dzx0m_literals2:
        inc     c
        add     a, a                    ; obtain length
        jp      c, dzx0m_literals1
        add     a, a
        rl      c
        GET_A                           ; BANKED: was ld a,(hl) : inc hl
        add     a, a
        jp      c, dzx0m_literals7
        add     a, a
        rl      c
        add     a, a
        jp      c, dzx0m_literals5
dzx0m_elias_literals5:
        add     a, a
        rl      c
        rl      b
        add     a, a
        jp      nc, dzx0m_elias_literals3
dzx0m_literals3:
        COPY_LITERALS                   ; BANKED: was ldir
        add     a, a                    ; copy from last offset or new offset?
        jr      c, dzx0m_new_offset2
        inc     c
        add     a, a                    ; obtain length
        jp      c, dzx0m_reuse1
        add     a, a
        rl      c
        GET_A                           ; BANKED: was ld a,(hl) : inc hl
        add     a, a
        jp      c, dzx0m_reuse7
        add     a, a
        rl      c
        add     a, a
        jp      c, dzx0m_reuse5
dzx0m_elias_reuse5:
        add     a, a
        rl      c
        rl      b
        add     a, a
        jp      nc, dzx0m_elias_reuse3
dzx0m_reuse3:
        push    hl                      ; preserve source
        ld      hl, (dzx0m_last_offset+1)
        add     hl, de                  ; calculate destination - offset
        COPY_MATCH                      ; BANKED: was ldir
        pop     hl                      ; restore source
        add     a, a                    ; copy from literals or new offset?
        jr      nc, dzx0m_literals2

dzx0m_new_offset2:
        ld      c, $fe                  ; prepare negative offset
        add     a, a                    ; obtain offset MSB
        jp      c, dzx0m_new_offset1
        add     a, a
        rl      c
        GET_A                           ; BANKED: was ld a,(hl) : inc hl
        add     a, a
        jp      c, dzx0m_new_offset7
        add     a, a
        rl      c
        add     a, a
        jp      c, dzx0m_new_offset5
dzx0m_elias_offset5:
        add     a, a
        rl      c
        rl      b
        add     a, a
        jp      nc, dzx0m_elias_offset3
dzx0m_new_offset3:
        inc     c
        ret     z                       ; check end marker
        ld      b, c
        GET_C                           ; BANKED: was ld c,(hl) : inc hl
        rr      b                       ; last offset bit becomes first length bit
        rr      c
        ld      (dzx0m_last_offset+1), bc ; preserve new offset
        ld      bc, 1
        jp      c, dzx0m_length3        ; obtain length
        add     a, a
        rl      c
        add     a, a
        jp      c, dzx0m_length1
        add     a, a
        rl      c
        GET_A                           ; BANKED: was ld a,(hl) : inc hl
        add     a, a
        jp      c, dzx0m_length7
dzx0m_elias_length7:
        add     a, a
        rl      c
        rl      b
        add     a, a
        jp      nc, dzx0m_elias_length5
dzx0m_length5:
        push    hl                      ; preserve source
        ld      hl, (dzx0m_last_offset+1)
        add     hl, de                  ; calculate destination - offset
        COPY_MATCH_PLUS1                ; BANKED: was ldir : inc c : ldi
        pop     hl                      ; restore source
        add     a, a                    ; copy from literals or new offset?
        jr      c, dzx0m_new_offset4
dzx0m_literals4:
        inc     c
        add     a, a                    ; obtain length
        jp      c, dzx0m_literals3
        add     a, a
        rl      c
        add     a, a
        jp      c, dzx0m_literals1
        add     a, a
        rl      c
        GET_A                           ; BANKED: was ld a,(hl) : inc hl
        add     a, a
        jp      c, dzx0m_literals7
dzx0m_elias_literals7:
        add     a, a
        rl      c
        rl      b
        add     a, a
        jp      nc, dzx0m_elias_literals5
dzx0m_literals5:
        COPY_LITERALS                   ; BANKED: was ldir
        add     a, a                    ; copy from last offset or new offset?
        jr      c, dzx0m_new_offset4
        inc     c
        add     a, a                    ; obtain length
        jp      c, dzx0m_reuse3
        add     a, a
        rl      c
        add     a, a
        jp      c, dzx0m_reuse1
        add     a, a
        rl      c
        GET_A                           ; BANKED: was ld a,(hl) : inc hl
        add     a, a
        jp      c, dzx0m_reuse7
dzx0m_elias_reuse7:
        add     a, a
        rl      c
        rl      b
        add     a, a
        jp      nc, dzx0m_elias_reuse5
dzx0m_reuse5:
        push    hl                      ; preserve source
        ld      hl, (dzx0m_last_offset+1)
        add     hl, de                  ; calculate destination - offset
        COPY_MATCH                      ; BANKED: was ldir
        pop     hl                      ; restore source
        add     a, a                    ; copy from literals or new offset?
        jr      nc, dzx0m_literals4

dzx0m_new_offset4:
        ld      c, $fe                  ; prepare negative offset
        add     a, a                    ; obtain offset MSB
        jp      c, dzx0m_new_offset3
        add     a, a
        rl      c
        add     a, a
        jp      c, dzx0m_new_offset1
        add     a, a
        rl      c
        GET_A                           ; BANKED: was ld a,(hl) : inc hl
        add     a, a
        jp      c, dzx0m_new_offset7
dzx0m_elias_offset7:
        add     a, a
        rl      c
        rl      b
        add     a, a
        jp      nc, dzx0m_elias_offset5
dzx0m_new_offset5:
        inc     c
        ret     z                       ; check end marker
        ld      b, c
        GET_C                           ; BANKED: was ld c,(hl) : inc hl
        rr      b                       ; last offset bit becomes first length bit
        rr      c
        ld      (dzx0m_last_offset+1), bc ; preserve new offset
        ld      bc, 1
        jp      c, dzx0m_length5        ; obtain length
        add     a, a
        rl      c
        add     a, a
        jp      c, dzx0m_length3
        add     a, a
        rl      c
        add     a, a
        jp      c, dzx0m_length1
dzx0m_elias_length1:
        add     a, a
        rl      c
        rl      b
        GET_A                           ; BANKED: was ld a,(hl) : inc hl
        add     a, a
        jp      nc, dzx0m_elias_length7
dzx0m_length7:
        push    hl                      ; preserve source
        ld      hl, (dzx0m_last_offset+1)
        add     hl, de                  ; calculate destination - offset
        COPY_MATCH_PLUS1                ; BANKED: was ldir : inc c : ldi
        pop     hl                      ; restore source
        add     a, a                    ; copy from literals or new offset?
        jp      c, dzx0m_new_offset6
dzx0m_literals6:
        inc     c
        add     a, a                    ; obtain length
        jp      c, dzx0m_literals5
        add     a, a
        rl      c
        add     a, a
        jp      c, dzx0m_literals3
        add     a, a
        rl      c
        add     a, a
        jp      c, dzx0m_literals1
dzx0m_elias_literals1:
        add     a, a
        rl      c
        rl      b
        GET_A                           ; BANKED: was ld a,(hl) : inc hl
        add     a, a
        jp      nc, dzx0m_elias_literals7
dzx0m_literals7:
        COPY_LITERALS                   ; BANKED: was ldir
        add     a, a                    ; copy from last offset or new offset?
        jp      c, dzx0m_new_offset6
        inc     c
        add     a, a                    ; obtain length
        jp      c, dzx0m_reuse5
        add     a, a
        rl      c
        add     a, a
        jp      c, dzx0m_reuse3
        add     a, a
        rl      c
        add     a, a
        jp      c, dzx0m_reuse1
dzx0m_elias_reuse1:
        add     a, a
        rl      c
        rl      b
        GET_A                           ; BANKED: was ld a,(hl) : inc hl
        add     a, a
        jp      nc, dzx0m_elias_reuse7
dzx0m_reuse7:
        push    hl                      ; preserve source
dzx0m_last_offset:
        ld      hl, 0
        add     hl, de                  ; calculate destination - offset
        COPY_MATCH                      ; BANKED: was ldir
        pop     hl                      ; restore source
        add     a, a                    ; copy from literals or new offset?
        jr      nc, dzx0m_literals6

        jp      dzx0m_new_offset6
; -----------------------------------------------------------------------------
