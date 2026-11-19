#!/bin/sh
# install_2026pld.sh -- 板上一次性安装：清掉旧程序 + 装新固件/服务端/程序包
#
# 用法（板子上，root）：
#   sh /data/install_2026pld.sh /tmp/ai_sys_pld2026.bin /tmp/tcp_image_server_pld2026 /tmp/programs
#
# 前置：/tmp 下已经放好 ①新比特流 ②新服务端 ③程序包目录（*.upk）
# 不动：厂商 Ubuntu 系统、内核、plbram/plbdma 内核模块、boot.bin
set -e

BIN=${1:?bitstream}
SRV=${2:?server binary}
PROGS=${3:?programs dir}

echo "== 1. 停服务"
systemctl stop tcp-image-server 2>/dev/null || true
systemctl disable tcp-image-server 2>/dev/null || true
pkill -f tcp_image_server 2>/dev/null || true
sleep 2

echo "== 2. 删除板上旧程序（按要求不备份）"
rm -rf /data/b112 /data/pld20261113 /data/pld2026 \
       /data/ws2026 /data/board_station /data/nv_acceptance \
       /data/backup_20261118 /data/install_2026pld.sh
rm -rf /root/tcp_image_rx /root/Fz3b_BoardCheck /root/Fz3b_BoardCheck_r1
sync
df -h /data | tail -1

echo "== 3. 装固件"
install -m 0644 "$BIN" /lib/firmware/ai_sys.bin
sha256sum /lib/firmware/ai_sys.bin | awk '{print $1"  /lib/firmware/ai_sys.bin"}' \
    > /lib/firmware/ai_sys_pp.sha256
rm -f /lib/firmware/ai_sys_vis.sha256          # 这一版没有 vis_layer，绝不能列进去
cat /lib/firmware/ai_sys_pp.sha256

echo "== 4. 装服务端 + 程序包"
mkdir -p /data/pld2026/programs
install -m 0755 "$SRV" /data/pld2026/tcp_image_server
cp -f "$PROGS"/*.upk /data/pld2026/programs/
ls -la /data/pld2026 /data/pld2026/programs

echo "== 5. 改写 systemd"
cat > /etc/systemd/system/tcp-image-server.service <<'EOF'
[Unit]
Description=Cardiac image + U-Net server (2026pld: f150nv+%3 去除+扇形寄存器)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/data/pld2026
ExecStart=/data/pld2026/tcp_image_server -p 5060 -a 0x40000000 --no-dma --echo --programs /data/pld2026/programs -n 0
Restart=always
RestartSec=2
Nice=-20
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable tcp-image-server

echo "== 6. 重启（PL 由 ai-sys-bitstream.service 开机加载）"
sync
echo "INSTALL_STAGED_OK"
