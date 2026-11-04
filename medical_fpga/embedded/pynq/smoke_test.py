"""Step-by-step bring-up check; run this before run_board_test.py:

    sudo python3 smoke_test.py          # on the board
    python smoke_test.py --sim          # PC, emulated IPs

Stops at the first failing step, so the failing step says which layer is broken:
  1 package files intact (SHA-256)     -> copy problem
  2 overlay loads, IPs at expected addresses, PL clock
  3 both IPs idle                      -> AXI-Lite control path
  4 invalid config returns 1           -> ap_start / ap_done / return register, no DDR access
  5 tiny conv through DDR (HP0)        -> m_axi reads/writes, physical addresses, caches
  6 tiny Add through DDR (HP1)
"""
import argparse
import hashlib
import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from unet_fpga import AP_CTRL, AP_RETURN, CONV_REGS, ELT_REGS, IP_NAMES, LANES, PynqDevice  # noqa: E402

EXPECTED_BASE = {'conv_layer_0': 0x80000000, 'eltwise_layer_0': 0x80010000}


def round_shift(v, shift):
    v = np.asarray(v, dtype=np.int64)
    if shift == 0:
        return v
    mag = np.abs(v)
    q, rem, half = mag >> shift, mag & ((1 << shift) - 1), 1 << (shift - 1)
    q = q + ((rem > half) | ((rem == half) & (q & 1 == 1)))
    return np.where(v < 0, -q, q)


class Step(object):
    number = 0

    def __init__(self, title):
        Step.number += 1
        self.title = title

    def __enter__(self):
        print('[%d] %s ...' % (Step.number, self.title), flush=True)
        return self

    def __exit__(self, kind, value, tb):
        if kind is None:
            print('    PASS', flush=True)
            return False
        print('    FAIL: %s: %s' % (kind.__name__, value), flush=True)
        print('Stopped at step %d. See the troubleshooting table in README.md.' % Step.number)
        sys.exit(1)


def write(ip, offset, value):
    ip.write(offset, int(value) & 0xFFFFFFFF)


def write_addr(ip, offset, buf):
    write(ip, offset, int(buf.physical_address))
    write(ip, offset + 4, int(buf.physical_address) >> 32)


def start_wait(ip, timeout=2.0):
    write(ip, AP_CTRL, 1)
    t = time.perf_counter()
    while not ip.read(AP_CTRL) & 0x2:
        if time.perf_counter() - t > timeout:
            raise TimeoutError('ap_done not seen within %.1f s (AP_CTRL=0x%x)' % (timeout, ip.read(AP_CTRL)))
    return ip.read(AP_RETURN)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--package', default=HERE)
    parser.add_argument('--sim', action='store_true')
    args = parser.parse_args()
    pkg = os.path.abspath(args.package)
    with open(os.path.join(pkg, 'manifest.json')) as f:
        manifest = json.load(f)
    rng = np.random.RandomState(7)

    with Step('package files intact'):
        bad = []
        for rel, digest in manifest['files_sha256'].items():
            path = os.path.join(pkg, rel)
            with open(path, 'rb') as f:
                if hashlib.sha256(f.read()).hexdigest() != digest:
                    bad.append(rel)
        if bad:
            raise ValueError('%d files differ, e.g. %s' % (len(bad), bad[:3]))
        print('    %d files checked' % len(manifest['files_sha256']))

    with Step('load overlay and find the IPs'):
        if args.sim:
            from sim_device import SimDevice
            dev = SimDevice()
        else:
            dev = PynqDevice(os.path.join(pkg, manifest['bitstream']))
            for name in IP_NAMES:
                addr = dev.overlay.ip_dict[name]['phys_addr']
                print('    %s at 0x%x' % (name, addr))
                if addr != EXPECTED_BASE[name]:
                    raise ValueError('%s expected at 0x%x' % (name, EXPECTED_BASE[name]))
            try:
                from pynq import Clocks
                print('    PL clock 0: %.2f MHz' % Clocks.fclk0_mhz)
            except Exception as e:  # informative only
                print('    (could not read PL clock: %s)' % e)
        conv, elt = dev.ip(IP_NAMES[0]), dev.ip(IP_NAMES[1])

    with Step('both IPs report idle'):
        for name, ip in ((IP_NAMES[0], conv), (IP_NAMES[1], elt)):
            ctrl = ip.read(AP_CTRL)
            print('    %s AP_CTRL=0x%x' % (name, ctrl))
            if not ctrl & 0x4:
                raise ValueError('%s is not idle' % name)

    with Step('invalid configuration is rejected (no DDR access)'):
        write(conv, CONV_REGS['cin'], 0)      # cin=0 is illegal
        write(elt, ELT_REGS['mode'], 3)       # mode=3 is illegal
        for name, ip in ((IP_NAMES[0], conv), (IP_NAMES[1], elt)):
            status = start_wait(ip)
            print('    %s returned %d' % (name, status))
            if status != 1:
                raise ValueError('%s should return 1 for an invalid configuration' % name)

    h, w = 4, 5
    with Step('tiny 1x1 conv through DDR (HP0)'):
        x = rng.randint(-120, 121, size=(h, w, LANES)).astype(np.int16)
        weight = rng.randint(-5, 6, size=(LANES, LANES)).astype(np.int8)        # [o][i]
        bias = rng.randint(-50, 51, size=LANES).astype(np.int64)
        mult = rng.randint(-3, 4, size=LANES).astype(np.int64)
        offset = rng.randint(-3, 4, size=LANES).astype(np.int64)
        shift, qmin, qmax = 1, -127, 127
        acc = x.astype(np.int64) @ weight.T.astype(np.int64) + bias
        expected = np.clip(round_shift(acc * mult + offset, shift), qmin, qmax)
        words = np.concatenate([weight.view(np.uint64).reshape(-1)] +
                               [a.view(np.uint64) for a in (bias, mult, offset)])
        bx, by, bp = dev.allocate(x.shape, np.int16), dev.allocate(x.shape, np.int16), dev.allocate(words.shape, np.uint64)
        bx[:] = x
        by[:] = 12345
        bp[:] = words
        for b in (bx, by, bp):
            b.flush()
        write_addr(conv, CONV_REGS['in_'], bx)
        write_addr(conv, CONV_REGS['out'], by)
        write_addr(conv, CONV_REGS['params'], bp)
        for key, value in (('mode', 1), ('cin', LANES), ('cout', LANES), ('in_h', h), ('in_w', w),
                           ('relu', 0), ('shift', shift), ('qmin', qmin), ('qmax', qmax)):
            write(conv, CONV_REGS[key], value)
        status = start_wait(conv)
        by.invalidate()
        got = np.array(by, dtype=np.int64)
        bad = int((got != expected).sum())
        print('    return=%d, %d of %d values differ' % (status, bad, expected.size))
        if status != 0 or bad:
            raise ValueError('conv result wrong; first rows got %s expected %s' % (got[0, 0], expected[0, 0]))

    with Step('tiny Add through DDR (HP1)'):
        a = rng.randint(-127, 128, size=(h, w, LANES)).astype(np.int16)
        b = rng.randint(-127, 128, size=(h, w, LANES)).astype(np.int16)
        expected = np.clip(a.astype(np.int64) + 2 * b.astype(np.int64), -127, 127)
        ba, bb, bo = (dev.allocate(a.shape, np.int16) for _ in range(3))
        ba[:], bb[:], bo[:] = a, b, 12345
        for buf in (ba, bb, bo):
            buf.flush()
        write_addr(elt, ELT_REGS['in_a'], ba)
        write_addr(elt, ELT_REGS['in_b'], bb)
        write_addr(elt, ELT_REGS['out'], bo)
        for key, value in (('mode', 0), ('ca', LANES), ('cb', LANES), ('h', h), ('w', w), ('m0', 1),
                           ('m1', 2), ('shift', 0), ('qmin', -127), ('qmax', 127)):
            write(elt, ELT_REGS[key], value)
        status = start_wait(elt)
        bo.invalidate()
        bad = int((np.array(bo, dtype=np.int64) != expected).sum())
        print('    return=%d, %d of %d values differ' % (status, bad, expected.size))
        if status != 0 or bad:
            raise ValueError('Add result wrong')

    print('\nSMOKE TEST PASS - next: sudo python3 run_board_test.py')
    return 0


if __name__ == '__main__':
    sys.exit(main())
