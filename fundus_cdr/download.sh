#!/bin/sh
# Resumable download of the two licensed datasets (figshare API URLs + md5 from the API, 2026-10-17).
set -e
D=/e/PLD/fundus_cdr/data
get() { # dir name url md5
  f="$1/$2"
  until curl -L -C - --retry 20 --retry-delay 5 -sS -o "$f" "$3"; do echo "retry $2"; sleep 5; done
  m=$(md5sum "$f" | cut -d' ' -f1); [ "$m" = "$4" ] && echo "OK $2 $m" || { echo "MD5 FAIL $2 $m"; exit 1; }
}
get $D/dl_papila PAPILA.zip https://ndownloader.figshare.com/files/35013982 7d73fc5157d44b221a62ed5a4baf3779
get $D/dl_chaksu Readme_Chaksu.pdf https://ndownloader.figshare.com/files/38944805 47702fba2507bfa11f08fc6d550a7d53
get $D/dl_chaksu Train.zip https://ndownloader.figshare.com/files/37875672 116970c47a89d435523f5c371b8d74a6
get $D/dl_chaksu Test.zip https://ndownloader.figshare.com/files/37875687 9fdac2596e1e884c7a19dabce1ee7339
echo ALL_DONE
