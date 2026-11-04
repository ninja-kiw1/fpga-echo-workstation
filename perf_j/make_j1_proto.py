"""Make perf_j/unet_ai_j1.c = deployed src/unet_ai.c (v5.16, sha256 0ced96c8...) plus
two TEST-ONLY additions, both off unless their env var is set:

  UNET_GTRACE=<file>  dump every GDMA ioctl of the last unet_run (bytes, us)
  UNET_J1OVL=1|2      J1 prototype (1 = worker spins, 2 = worker sleeps on a condvar): the readback DMA of strip i runs on a worker
                      thread while the main thread writes strip i+1's input into
                      BRAM (IP idle the whole time; worker joined before start_ap)

Never deployed; used only by strict_net_test_j1 on the board.
"""
import hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "tcp_image/tcp_image_rx/src/unet_ai.c"
text = SRC.read_text(encoding="utf-8")
assert hashlib.sha256(SRC.read_bytes()).hexdigest().startswith("0ced96c88f603605")


def sub(old, new, count=1):
    global text
    n = text.count(old)
    assert n == count, (old[:60], n)
    text = text.replace(old, new)


first = text.index("#include ")
text = text[:first] + "#include <pthread.h>\n#include <sched.h>\n" + text[first:]

sub("static uint64_t now_us(void);                            /* 定义在下面 */",
    """static uint64_t now_us(void);                            /* 定义在下面 */

/* ---- J1 test-only: GDMA call trace ---- */
#define J1_NTR 16384
static uint32_t g_j1_tr_len[J1_NTR], g_j1_tr_us[J1_NTR];
static int      g_j1_ntr;""")

sub("""        g_dma_xfers++;
        t1 = now_us();
        g_dma_io_us += t1 - t0;""",
    """        g_dma_xfers++;
        t1 = now_us();
        g_dma_io_us += t1 - t0;
        if (g_j1_ntr < J1_NTR) {
            g_j1_tr_len[g_j1_ntr] = (uint32_t)chunk;
            g_j1_tr_us[g_j1_ntr]  = (uint32_t)(t1 - t0);
            g_j1_ntr++;
        }""")

sub("/* 本条带已 start_ap: 让待办 memcpy",
    """/* ---- J1 test-only: readback DMA on a worker thread, overlapped with put ---- */
static int             g_j1_ovl = -1;            /* -1 = not decided yet */
static pthread_t       g_j1_th;
static volatile int    g_j1_go, g_j1_done = 1;
static uint32_t        g_j1_off;
static void           *g_j1_dst;
static size_t          g_j1_bytes;
static uint64_t        g_j1_wait_us, g_j1_posts;
static pthread_mutex_t g_j1_mx = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t  g_j1_cv = PTHREAD_COND_INITIALIZER;

static void *j1_worker(void *arg)
{
    (void)arg;
    for (;;) {
        if (g_j1_ovl == 2) {                 /* UNET_J1OVL=2: sleep on a condvar */
            pthread_mutex_lock(&g_j1_mx);
            while (!g_j1_go) {
                pthread_cond_wait(&g_j1_cv, &g_j1_mx);
            }
            pthread_mutex_unlock(&g_j1_mx);
        } else {                             /* UNET_J1OVL=1: spin + yield */
            while (!__atomic_load_n(&g_j1_go, __ATOMIC_ACQUIRE)) {
                sched_yield();
            }
        }
        g_j1_go = 0;
        bram_get_defer(g_j1_off, g_j1_dst, g_j1_bytes);
        __atomic_store_n(&g_j1_done, 1, __ATOMIC_RELEASE);
    }
    return NULL;
}

static int j1_on(void)
{
    if (g_j1_ovl < 0) {
        const char *e = getenv("UNET_J1OVL");
        g_j1_ovl = (e != NULL) ? ((atoi(e) == 2) ? 2 : 1) : 0;
        if (g_j1_ovl && pthread_create(&g_j1_th, NULL, j1_worker, NULL) != 0) {
            g_j1_ovl = 0;
        }
        if (g_j1_ovl) {
            printf("[ai] J1 prototype: readback DMA overlapped with put (worker thread)\\n");
        }
    }
    return g_j1_ovl;
}

static void j1_post(uint32_t off, void *dst, size_t bytes)
{
    g_j1_off = off; g_j1_dst = dst; g_j1_bytes = bytes;
    __atomic_store_n(&g_j1_done, 0, __ATOMIC_RELAXED);
    if (g_j1_ovl == 2) {
        pthread_mutex_lock(&g_j1_mx);
        g_j1_go = 1;
        pthread_cond_signal(&g_j1_cv);
        pthread_mutex_unlock(&g_j1_mx);
    } else {
        __atomic_store_n(&g_j1_go, 1, __ATOMIC_RELEASE);
    }
    g_j1_posts++;
}

/* must be called before start_ap: the IP may only start once the DMA is done */
static void j1_wait(void)
{
    uint64_t t0;

    if (__atomic_load_n(&g_j1_done, __ATOMIC_ACQUIRE)) {
        return;
    }
    t0 = now_us();
    while (!__atomic_load_n(&g_j1_done, __ATOMIC_ACQUIRE)) {
    }
    g_j1_wait_us += now_us() - t0;
}

#define J1_GET(o, d, b) do { if (j1_on()) { j1_post((o), (d), (b)); } \\
                             else { bram_get_defer((o), (d), (b)); } } while (0)

static void j1_dump(void)
{
    const char *path = getenv("UNET_GTRACE");
    FILE *f;
    int i;

    if (g_j1_ovl > 0) {
        printf("[j1] posts=%llu main_wait=%llu us\\n", (unsigned long long)g_j1_posts,
               (unsigned long long)g_j1_wait_us);
    }
    g_j1_posts = g_j1_wait_us = 0u;
    if (path == NULL || (f = fopen(path, "w")) == NULL) {
        return;
    }
    for (i = 0; i < g_j1_ntr; i++) {
        fprintf(f, "%u %u\\n", g_j1_tr_len[i], g_j1_tr_us[i]);
    }
    fclose(f);
}

/* 本条带已 start_ap: 让待办 memcpy""")

sub("                    bram_get_defer(", "                    J1_GET(", 2)
sub("            ovl_arm((size_t)nin * (size_t)rin * WORD_BYTES);",
    "            ovl_arm((size_t)nin * (size_t)rin * WORD_BYTES);\n            j1_wait();")
sub("            ovl_arm((size_t)nin * (size_t)rin_a * WORD_BYTES);",
    "            ovl_arm((size_t)nin * (size_t)rin_a * WORD_BYTES);\n            j1_wait();")
sub("    g_dma_xfers = 0u;\n", "    g_dma_xfers = 0u;\n    g_j1_ntr = 0;\n")
sub("        ovl_report();\n", "        ovl_report();\n        j1_dump();\n")

(HERE / "unet_ai_j1.c").write_text(text, encoding="utf-8")
print("wrote unet_ai_j1.c", hashlib.sha256(text.encode()).hexdigest()[:16])
