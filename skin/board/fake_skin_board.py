"""Local stand-in for the skin board server, for dry runs of verify_board_skin.py.

Speaks the real v4 wire protocol (12-byte header + 512 CRC row packets in;
64-byte stats block + mask / logit images out, same order and encodings as
tcp_image_server.c / ai_view.c) and computes logits with the HLS bit-level
simulator on the deployed layer table. It proves the plumbing, not the board.

    python board/fake_skin_board.py [port]      # default 5061, Ctrl+C to stop
"""
import socket
import struct
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from common import PLD, ROOT  # noqa: E402
from export_verify import SizedRunner  # noqa: E402

sys.path.insert(0, str(PLD / 'tcp_image' / 'tcp_image_ui'))
import proto  # noqa: E402

DEFAULT_SHIFT = 2          # tcp_image_server_skin --ai-res 128


def recv_exact(c, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = c.recv(n - len(buf))
        if not chunk:
            raise ConnectionError('closed')
        buf += chunk
    return bytes(buf)


def serve(c, runner):
    while True:
        hdr = recv_exact(c, proto.CTRL_BYTES_V4)
        _magic, _mode, flags, aiparam, _thr, _r = struct.unpack('<HHHHHH', hdr)
        img = bytearray(proto.IMG_TOTAL_BYTES)
        for _ in range(proto.PKT_COUNT):
            row, length, crc = proto.parse_header(recv_exact(c, proto.PKT_HDR_BYTES))
            payload = recv_exact(c, length)
            assert crc == proto.crc16_ccitt_false(payload), 'row CRC'
            img[row * proto.IMG_ROW_BYTES:(row + 1) * proto.IMG_ROW_BYTES] = payload
        shift = (aiparam & proto.AIPARAM_RES_MASK) >> proto.AIPARAM_RES_SHIFT or DEFAULT_SHIFT
        thr = struct.unpack('b', bytes([(aiparam >> 8) & 0xFF]))[0]
        k = 1 << shift
        rgb = np.frombuffer(bytes(img), np.uint8).reshape(512, 512, 3)
        logits = runner.run(np.ascontiguousarray(rgb[::k, ::k])).astype(np.int64)
        up = np.repeat(np.repeat(logits, k, 0), k, 1)
        out = b''
        if flags & proto.FLAG_STATS:
            lesion = int((logits >= thr).sum())
            out += struct.pack('<16I', proto.AI_STAT_MAGIC, 4, flags, 512 >> shift, lesion, logits.size,
                               lesion * 1000 // logits.size, int(logits.min()) & 0xFFFFFFFF,
                               int(logits.max()) & 0xFFFFFFFF, 12345, 0, 0, 0, 0, 1, 0)
        if flags & proto.FLAG_ECHO_MASK:
            m = np.where(up >= thr, 255, 0).astype(np.uint8)
            out += b''.join(proto.iter_packets(np.repeat(m[:, :, None], 3, 2).tobytes()))
        if flags & proto.FLAG_ECHO_LOGIT:
            v = (up + proto.LOGIT_IMG_OFFSET).astype(np.uint8)
            out += b''.join(proto.iter_packets(np.repeat(v[:, :, None], 3, 2).tobytes()))
        c.sendall(out)


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5061
    runner = SizedRunner(ROOT / 'deploy' / 'r128')
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('127.0.0.1', port))
    srv.listen(1)
    print(f'fake skin board on 127.0.0.1:{port}', flush=True)
    try:
        while True:
            c, _ = srv.accept()
            try:
                serve(c, runner)
            except ConnectionError:
                pass
            finally:
                c.close()
    finally:
        runner.close()


if __name__ == '__main__':
    main()
