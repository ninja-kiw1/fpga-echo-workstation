/**
 * @file  crc16.h
 * @brief CRC-16/CCITT-FALSE 实现(与 PC 端 Python 脚本保持完全一致)。
 *
 *  参数模型(CRC 目录里的标准命名 CCITT-FALSE):
 *      多项式 poly   = 0x1021 (x^16 + x^12 + x^5 + 1)
 *      初值   init   = 0xFFFF
 *      输入反转 refin = false
 *      输出反转 refout= false
 *      异或输出 xorout= 0x0000
 *
 *  标准校验值: 对 ASCII 字符串 "123456789" 计算, 结果应为 0x29B1。
 *  程序启动自检和 Python 脚本的 --selftest 都用这条向量对齐实现。
 */
#ifndef CRC16_H
#define CRC16_H

#include <stdint.h>
#include <stddef.h>

/** @brief 计算 CRC16 的初值(CCITT-FALSE 模型)。 */
#define CRC16_CCITT_FALSE_INIT  0xFFFFu

/**
 * @brief 预先构建查找表（256 项，约 1 µs）。
 *
 *  不调也行 —— 首次调用 update 时会自动构建。单线程程序无需加锁；
 *  多线程的话在启动时先调一次最省事。
 */
void crc16_init(void);

/**
 * @brief 在已有 CRC 状态上继续累加数据(用于分段/逐行累加)。
 *
 * @param crc  上一次的 CRC 值, 首次调用传 CRC16_CCITT_FALSE_INIT。
 * @param data 数据指针。
 * @param len  数据长度(字节)。
 * @return     更新后的 CRC 值。
 */
uint16_t crc16_ccitt_false_update(uint16_t crc, const uint8_t *data, size_t len);

/**
 * @brief 一次性计算一块数据的 CRC16(等价于 update(INIT, data, len))。
 */
uint16_t crc16_ccitt_false(const uint8_t *data, size_t len);

#endif /* CRC16_H */
