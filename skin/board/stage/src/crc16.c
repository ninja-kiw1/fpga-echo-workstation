/**
 * @file  crc16.c
 * @brief CRC-16/CCITT-FALSE，**查表实现**。
 *
 *  算法参数（和原来逐位版本完全一致，结果逐位相同）：
 *      poly=0x1021, init=0xFFFF, 不反转, 不异或输出。
 *
 *  为什么改成查表：原来的逐位实现在 ZU3EG 的 A53（1.07 GHz）上算 786432 字节
 *  要 ~15 ms，而程序每收一行、每发一行都要各算一次 —— CRC 期间进程不读 socket，
 *  TCP 窗口被关小，**收发两个方向都被它拖慢**（实测收一帧 ~90 ms）。
 *  查表版每字节只做一次查表 + 移位，约快 8 倍。
 *
 *  表在首次使用时构建（256 项，约 1 µs）。本程序是单线程的，无需加锁；
 *  若将来多线程调用，请在启动时先调一次 crc16_init()。
 */
#include "crc16.h"

static uint16_t crc16_table[256];
static int      crc16_table_ready = 0;

/** @brief 构建 256 项查找表（幂等）。 */
void crc16_init(void)
{
    uint16_t i;
    int      j;

    if (crc16_table_ready != 0) {
        return;
    }
    for (i = 0u; i < 256u; i++) {
        uint16_t crc = (uint16_t)(i << 8);
        for (j = 0; j < 8; j++) {
            if ((crc & 0x8000u) != 0u) {
                crc = (uint16_t)((crc << 1) ^ 0x1021u);
            } else {
                crc = (uint16_t)(crc << 1);
            }
        }
        crc16_table[i] = crc;
    }
    crc16_table_ready = 1;
}

uint16_t crc16_ccitt_false_update(uint16_t crc, const uint8_t *data, size_t len)
{
    size_t i;

    if (crc16_table_ready == 0) {
        crc16_init();
    }

    for (i = 0u; i < len; i++) {
        /* 高字节与数据异或后查表, 低字节左移 8 位再异或 —— 等价于原来的 8 次逐位 */
        crc = (uint16_t)((crc << 8) ^
                         crc16_table[(((crc >> 8) ^ (uint16_t)data[i]) & 0xFFu)]);
    }

    return crc;
}

uint16_t crc16_ccitt_false(const uint8_t *data, size_t len)
{
    return crc16_ccitt_false_update(CRC16_CCITT_FALSE_INIT, data, len);
}
