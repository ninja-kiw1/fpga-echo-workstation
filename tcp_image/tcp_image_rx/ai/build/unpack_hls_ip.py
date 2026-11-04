"""Unpack the exported HLS IP archives (solution1/impl/ip/xilinx_com_hls_*.zip) next to themselves.

    python unpack_hls_ip.py [root]      # default root: the repository / project root (four levels up)

The public repository keeps only the IP archives (the unpacked IP has tool-generated mixed-case file names);
the Vivado block-design scripts (ai/bd/build_ai_sys_f_*.tcl) read the unpacked component.xml, so run this once
before building. Folders that already hold a component.xml are left alone.
"""
import glob
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else os.path.normpath(os.path.join(HERE, '..', '..', '..', '..'))
    archives = glob.glob(os.path.join(root, '**', 'solution1', 'impl', 'ip', 'xilinx_com_hls_*.zip'), recursive=True)
    for path in sorted(archives):
        dst = os.path.dirname(path)
        if os.path.exists(os.path.join(dst, 'component.xml')):
            print('skip  ', os.path.relpath(path, root))
            continue
        with zipfile.ZipFile(path) as z:
            z.extractall(dst)
        print('unpack', os.path.relpath(path, root))
    print('%d archives' % len(archives))
    return 0


if __name__ == '__main__':
    sys.exit(main())
