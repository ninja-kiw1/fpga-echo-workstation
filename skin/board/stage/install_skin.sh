#!/bin/sh
# 在 FZ3B 板上运行（未上板验证过）。前提：本目录 = 上传后的 board/stage/ + 本脚本 + skin_mode.sh。
#
#   1. 用与眼底完全相同的源码 + 皮肤参数，原生编译出 tcp_image_server_skin；
#   2. 复制眼底服务的 unit，只改 ExecStart（换程序、追加 --ai-res 128）并加 Conflicts，
#      生成 tcp-image-server-skin.service —— 两个服务互斥，同一时刻只有一个占端口和 PL；
#   3. 不 enable：开机默认仍是眼底服务。切换用 ./skin_mode.sh skin | fundus。
#
# 不动：/root/tcp_image_rx/ 下的眼底程序、tcp-image-server.service、比特流、内核模块。
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
RES=${1:-128}
FUNDUS_UNIT=/etc/systemd/system/tcp-image-server.service
SKIN_UNIT=/etc/systemd/system/tcp-image-server-skin.service

cd "$HERE"
make clean >/dev/null 2>&1 || true
make CC=gcc TARGET=tcp_image_server_skin
install -d /root/skin
install -m 755 tcp_image_server_skin /root/skin/tcp_image_server_skin

[ -f "$FUNDUS_UNIT" ] || { echo "missing $FUNDUS_UNIT"; exit 1; }
sed -e "s#^ExecStart=[^ ]*\(.*\)#ExecStart=/root/skin/tcp_image_server_skin\1 --ai-res $RES#" \
    -e 's#^Description=.*#Description=tcp_image server with ISIC skin-lesion weights (same bitstream)#' \
    -e '/^\[Unit\]/a Conflicts=tcp-image-server.service' \
    "$FUNDUS_UNIT" > "$SKIN_UNIT"
systemctl daemon-reload
echo "== installed. ExecStart:"
grep '^ExecStart=' "$SKIN_UNIT"
echo "== switch with: $HERE/skin_mode.sh skin   (back: $HERE/skin_mode.sh fundus)"
