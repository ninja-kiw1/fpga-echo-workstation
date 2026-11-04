#!/bin/sh
# 切换板端 AI 权重：skin = ISIC 皮肤镜病灶，fundus = STARE 眼底血管（开机默认）。
# 比特流不变；两个 unit 互相 Conflicts，start 一个会自动停掉另一个（约 1–2 s）。
case "$1" in
    skin)   systemctl start tcp-image-server-skin.service ;;
    fundus) systemctl start tcp-image-server.service ;;
    *)      echo "usage: $0 skin|fundus"; exit 2 ;;
esac
sleep 1
for u in tcp-image-server tcp-image-server-skin; do
    printf '%-24s %s\n' "$u" "$(systemctl is-active $u.service)"
done
