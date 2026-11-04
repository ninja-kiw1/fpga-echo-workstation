#!/bin/sh
# host DLL of the C kernel for track_val.py (msys2 mingw64 gcc)
cd "$(dirname "$0")" && gcc -O3 -shared -std=c99 -o bm_sad.dll bm_sad.c
