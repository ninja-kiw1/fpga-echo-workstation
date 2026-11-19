#!/bin/sh
# deploy_ws2026.sh —— 把暂存区里的板端上位机装到 /data/ws2026/app（带备份）。
#
#   sh deploy_ws2026.sh [暂存区根目录]      # 默认 /tmp/ws_stage
#
# 做的事：
#   1. 先停界面（有 launcher 就先杀 launcher，否则会被它重新拉起 —— 11-18 踩过）
#   2. 备份现有 app/ 四个文件到 /data/ws2026/_bak_<日期>c/app/
#   3. 覆盖 workstation.py / echo_ws.py / proto.py / echo_heart_config.json
#   4. 清 __pycache__，打印 md5
set -e

SRC=${1:-/tmp/ws_stage}
DST=/data/ws2026
BK=$DST/_bak_20261119c
FILES="workstation.py echo_ws.py proto.py echo_heart_config.json"

echo "== 1) 停界面 =="
pkill -f 'board/launch[.]sh' 2>/dev/null || true
pkill -f 'board/ws_main[.]py' 2>/dev/null || true
sleep 1
ps aux | grep -E '[w]s_main|[l]aunch.sh' || echo "   界面没在跑"

echo "== 2) 备份到 $BK =="
mkdir -p "$BK/app"
for f in $FILES; do
    [ -f "$DST/app/$f" ] && cp -a "$DST/app/$f" "$BK/app/$f"
done
ls -la "$BK/app"

echo "== 3) 覆盖 =="
for f in $FILES; do
    cp -f "$SRC/app/$f" "$DST/app/$f"
done
rm -rf "$DST/app/__pycache__" "$DST/shim/tkinter/__pycache__"

echo "== 4) md5 =="
cd "$DST/app" && md5sum $FILES
