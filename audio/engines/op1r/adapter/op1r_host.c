/* The variables and calls op1r's harness header (`harness/op1_host.h`) declares in
 * place of the OP-1 firmware's addresses, defined once per box (#1230). op1r's own
 * tests define the same set (`harness/reverb_test.c`). The adapter fills
 * host_fx_scratch; the rest only has to exist: a plugin's screen code links
 * against them and never runs here (a box draws no plugin screen), and the meter
 * is written and never read. */
#include "op1_plugin.h"

int16_t host_sine[8192];
int32_t host_pitch_inc[128];
uint8_t host_voice_mem[6 * 0x24];
int16_t host_voice_vel[6];
int16_t host_accel[3];
int16_t host_knobs[8][8];
int host_slot;
const uint8_t *host_sample_buffer[8];
int16_t host_fx_knobs[3][8];
int16_t host_fx_meter[3];
void *host_fx_scratch[3];
volatile uint8_t op1_fx_page[3][4];
volatile uint8_t op1_eng_page[4];

void *host_find_node(void *s, const char *id) { (void)s; (void)id; return 0; }
void host_node_set_transform(void *n, const int32_t *m) { (void)n; (void)m; }
void host_node_set_visible(void *n, int on) { (void)n; (void)on; }
void host_screen_draw(void *s, int dt) { (void)s; (void)dt; }
void *host_raster(void) { return 0; }
void host_raster_line(void *r, const void *a, const void *b, int32_t w, const uint8_t *c)
{ (void)r; (void)a; (void)b; (void)w; (void)c; }
void *host_text_ctor(void *o, int32_t x, int32_t y, int32_t sz, const uint8_t *c)
{ (void)x; (void)y; (void)sz; (void)c; return o; }
void host_text_set(void *t, const char *s) { (void)t; (void)s; }
void host_text_draw(void *t, void *r, const int32_t *m) { (void)t; (void)r; (void)m; }
