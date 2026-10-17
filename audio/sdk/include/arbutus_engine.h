/* arbutus_engine.h — Arbutus-1 engine ABI, version 2.
 *
 * The contract is docs/design/engine-spec.md §3, with §15 the ABI 2 delta; this
 * file is that contract in code and wins wherever the prose disagrees (§14). C
 * at the boundary so a box can be written in anything; what is inside the box
 * is the author's business.
 *
 * The pre-release rule (Elfy, 2026-09-17). ABI 2 has not shipped outside this
 * repo, so a struct does not grow by appending into _reserved: a new field goes
 * in its logical place as a named field, the layout is recomputed with no
 * implicit padding, every offset is re-pinned in tests/abi_layout.h (C and
 * C++), and every box is rebuilt. ARB_ABI stays 2 until something has shipped;
 * the _reserved tails are headroom for after the first release, when the ABI 1
 * rule comes back: never reorder, append inside _reserved, bump ARB_ABI on any
 * break. The host supports exactly one ARB_ABI and refuses every other (§7.4).
 * tests/test_abi.cpp pins every size and offset below from C and from C++.
 *
 * Compiles as C99 and as C++20; the host and the box may disagree about the
 * language but not about the layout.
 */
#ifndef ARBUTUS_ENGINE_H
#define ARBUTUS_ENGINE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define ARB_ABI          2
#define ARB_MAX_PARAMS   8
#define ARB_MAX_SOFTKEYS 4
#define ARB_MAX_ASSETS   3           /* asset keys one engine may declare (§3.2) */
#define ARB_MAX_OUTPUTS  4           /* modulator control outputs; the MOD page's number, not a DSP limit (§15.9) */
#define ARB_MAX_BLOCK    2048        /* default; [TBD-Elfy] */

/* The two entry points are the only symbols a box must export; everything else
 * is hidden so two boxes with the same helper name cannot collide at dlopen. */
#if defined(_WIN32)
#  define ARB_EXPORT __declspec(dllexport)
#else
#  define ARB_EXPORT __attribute__((visibility("default")))
#endif

/* ---- capabilities (engine.json "caps" and arb_engine_desc.caps) ---- */
#define ARB_CAP_AUDIO_IN    (1u << 0)
#define ARB_CAP_AUDIO_OUT   (1u << 1)
#define ARB_CAP_EVENTS_IN   (1u << 2)
#define ARB_CAP_EVENTS_OUT  (1u << 3)
#define ARB_CAP_SIDECHAIN   (1u << 4)   /* wants the side[] port; FX page shows a source selector */
#define ARB_CAP_SEQUENCER   (1u << 5)   /* may fill the SEQ slot; receives soft-keys */
#define ARB_CAP_MODULATOR   (1u << 6)   /* fills a MOD-rack tab; writes ctl_out (modulation-spec §3) */

/* Manifest spelling of the bits above, in bit order. §3.8 writes caps as
 * strings so the library browser never loads a binary; the loader maps them
 * back through this table, so the two can never mean different things. */
#define ARB_CAP_NAMES { "audio_in", "audio_out", "events_in", "events_out", \
                        "sidechain", "sequencer", "modulator" }
#define ARB_CAP_COUNT 7

/* ---- events ---- */
typedef enum {
    ARB_EV_NOTE_ON = 1,   /* a = note 0..127, b = velocity 0..1            */
    ARB_EV_NOTE_OFF,      /* a = note                                       */
    ARB_EV_CLOCK,         /* one per tick at host.ppqn; a = tick in beat    */
    ARB_EV_TRANSPORT,     /* a = 0 stop, 1 start, 2 locate; b = song beats  */
    ARB_EV_SOFTKEY,       /* a = key 0..3, b = 1 down / 0 up; flags bit0 = SHIFT held */
    /* ABI 2, appended; the values above are unchanged. There is no bend
     * event: bend reaches an engine as arb_host.pitch_st, resolved by the host,
     * so an engine has one pitch mechanism, not two. */
    ARB_EV_CC             /* a = controller 0..127, b = 0..1                */
} arb_event_type;

typedef struct {
    uint32_t frame;       /* offset within this block, 0 <= frame < frames  */
    uint16_t type;        /* arb_event_type                                 */
    uint16_t a;
    float    b;
    uint32_t flags;
} arb_event;

/* ---- parameters ---- */
typedef enum { ARB_PARAM_CONTINUOUS = 0, ARB_PARAM_INTEGER, ARB_PARAM_ENUM } arb_param_type;
typedef enum { ARB_CURVE_LIN = 0, ARB_CURVE_LOG } arb_curve;

typedef struct {
    char           id[16];      /* stable snake_case; never renamed once shipped   */
    char           name[8];     /* screen label, UPPERCASE; length [TBD-Elfy]      */
    arb_param_type type;
    arb_curve      curve;       /* mapping of normalised 0..1 onto min..max         */
    float          min, max, def;
    float          step;        /* per encoder detent, in units; 0 = host default   */
    char           unit[8];
    const char* const* labels;  /* ENUM: (max-min+1) strings; otherwise NULL        */
    /* 1 = a keys.VEL / keys.NOTE route to this param is applied per voice by the
     * engine, through set_voice_mod (modulation-spec §8). 0 = the host applies
     * such a route globally with the last note's value. */
    uint8_t        per_voice;
    /* 1 = the value is per pad, set with set_note_param; set_param on this slot
     * is ignored (§15.3). */
    uint8_t        per_note;
    uint8_t        _reserved[14];
} arb_param_desc;

typedef struct { char id[16]; char label[8]; } arb_softkey_desc;

/* One modulator output (modulation-spec §3.1). `name` is the screen label. */
typedef struct {
    char    id[16];             /* stable snake_case, like a param id          */
    char    name[8];            /* UPPERCASE                                   */
    uint8_t bipolar;            /* 1 = -1..1, 0 = 0..1                         */
    uint8_t _pad[7];
} arb_output_desc;

typedef struct {
    uint32_t         abi;           /* == ARB_ABI                              */
    uint32_t         caps;          /* ARB_CAP_*                               */
    char             id[64];        /* "author/name"                           */
    char             version[16];   /* semver                                  */
    uint32_t         num_params;    /* <= ARB_MAX_PARAMS; slots 4-7 may be empty */
    uint32_t         num_softkeys;  /* <= ARB_MAX_SOFTKEYS; sequencer only     */
    arb_param_desc   params[ARB_MAX_PARAMS];
    arb_softkey_desc softkeys[ARB_MAX_SOFTKEYS];
    uint32_t         latency_frames;/* reported, not compensated in v1         */
    uint32_t         num_outputs;   /* <= ARB_MAX_OUTPUTS; modulators only (1..4), else 0 */
    arb_output_desc  outputs[ARB_MAX_OUTPUTS];
    /* The asset keys this engine accepts; the host refuses a patch or kit that
     * names any other one (§7.4). num_assets == 0: the engine takes no assets. */
    char             asset_keys[ARB_MAX_ASSETS][16];/* stable snake_case       */
    uint8_t          num_assets;    /* <= ARB_MAX_ASSETS                       */
    uint8_t          honours_env;   /* 1 = ENV page values are applied         */
    uint8_t          _reserved[62];
} arb_engine_desc;

/* ---- the host block: read-only, passed to every call ---- */
typedef struct {
    uint32_t sample_rate;
    uint32_t max_block;
    double   tempo_bpm;
    int64_t  song_frame;        /* transport position                          */
    uint32_t ppqn;              /* clock ticks per quarter; default 24 [TBD-Elfy] */
    uint32_t rng_seed;          /* engines seed from here; determinism for goldens */
    /* ENV page (DDR: per-voice, never over the mixed output)                  */
    float    env_a, env_d, env_s, env_r;   /* s, s, 0..1, s                    */
    /* ENV SHIFT row (DDR-052). PolyHost applies all three; see sdk/poly.hpp.  */
    float    env_glide;         /* s; portamento in mono/legato, and the bend ramp */
    float    env_detune;        /* cents; unison spread, outermost voice to outermost */
    float    bend_range;        /* semitones; what a full bend is worth         */
    /* The instrument's pitch offset in semitones, resolved by the host: bend
     * position x bend_range plus the host.pitch route (modulation-spec §5).
     * Every voice follows it, ringing ones included. A block-rate value: an
     * engine smooths it (PolyHost does).                                      */
    float    pitch_st;
    /* IMU: ax ay az (g) gx gy gz (deg/s), sampled on the control thread at
     * ~200 Hz (modulation-spec §13). All zeros when no IMU is fitted, so an
     * engine never has to ask whether there is one. [units: default chosen
     * here, the LSM6DSO's own; the spec names only the axes] */
    float    motion[6];
    uint8_t  playing;
    uint8_t  env_mode;          /* arb_env_mode                                */
    uint8_t  _reserved[46];
} arb_host;

/* env_mode values, so nobody writes a bare 2 and means something else. */
typedef enum {
    ARB_ENV_POLY = 0, ARB_ENV_MONO, ARB_ENV_LEGATO, ARB_ENV_UNISON
} arb_env_mode;

/* ---- one block of I/O ---- */
typedef struct {
    const float*     in[2];     /* NULL when the slot provides no audio in     */
    const float*     side[2];   /* NULL unless ARB_CAP_SIDECHAIN and a source is set */
    float*           out[2];    /* always valid; ALWAYS write both channels    */
    const arb_event* ev_in;
    arb_event*       ev_out;
    uint32_t*        n_ev_out;
    uint32_t         frames;    /* <= host.max_block; any value, every call    */
    uint32_t         n_ev_in;
    uint32_t         ev_out_cap;
    /* A modulator writes one value per output per block (modulation-spec §7):
     * bipolar outputs -1..1, unipolar 0..1. The host zeroes all four before
     * every process() and reads them after. Other roles leave them alone. */
    float            ctl_out[ARB_MAX_OUTPUTS];
    /* ABI 1 had none. The host allocates arb_io, so growth at the end never
     * broke a box, but 28 bytes also makes the struct's tail padding explicit
     * (128 bytes) and gives the MOD rack room without a re-layout. */
    uint8_t          _reserved[28];
} arb_io;

/* Which per-voice source a set_voice_mod route reads (modulation-spec §8). */
typedef enum { ARB_VSRC_VEL = 0, ARB_VSRC_NOTE } arb_voice_src;

typedef struct {
    void* (*create)(const arb_host* host, const char* box_dir);  /* load assets here; NULL = failed */
    void  (*destroy)(void* h);
    void  (*reset)(void* h);                                     /* silence + clear state */
    void  (*set_param)(void* h, uint32_t idx, float normalised); /* 0..1                   */
    float (*get_param)(void* h, uint32_t idx);                   /* 0..1                   */
    void  (*process)(void* h, const arb_host* host, arb_io* io);

    /* Samples and kits are *content*, not parameters (emulator DDR-037), so
     * they do not cross as a float 0..1. One file per declared key.
     *
     * CONTROL THREAD ONLY. It may allocate, it may read files, it may take as
     * long as it likes. The host calls it after create() and before the engine
     * is placed in the graph, never once it is rendering. §3.7.1 is not
     * weakened: no I/O ever happens in process().
     *
     * `key` is one of arb_engine_desc::asset_keys; `path` is absolute. Returns
     * 0 on success, non-zero to refuse the load. NULL = takes no assets. */
    int   (*load_asset)(void* h, const char* key, const char* path);

    /* Per-pad value of a param flagged per_note (§15.3). Same thread rules as
     * set_param. NULL = the engine has no per_note params. */
    void  (*set_note_param)(void* h, uint8_t note, uint32_t idx, float normalised);

    /* The readout for param `idx` at `normalised`, e.g. "1/8", "STOP", "3.2 Hz"
     * (modulation-spec §3.3). Writes a NUL-terminated string of at most n-1
     * bytes and returns non-zero; returns 0 to let the host draw its default.
     *
     * The value is an argument, not read from the engine, so the host can label
     * any position -- the knob, a range popup's ends, a modulated ghost -- and
     * so this is callable on the control thread while process() runs. It must
     * therefore depend only on (idx, normalised), state fixed at create(), and
     * state the box publishes atomically for it (an std::atomic process() or
     * load_asset() stores, as pattern's AUTO length; engine-spec §15.1): no
     * writes, no unsynchronised reads of anything set_param or process()
     * changes. Such a readout may lag by one block. NULL = host default for
     * every param.
     * [deviation: §15.1 lists (h, idx, buf, n); the value argument is added for
     * the reasons above -- audio/CLAUDE.md defaults table] */
    int   (*format_param)(void* h, uint32_t idx, float normalised, char* buf, uint32_t n);

    /* A keys.VEL or keys.NOTE route to a per_voice param (modulation-spec §8):
     * each voice then reads that param at knob + depth * source for its own
     * lifetime. `src` is arb_voice_src; depth 0 removes the route. Same thread
     * rules as set_param. NULL = no per_voice params. */
    void  (*set_voice_mod)(void* h, uint32_t idx, uint8_t src, float depth);

    uint8_t _reserved[64];
} arb_vtable;

/* Exactly these two exported symbols, C linkage. */
ARB_EXPORT const arb_engine_desc* arb_describe(void);   /* callable before create(); no allocation */
ARB_EXPORT const arb_vtable*      arb_entry(void);

/* Host-side conventions the struct definitions cannot state:
 *  - create() returns NULL if the box cannot start; the host then refuses the
 *    load rather than running a half-built engine.
 *  - the host sets *io->n_ev_out = 0 and ctl_out[] to zero before process();
 *    an engine that emits nothing may leave them alone. Emitted events must be
 *    sorted by frame.
 *  - io->ev_in is sorted by frame and every frame is < io->frames.
 *  - out[0] and out[1] may alias in[0] and in[1]. Read before you write.
 *  - load_asset() is called once per key, after create(), on the control
 *    thread. An engine that declares keys but leaves the slot NULL is
 *    ENGINE BROKEN; so is one that flags a param per_note without
 *    set_note_param, or per_voice without set_voice_mod.
 *  - set_param, set_note_param and set_voice_mod are called by whichever
 *    thread owns the instance -- the control thread before it enters the
 *    graph, the audio thread between blocks after -- never concurrently with
 *    process(). format_param is the one call that may be concurrent.
 */

#ifdef __cplusplus
}
#endif
#endif /* ARBUTUS_ENGINE_H */
