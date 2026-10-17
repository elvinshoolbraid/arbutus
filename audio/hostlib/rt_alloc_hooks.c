/* rt_alloc_hooks.c — the Linux/glibc half of `--rt-guard` (#52).
 *
 * A counting allocator: we define the malloc family in the *executable*, so ELF
 * symbol interposition routes every allocation in the process through here —
 * the box's `new` (libstdc++'s `operator new` calls `malloc` through the PLT),
 * the host's own containers, anything a dlopen'd box does — and we forward
 * straight to glibc's real allocator via `__libc_*`. Nothing is replaced: every
 * block still comes from, and goes back to, the same glibc arena, so a path
 * that misses these wrappers is still correct, just uncounted.
 *
 * Why not the other candidates (#52 listed them):
 *   - `mallinfo2()` walks the arenas under a lock; #50 already rejected it as
 *     neither RT-safe nor cheap.
 *   - `malloc_info` / `/proc/self/statm` are page-granular and would miss a
 *     small allocation entirely.
 *   - glibc's `__malloc_hook` was removed in glibc 2.34.
 *   - `LD_PRELOAD` + `dlsym(RTLD_NEXT)` is the textbook shim, and the textbook
 *     trap: `dlsym` itself calls `calloc`, so the hook recurses before libc is
 *     up. Calling `__libc_malloc` directly (a glibc export since 2.2.5) avoids
 *     dlsym altogether, and linking the counter in avoids re-exec'ing
 *     `arbutus-audio` with a doctored environment just to bench it.
 *
 * RT-safety: each hook is one relaxed atomic load (the arm flag) and, when
 * armed, one relaxed atomic add. No lock, no allocation, no recursion, no
 * thread-local, no lazy init — the two statics are constant-initialised, so the
 * hooks are correct from the process's first allocation, which happens long
 * before main().
 *
 * Cost when `--rt-guard` is off: the load and a branch that always falls the
 * same way. The audio thread allocates nothing by contract, so the realtime
 * path pays nothing at all.
 *
 * Known undercounts, all in the safe direction (they can hide an allocation,
 * never invent one, because the guard only reacts to a *rise*):
 *   - glibc-internal allocations (`strdup`, `getline`, stdio buffers) bind to
 *     libc's own hidden alias and never reach us, while a later `free()` from
 *     our code does — hence the bias below, so the count cannot go negative and
 *     be misread as "cannot count".
 *   - `valloc`/`pvalloc` are deprecated and not interposed.
 */

#include <errno.h>
#include <stddef.h>

#if defined(__GLIBC__) && !defined(__APPLE__)
#define ARB_RT_COUNTING 1
#else
#define ARB_RT_COUNTING 0
#endif

#if ARB_RT_COUNTING

extern void *__libc_malloc(size_t);
extern void __libc_free(void *);
extern void *__libc_calloc(size_t, size_t);
extern void *__libc_realloc(void *, size_t);
extern void *__libc_memalign(size_t, size_t);

/* Constant-initialised: no static constructor, so no init-order hole. */
static long arb_rt_live = 0;
static int arb_rt_armed = 0;

/* Uncounted frees (see the header comment) can push the delta below zero, and a
 * negative live_blocks() is the callers' "this platform cannot count". Report
 * the count with a fixed offset so it stays positive; only differences matter,
 * and 16M is far more than any block can move a counter inside one render. */
#define ARB_RT_BIAS (1L << 24)

static inline void arb_rt_bump(long d) {
    if (__atomic_load_n(&arb_rt_armed, __ATOMIC_RELAXED))
        __atomic_fetch_add(&arb_rt_live, d, __ATOMIC_RELAXED);
}

void arb_rt_alloc_enable(void) { __atomic_store_n(&arb_rt_armed, 1, __ATOMIC_RELAXED); }

long arb_rt_alloc_live(void) {
    if (!__atomic_load_n(&arb_rt_armed, __ATOMIC_RELAXED)) return -1;
    return ARB_RT_BIAS + __atomic_load_n(&arb_rt_live, __ATOMIC_RELAXED);
}

void *malloc(size_t n) {
    void *p = __libc_malloc(n);
    if (p) arb_rt_bump(1);
    return p;
}

void free(void *p) {
    if (p) arb_rt_bump(-1);
    __libc_free(p);
}

void *calloc(size_t n, size_t sz) {
    void *p = __libc_calloc(n, sz);
    if (p) arb_rt_bump(1);
    return p;
}

void *realloc(void *p, size_t n) {
    void *q = __libc_realloc(p, n);
    if (!p) {
        if (q) arb_rt_bump(1); /* acted as malloc */
    } else if (n == 0) {
        if (!q) arb_rt_bump(-1); /* glibc: realloc(p, 0) frees and returns NULL */
    }
    /* Otherwise it is still exactly one block, or the call failed and the old
     * block is still live: either way the live count is unchanged. */
    return q;
}

void *memalign(size_t align, size_t n) {
    void *p = __libc_memalign(align, n);
    if (p) arb_rt_bump(1);
    return p;
}

/* C11 / C++17 over-aligned new goes through one of these two. */
void *aligned_alloc(size_t align, size_t n) {
    void *p = __libc_memalign(align, n);
    if (p) arb_rt_bump(1);
    return p;
}

int posix_memalign(void **out, size_t align, size_t n) {
    void *p;
    if (align < sizeof(void *) || (align & (align - 1)) != 0) return EINVAL;
    p = __libc_memalign(align, n);
    if (!p) return ENOMEM;
    arb_rt_bump(1);
    *out = p;
    return 0;
}

#else /* !ARB_RT_COUNTING */

void arb_rt_alloc_enable(void) {}
long arb_rt_alloc_live(void) { return -1; }

#endif
