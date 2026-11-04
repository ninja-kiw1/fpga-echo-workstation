/*
 * host_test_rx.c —— 主机端功能测试(测试用, 不是交付物)。
 *
 * 思路: 直接 #include 交付的主程序 .c 文件。这样里面的 static 函数
 * receive_frame() 在本文件里可见, 可以**原样调用交付的那份代码**,
 * 一行都不改; 我们只把它的数据来源(recv)换成内存里的字节流,
 * 从而精确模拟 TCP 的粘包/拆包/乱序/截断。
 *
 * 覆盖:
 *   - 多种分块大小(1 B / 7 B / 1541 B / 1542 B ...)下都能收满整幅;
 *   - 收到的缓冲区与发出的原图**逐字节一致**(行放置正确);
 *   - 整幅 CRC 与 Python 端独立算出的 0xC35A 相同;
 *   - 单行 CRC 被篡改 -> rows_ok=511 / crc_err=1 / 返回 -3;
 *   - 行号越界 -> 失步, seq_err=1, 返回 -1;
 *   - 控制头魔数不对 / 模式越界 -> 返回 -1;
 *   - 字节流被截断 -> 返回 -1。
 */
#define main server_main_disabled
#include "tcp_image_server.c"
#undef main

#include <stdio.h>
#include <string.h>

/* ------------------------------------------------------------------ */
/* 伪造的 recv(): 从内存字节流按可配置分块喂数据                       */
/* ------------------------------------------------------------------ */
static const uint8_t *g_stream = NULL;
static size_t         g_len    = 0u;
static size_t         g_pos    = 0u;
static size_t         g_chunk  = 0u;   /* 0 = 一次给完 */

ssize_t recv(int fd, void *buf, size_t len, int flags)
{
    size_t avail;
    size_t n;

    (void)fd;
    (void)flags;

    if (g_pos >= g_len) {
        return 0;                      /* 流结束 */
    }
    avail = g_len - g_pos;
    n = (avail < len) ? avail : len;
    if ((g_chunk != 0u) && (n > g_chunk)) {
        n = g_chunk;                   /* 模拟 TCP 只到达了一小部分 */
    }
    memcpy(buf, g_stream + g_pos, n);
    g_pos += n;
    return (ssize_t)n;
}

/* ------------------------------------------------------------------ */
/* 仅为了链接通过的空壳(被测的 main 不会被调用)                       */
/* ------------------------------------------------------------------ */
int open(const char *p, int f, ...) { (void)p; (void)f; return -1; }
void *mmap(void *a, size_t l, int pr, int fl, int fd, off_t o)
{ (void)a; (void)l; (void)pr; (void)fl; (void)fd; (void)o; return MAP_FAILED; }
int munmap(void *a, size_t l) { (void)a; (void)l; return -1; }
long sysconf(int n) { (void)n; return 4096; }
int socket(int d, int t, int p) { (void)d; (void)t; (void)p; return -1; }
int bind(int f, const struct sockaddr *a, socklen_t l) { (void)f; (void)a; (void)l; return -1; }
int listen(int f, int b) { (void)f; (void)b; return -1; }
int accept(int f, struct sockaddr *a, socklen_t *l) { (void)f; (void)a; (void)l; return -1; }
int setsockopt(int f, int lv, int o, const void *v, socklen_t ol)
{ (void)f; (void)lv; (void)o; (void)v; (void)ol; return -1; }
const char *inet_ntop(int af, const void *s, char *d, socklen_t sz)
{ (void)af; (void)s; (void)sz; return d; }
uint16_t htons(uint16_t v) { return v; }
uint16_t ntohs(uint16_t v) { return v; }
uint32_t htonl(uint32_t v) { return v; }

/* 信号 / 发送路径不测, 补空壳只为链接通过。
 * (sigaction/sigemptyset 的 struct 定义在 test/shim/shim_extra.h 里) */
int sigemptyset(int *set) { if (set != NULL) { *set = 0; } return 0; }
int sigaction(int sig, const struct sigaction *act, struct sigaction *oldact)
{ (void)sig; (void)act; (void)oldact; return 0; }
ssize_t send(int fd, const void *buf, size_t len, int flags)
{ (void)fd; (void)buf; (void)flags; return (ssize_t)len; }

/* 这个 MinGW(6.2.0) 的 libc 里没有 clock_gettime, 补一个空实现。
 * 被测路径只拿它算吞吐, 不影响接收逻辑。 */
int clock_gettime(clockid_t clk_id, struct timespec *tp)
{
    (void)clk_id;
    tp->tv_sec  = 0;
    tp->tv_nsec = 0;
    return 0;
}

/* ------------------------------------------------------------------ */
/* 测试数据                                                            */
/* ------------------------------------------------------------------ */
#define EXPECT_FRAME_CRC16  0xC35Au
#define EXPECT_ROW0_CRC16   0x6106u
#define EXPECT_ROW100_CRC16 0x75CEu
#define EXPECT_ROW511_CRC16 0x98CDu

static uint8_t g_image[IMG_TOTAL_BYTES];
static uint8_t g_ctrl[CTRL_BYTES];
static uint8_t g_pkt_stream[STREAM_TOTAL_BYTES];
static uint8_t g_wire[WIRE_TOTAL_BYTES];      /* 4 字节控制头 + 512 个行包 */
static uint8_t g_rxbuf[IMG_TOTAL_BYTES];
static struct frame_status g_st;
static uint32_t g_mode;

struct case_result {
    int      rc;
    uint32_t mode;
    uint32_t rows_ok;
    uint32_t crc_err;
    uint32_t seq_err;
    uint32_t bytes;
    uint16_t frame_crc;
    int      identical;
};

/* 每行用按行号播种的 LCG 生成, 保证 512 行互不相同 */
static void build_image(void)
{
    uint32_t r;
    uint32_t c;

    for (r = 0u; r < PKT_COUNT; r++) {
        uint32_t s = r * 2654435761u;
        for (c = 0u; c < IMG_ROW_BYTES; c++) {
            s = (s * 1664525u) + 1013904223u;
            g_image[(size_t)r * IMG_ROW_BYTES + c] = (uint8_t)(s >> 24);
        }
    }
}

/* 按协议把图像打成 512 个行包 */
static void build_stream(void)
{
    uint32_t r;

    for (r = 0u; r < PKT_COUNT; r++) {
        uint8_t       *p   = g_pkt_stream + ((size_t)r * PKT_TOTAL_BYTES);
        const uint8_t *src = g_image + ((size_t)r * IMG_ROW_BYTES);

        proto_put_le16(p + PKT_OFF_ROW, (uint16_t)r);
        proto_put_le16(p + PKT_OFF_LEN, (uint16_t)PKT_PAYLOAD_BYTES);
        proto_put_le16(p + PKT_OFF_CRC16, crc16_ccitt_false(src, IMG_ROW_BYTES));
        memcpy(p + PKT_OFF_PAYLOAD, src, IMG_ROW_BYTES);
    }
}

/*
 * 跑一个用例。corrupt_row >= 0 时篡改该行的 CRC 字段;
 * bad_row >= 0 时把第 bad_row 个包的"行号"改成非法值。
 */
static struct case_result exec_case(size_t chunk, size_t wire_len,
                                    int corrupt_row, int bad_row,
                                    uint16_t ctrl_mode, uint16_t ctrl_magic)
{
    struct case_result r;

    build_stream();

    if (corrupt_row >= 0) {
        uint8_t *p = g_pkt_stream + ((size_t)corrupt_row * PKT_TOTAL_BYTES);
        proto_put_le16(p + PKT_OFF_CRC16,
                       (uint16_t)(proto_get_le16(p + PKT_OFF_CRC16) ^ 0x0001u));
    }
    if (bad_row >= 0) {
        uint8_t *p = g_pkt_stream + ((size_t)bad_row * PKT_TOTAL_BYTES);
        proto_put_le16(p + PKT_OFF_ROW, 9999u);
    }

    /* 组装线上字节流: 4 字节控制头 + 512 个行包 */
    proto_put_le16(g_ctrl + CTRL_OFF_MAGIC, ctrl_magic);
    proto_put_le16(g_ctrl + CTRL_OFF_MODE,  ctrl_mode);
    memcpy(g_wire, g_ctrl, CTRL_BYTES);
    memcpy(g_wire + CTRL_BYTES, g_pkt_stream, STREAM_TOTAL_BYTES);

    memset(g_rxbuf, 0xAA, sizeof(g_rxbuf));
    memset(&g_st, 0, sizeof(g_st));
    g_mode = 0u;

    g_stream = g_wire;
    g_len    = wire_len;
    g_pos    = 0u;
    g_chunk  = chunk;

    /* verbose=1: receive_frame 只在 verbose 时才顺便算整幅 frame CRC,
     * 本用例要核对这个值, 所以必须开 verbose(逐包日志只在出错时才打,
     * 不会刷屏)。 */
    r.rc        = receive_frame(0, g_rxbuf, &g_st, &g_mode, 1);
    r.mode      = g_mode;
    r.rows_ok   = g_st.rows_ok;
    r.crc_err   = g_st.crc_errors;
    r.seq_err   = g_st.seq_errors;
    r.bytes     = g_st.bytes_recv;
    r.frame_crc = (uint16_t)g_st.frame_crc16;
    r.identical = (memcmp(g_rxbuf, g_image, IMG_TOTAL_BYTES) == 0) ? 1 : 0;
    return r;
}

int main(void)
{
    int fails = 0;

    build_image();
    build_stream();

    printf("=== host functional test of the REAL receive path ===\n");
    printf("image %ux%u RGB888 = %u bytes; %u packets x %u bytes = %u bytes stream\n",
           (unsigned)IMG_WIDTH, (unsigned)IMG_HEIGHT, (unsigned)IMG_TOTAL_BYTES,
           (unsigned)PKT_COUNT, (unsigned)PKT_TOTAL_BYTES, (unsigned)STREAM_TOTAL_BYTES);
    printf("expect frame CRC16 = 0x%04X\n\n", (unsigned)EXPECT_FRAME_CRC16);

    /* ---- 正向: 各种分块大小 ---- */
    {
        static const size_t chunks[] = { 0u, 1u, 3u, 7u, 1000u, 1541u, 1542u, 4096u, 65536u };
        size_t i;
        for (i = 0u; i < (sizeof(chunks) / sizeof(chunks[0])); i++) {
            struct case_result r = exec_case(chunks[i], WIRE_TOTAL_BYTES, -1, -1, 3, CTRL_MAGIC);
            int ok = (r.rc == 0) && (r.mode == 3u) && (r.rows_ok == PKT_COUNT) &&
                     (r.crc_err == 0u) && (r.seq_err == 0u) &&
                     (r.bytes == STREAM_TOTAL_BYTES) &&
                     (r.frame_crc == EXPECT_FRAME_CRC16) && (r.identical == 1);
            printf("[%s] chunk=%-6u rc=%2d mode=%u rows=%3u crc_err=%u seq_err=%u bytes=%u frame_crc=0x%04X identical=%d\n",
                   ok ? "PASS" : "FAIL", (unsigned)chunks[i], r.rc, (unsigned)r.mode,
                   (unsigned)r.rows_ok, (unsigned)r.crc_err, (unsigned)r.seq_err,
                   (unsigned)r.bytes, (unsigned)r.frame_crc, r.identical);
            if (ok == 0) { fails++; }
        }
    }

    printf("\n-- negative cases --\n");

    /* ---- 篡改某行的 CRC ---- */
    {
        struct case_result r = exec_case(1000u, WIRE_TOTAL_BYTES, 100, -1, 0, CTRL_MAGIC);
        int ok = (r.rc == -3) && (r.rows_ok == (PKT_COUNT - 1u)) &&
                 (r.crc_err == 1u) && (r.bytes == STREAM_TOTAL_BYTES);
        printf("[%s] corrupt CRC on row 100: rc=%d rows=%u crc_err=%u  (expect rc=-3 rows=511 crc_err=1)\n",
               ok ? "PASS" : "FAIL", r.rc, (unsigned)r.rows_ok, (unsigned)r.crc_err);
        if (ok == 0) { fails++; }
    }

    /* ---- 行号越界 -> 失步 ---- */
    {
        struct case_result r = exec_case(1000u, WIRE_TOTAL_BYTES, -1, 5, 0, CTRL_MAGIC);
        int ok = (r.rc == -1) && (r.seq_err == 1u);
        printf("[%s] bad row field in packet 5: rc=%d seq_err=%u  (expect rc=-1 seq_err=1)\n",
               ok ? "PASS" : "FAIL", r.rc, (unsigned)r.seq_err);
        if (ok == 0) { fails++; }
    }

    /* ---- 控制头魔数不对 -> 失步 ---- */
    {
        struct case_result r = exec_case(1000u, WIRE_TOTAL_BYTES, -1, -1, 0, 0x1234u);
        int ok = (r.rc == -1);
        printf("[%s] bad control magic 0x1234: rc=%d  (expect rc=-1)\n",
               ok ? "PASS" : "FAIL", r.rc);
        if (ok == 0) { fails++; }
    }

    /* ---- 控制头模式越界 -> 失步 ---- */
    {
        struct case_result r = exec_case(1000u, WIRE_TOTAL_BYTES, -1, -1, 9, CTRL_MAGIC);
        int ok = (r.rc == -1);
        printf("[%s] bad control mode 9: rc=%d  (expect rc=-1)\n",
               ok ? "PASS" : "FAIL", r.rc);
        if (ok == 0) { fails++; }
    }

    /* ---- 流被截断 ---- */
    {
        struct case_result r = exec_case(1000u, WIRE_TOTAL_BYTES - 100u, -1, -1, 0, CTRL_MAGIC);
        int ok = (r.rc == -1);
        printf("[%s] truncated stream (-100 B): rc=%d rows=%u  (expect rc=-1)\n",
               ok ? "PASS" : "FAIL", r.rc, (unsigned)r.rows_ok);
        if (ok == 0) { fails++; }
    }

    /* ---- 行放置抽查 ---- */
    {
        struct case_result r = exec_case(7u, WIRE_TOTAL_BYTES, -1, -1, 0, CTRL_MAGIC);
        uint16_t c0   = crc16_ccitt_false(g_rxbuf + (0u * IMG_ROW_BYTES), IMG_ROW_BYTES);
        uint16_t c100 = crc16_ccitt_false(g_rxbuf + (100u * IMG_ROW_BYTES), IMG_ROW_BYTES);
        uint16_t c511 = crc16_ccitt_false(g_rxbuf + (511u * IMG_ROW_BYTES), IMG_ROW_BYTES);
        int ok = (r.rc == 0) && (c0 == EXPECT_ROW0_CRC16) &&
                 (c100 == EXPECT_ROW100_CRC16) && (c511 == EXPECT_ROW511_CRC16);
        printf("[%s] row placement: row0=0x%04X row100=0x%04X row511=0x%04X  (expect 0x%04X/0x%04X/0x%04X)\n",
               ok ? "PASS" : "FAIL", (unsigned)c0, (unsigned)c100, (unsigned)c511,
               (unsigned)EXPECT_ROW0_CRC16, (unsigned)EXPECT_ROW100_CRC16,
               (unsigned)EXPECT_ROW511_CRC16);
        if (ok == 0) { fails++; }
    }

    /* ---- 像素格式转换往返: 紧包 RGB888 <-> DDR 32bit/像素 ----
     * 这正是板端在"接收 -> DMA"和"DMA -> 回发"之间新加的两步, 纯算法,
     * 不碰硬件, 所以在这里真跑一遍: 786432 -> 1048576 -> 786432 必须逐字节还原。 */
    {
        static uint8_t ddr32[IMG_WIDTH * IMG_HEIGHT * IMG_PIX_BYTES];
        static uint8_t back[IMG_TOTAL_BYTES];
        uint32_t w0;
        int ok_round, ok_layout;

        expand_rgb888_to_ddr(g_image, ddr32);
        pack_ddr_to_rgb888(ddr32, back);

        ok_round = (memcmp(back, g_image, IMG_TOTAL_BYTES) == 0) ? 1 : 0;

        /* 抽查第 0 个像素在 DDR 里的排布: {8'h00, R, G, B} */
        w0 = (uint32_t)ddr32[0] | ((uint32_t)ddr32[1] << 8) |
             ((uint32_t)ddr32[2] << 16) | ((uint32_t)ddr32[3] << 24);
        ok_layout = (((w0 >> 24) == 0u) &&
                     (((w0 >> 16) & 0xFFu) == g_image[0]) &&   /* R */
                     (((w0 >>  8) & 0xFFu) == g_image[1]) &&   /* G */
                     (( w0        & 0xFFu) == g_image[2]))     /* B */
                    ? 1 : 0;

        printf("[%s] pixel fmt round-trip: 786432 -> %u -> 786432 exact=%d, "
               "pixel0={00,%u,%u,%u} layout=%d\n",
               (ok_round && ok_layout) ? "PASS" : "FAIL",
               (unsigned)(IMG_WIDTH * IMG_HEIGHT * IMG_PIX_BYTES),
               ok_round,
               (unsigned)((w0 >> 16) & 0xFFu), (unsigned)((w0 >> 8) & 0xFFu),
               (unsigned)(w0 & 0xFFu), ok_layout);
        if (ok_round == 0 || ok_layout == 0) { fails++; }
    }

    printf("\n=== TOTAL FAILURES: %d ===\n", fails);
    return fails;
}
