"""Run the U-Net accelerator on the FZ3B over JTAG only (no OS on the board).

Board: SW1 all ON (JTAG boot mode), power-cycled, JTAG cable connected.

    & 'C:/Users/21888/anaconda3/envs/srtp-vision/python.exe' jtag_verify.py
    ... --image im0324 --dump final           # another image, final output only
    ... --dump all                            # read back all 30 nodes (slow over JTAG)

XSCT does what the ARM program would do: program the PL, run psu_init (clocks,
DDR, PS-PL isolation), load parameters and the packed input into DDR, start the
30 nodes of network.txt one by one through the AXI-Lite registers, then read
chosen node buffers back. This script compares them byte-for-byte with the HLS
reference vectors (deployment/unet_network/vectors).

Every node gets its own DDR buffer (no reuse), so any intermediate tensor can be
read back after the run. Per-node times are measured on the PC around JTAG
polling and are therefore upper bounds, not accelerator latency.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import zipfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MEDICAL = os.path.normpath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(MEDICAL, 'embedded', 'pynq'))
from unet_fpga import CONV_REGS, ELT_REGS, load_network  # noqa: E402

def find_xsct():
    """XSCT from $XSCT, then PATH, then common Vitis 2023.1 install folders."""
    import glob
    import shutil
    if os.environ.get('XSCT'):
        return os.environ['XSCT']
    found = shutil.which('xsct.bat') or shutil.which('xsct')
    if found:
        return found
    for pattern in (r'D:\xlinx2023.1\Vitis\*\bin\xsct.bat', r'C:\Xilinx\Vitis\*\bin\xsct.bat',
                    r'D:\Xilinx\Vitis\*\bin\xsct.bat', '/tools/Xilinx/Vitis/*/bin/xsct'):
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    raise SystemExit('XSCT not found: set the XSCT environment variable to .../Vitis/2023.1/bin/xsct.bat')


XSCT = None  # resolved in main() so --dry-run works without Vitis
VECTORS = os.path.join(MEDICAL, 'deployment', 'unet_network', 'vectors')
VIVADO_OUT = os.path.join(MEDICAL, 'vivado', 'output')
CONV_BASE, ELT_BASE = 0x80000000, 0x80010000
DDR_BASE = 0x10000000            # 256 MiB; nothing else uses DDR in JTAG boot mode
DEFAULT_DUMP = ['first_block_2', 'add', 'down_path_0_pool', 'add_2', 'up_path_0_up_layer_block_0',
                'cat', 'up_path_0_conv_layer_block_2', 'add_4', 'final']


def tcl_path(path):
    return os.path.abspath(path).replace('\\', '/')


def align(n, a=0x1000):
    return (n + a - 1) // a * a


def layout(nodes):
    """Fixed DDR address for every parameter blob and every node buffer."""
    addr, params, buffers = DDR_BASE, {}, {}
    for n in nodes:
        if n.kind == 'CONV':
            size = os.path.getsize(os.path.join(VECTORS, 'params', n.name + '.bin'))
            params[n.name] = (addr, size)
            addr = align(addr + size)
    for n in nodes:
        size = int(np.prod(n.shape)) * 2
        buffers[n.name] = (addr, size)
        addr = align(addr + size)
    return params, buffers, addr - DDR_BASE


def node_tcl(n, params, buffers):
    lines = []

    def reg(base, offset, value):
        lines.append('wreg 0x%08x 0x%08x' % (base + offset, int(value) & 0xFFFFFFFF))

    def addr(base, offset, a):
        reg(base, offset, a & 0xFFFFFFFF)
        reg(base, offset + 4, a >> 32)

    out, a = buffers[n.name][0], buffers[n.in0][0]
    if n.kind == 'CONV':
        base, r = CONV_BASE, CONV_REGS
        addr(base, r['in_'], a)
        addr(base, r['out'], out)
        addr(base, r['params'], params[n.name][0])
        for k, v in (('mode', n.mode), ('cin', n.ca), ('cout', n.cb), ('in_h', n.h), ('in_w', n.w),
                     ('relu', n.relu), ('shift', n.shift), ('qmin', n.qmin), ('qmax', n.qmax)):
            reg(base, r[k], v)
    else:
        base, r = ELT_BASE, ELT_REGS
        b = buffers[n.in1][0] if n.in1 != '-' else a
        addr(base, r['in_a'], a)
        addr(base, r['in_b'], b)
        addr(base, r['out'], out)
        for k, v in (('mode', n.mode), ('ca', n.ca), ('cb', n.cb), ('h', n.h), ('w', n.w), ('m0', n.m0),
                     ('m1', n.m1), ('shift', n.shift), ('qmin', n.qmin), ('qmax', n.qmax)):
            reg(base, r[k], v)
    lines.append('run_ip 0x%08x %s' % (base, n.name))
    return lines


TCL_PRELUDE = r'''
# The JTAG link shows occasional transfer errors (DAP ACK / corrupted words),
# so every access is retried and every transfer is verified.
proc step {msg} { puts "STEP $msg"; flush stdout }
proc retry {script {tries 5}} {
    for {set i 1} {$i <= $tries} {incr i} {
        if {![catch {uplevel 1 $script} result]} { return $result }
        puts "JTAGRETRY $i: $result"; flush stdout
        after [expr {500 * $i}]
        # A corrupted scan can drop the debug context ("Invalid context"):
        # reconnect and select the memory target again.
        catch {disconnect}
        catch {open_link}
    }
    error "JTAG access failed after $tries tries: $script"
}
proc rd {addr} { return [retry [list mrd -value $addr]] }
proc wreg {addr value} {
    # Write, read back, compare; the AXI-Lite argument registers are readable.
    for {set i 0} {$i < 5} {incr i} {
        retry [list mwr $addr $value]
        if {[rd $addr] == $value} { return }
    }
    error "register write failed at $addr"
}
proc read_bytes {path} { set f [open $path rb]; set d [read $f]; close $f; return $d }
proc run_ip {base name} {
    retry [list mwr $base 1]
    set t0 [clock microseconds]
    while {([rd $base] & 2) == 0} {
        if {[clock microseconds] - $t0 > 30000000} { error "TIMEOUT $name ctrl=[format 0x%%x [rd $base]]" }
    }
    set us [expr {[clock microseconds] - $t0}]
    set ret [rd [expr {$base + 0x10}]]
    puts "NODE $name $us $ret"; flush stdout
    if {$ret != 0} { error "BADRETURN $name $ret" }
}
set ::chunk_words 16384   ;# 64 KiB per verified read
proc stable_read {addr words} {
    # Read in 64 KiB chunks; a chunk is accepted when two consecutive reads agree.
    # Transfer errors are sparse and random, so a small chunk quickly reads clean twice.
    set tmp [file join $::workdir chunk.tmp]
    set data ""
    set rereads 0
    for {set done 0} {$done < $words} {incr done $::chunk_words} {
        set n [expr {min($::chunk_words, $words - $done)}]
        set a [expr {$addr + 4 * $done}]
        retry [list mrd -bin -file $tmp $a $n]
        set prev [read_bytes $tmp]
        set ok 0
        for {set i 0} {$i < 10} {incr i} {
            retry [list mrd -bin -file $tmp $a $n]
            set cur [read_bytes $tmp]
            if {$cur eq $prev} { set ok 1; break }
            incr rereads
            set prev $cur
        }
        if {!$ok} { error "chunk at [format 0x%%x $a] never read the same twice" }
        append data $cur
    }
    file delete $tmp
    if {$rereads} { puts "DUMPRETRY $rereads chunk re-reads at [format 0x%%x $addr]"; flush stdout }
    return $data
}
proc timed_dow {file addr} {
    # Upload, then verify with stable reads; re-upload on a real mismatch.
    set want [read_bytes $file]
    set words [expr {([string length $want] + 3) / 4}]
    set t0 [clock milliseconds]
    for {set i 1} {$i <= 4} {incr i} {
        retry [list dow -data $file $addr]
        set got [string range [stable_read $addr $words] 0 [expr {[string length $want] - 1}]]
        if {$got eq $want} {
            puts "LOAD [file tail $file] [file size $file] [expr {[clock milliseconds] - $t0}] attempts=$i"; flush stdout
            return
        }
        puts "LOADRETRY [file tail $file] attempt $i"; flush stdout
    }
    error "upload of $file failed verification"
}
proc timed_dump {file addr words} {
    set t0 [clock milliseconds]
    set data [stable_read $addr $words]
    set f [open $file wb]; puts -nonewline $f $data; close $f
    puts "DUMP [file tail $file] [expr {$words * 4}] [expr {[clock milliseconds] - $t0}]"; flush stdout
}

set ::workdir {%(workdir)s}
set ::jtag_hz %(jtag_hz)d
proc open_link {} {
    connect
    jtag targets -set -filter {level == 0}
    jtag frequency $::jtag_hz
    # Memory and register accesses go through the APU memory access port (as
    # psu_init does); the A53 cores stay in reset. Going through core #0's debug
    # interface dropped the debug context ("EDITR not ready") in long read-backs.
    targets -set -nocase -filter {name =~ "*APU*"}
    configparams force-mem-accesses 1
}
open_link
step "connected: [lindex [split [jtag targets] \n] 0], JTAG [jtag frequency] Hz"
'''

TCL_INIT = r'''
targets -set -nocase -filter {name =~ "*PSU*"}
rst -system
after 3000
targets -set -nocase -filter {name =~ "PL"}
fpga -file {%(bit)s}
step "bitstream programmed"
targets -set -nocase -filter {name =~ "*APU*"}
source {%(psu_init)s}
psu_init
after 1000
psu_ps_pl_isolation_removal
after 1000
psu_ps_pl_reset_config
catch {psu_protection}
step "psu_init done (clocks, DDR, PS-PL isolation removed)"
targets -set -nocase -filter {name =~ "*APU*"}
configparams force-mem-accesses 1

# DDR sanity check at both ends of the region used.
foreach a {0x%(ddr_lo)08x 0x%(ddr_hi)08x} {
    wreg $a 0x5A5AA5A5
}
step "DDR read/write ok"
foreach {name base} {conv_layer_0 0x%(conv)08x eltwise_layer_0 0x%(elt)08x} {
    set ctrl [rd $base]
    puts "IP $name ctrl=[format 0x%%x $ctrl]"
    if {($ctrl & 4) == 0} { error "$name not idle" }
}
step "both IPs idle"
'''


def build_tcl(nodes, params, buffers, image, dumps, workdir, bitfile, psu_init, total, jtag_hz,
              readback_only=False):
    values = dict(bit=tcl_path(bitfile), psu_init=tcl_path(psu_init), ddr_lo=DDR_BASE,
                  workdir=tcl_path(workdir), jtag_hz=jtag_hz,
                  ddr_hi=DDR_BASE + total - 4, conv=CONV_BASE, elt=ELT_BASE)
    lines = [TCL_PRELUDE % values]
    if readback_only:
        # The board still holds the tensors of the previous run: no reset, no reload.
        lines.append('step "read-back only (no reset, no reprogramming)"')
        dump_lines(lines, dumps, buffers, workdir)
        return '\n'.join(lines) + '\n'
    lines.append(TCL_INIT % values)
    for name, (a, _) in params.items():
        lines.append('timed_dow {%s} 0x%08x' % (tcl_path(os.path.join(VECTORS, 'params', name + '.bin')), a))
    x = nodes[0]
    lines.append('timed_dow {%s} 0x%08x' % (tcl_path(os.path.join(VECTORS, 'images', image, 'input.bin')),
                                            buffers[x.name][0]))
    lines.append('step "parameters and input loaded"')
    lines.append('set t_net [clock microseconds]')
    for n in nodes[1:]:
        lines.extend(node_tcl(n, params, buffers))
    lines.append('puts "NETWORK_US [expr {[clock microseconds] - $t_net}]"')
    dump_lines(lines, dumps, buffers, workdir)
    return '\n'.join(lines) + '\n'


def dump_lines(lines, dumps, buffers, workdir):
    for name in dumps:
        a, size = buffers[name]
        lines.append('timed_dump {%s} 0x%08x %d' % (tcl_path(os.path.join(workdir, name + '.bin')), a, size // 4))
    lines.append('step "finished"')
    lines.append('disconnect')


def run_xsct(tcl_file, log_path, workdir, st):
    """Run one XSCT script, echo the interesting lines, collect results into st."""
    with open(log_path, 'a') as log:
        log.write('\n##### %s\n' % os.path.basename(tcl_file))
        proc = subprocess.Popen([XSCT, tcl_file], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                cwd=workdir, universal_newlines=True, errors='replace')
        for line in proc.stdout:
            log.write(line)
            line = line.rstrip()
            m = re.match(r'NODE (\S+) (\d+) (\d+)', line)
            if m:
                st['node_us'][m.group(1)] = int(m.group(2))
            elif line.startswith('LOAD '):
                st['loads'].append(line.split()[1:])
            elif line.startswith('DUMP '):
                st['dumps'].append(line.split()[1:])
            elif line.startswith('NETWORK_US '):
                st['network_us'] = int(line.split()[1])
            elif line.startswith('STEP '):
                st['steps'].append(line[5:])
            elif line.startswith(('JTAGRETRY', 'LOADRETRY', 'DUMPRETRY')):
                st['retries'].append(line)
            if line.strip() and not line.startswith(('INFO:', '  ', '\t', 'NODE ', '*')) and \
                    'hw_server' not in line or 'error' in line.lower():
                print(line, flush=True)
        proc.wait()
    st['returncodes'].append(proc.returncode)
    return proc.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--image', default='im0002')
    parser.add_argument('--dump', nargs='*', help='node names, "final", or "all" (default: 9 key nodes)')
    parser.add_argument('--jtag-hz', type=int, default=5000000,
                        help='JTAG clock; 10-15 MHz showed frequent transfer errors on this cable/board')
    parser.add_argument('--readback-only', action='store_true',
                        help='only read node buffers left in DDR by the previous run (same image!)')
    parser.add_argument('--run-retries', type=int, default=3,
                        help='full restarts (from reset) if the JTAG link fails before the network finishes')
    parser.add_argument('--readback-retries', type=int, default=4,
                        help='fresh XSCT processes to finish read-backs after a JTAG failure')
    parser.add_argument('--dry-run', action='store_true', help='write the XSCT script only')
    args = parser.parse_args()

    nodes = load_network(os.path.join(VECTORS, 'network.txt'))
    names = [n.name for n in nodes]
    if not args.dump:
        dumps = DEFAULT_DUMP
    elif args.dump == ['all']:
        dumps = names
    else:
        dumps = args.dump
    unknown = [d for d in dumps if d not in names]
    if unknown:
        parser.error('unknown nodes: %s' % unknown)
    golden_dir = os.path.join(VECTORS, 'images', args.image, 'golden')
    if not os.path.isdir(golden_dir) and dumps != [names[-1]]:
        print('No per-node golden tensors for %s: only the final output will be dumped.' % args.image)
        dumps = [names[-1]]

    stamp = time.strftime('%Y%m%d_%H%M%S')
    workdir = os.path.join(HERE, 'results', '%s_%s' % (stamp, args.image))
    os.makedirs(workdir)
    xsa_dir = os.path.join(workdir, 'xsa')
    with zipfile.ZipFile(os.path.join(VIVADO_OUT, 'unet_system.xsa')) as z:
        z.extract('psu_init.tcl', xsa_dir)
        z.extract('unet_system.bit', xsa_dir)
    params, buffers, total = layout(nodes)
    tcl = build_tcl(nodes, params, buffers, args.image, dumps, workdir,
                    os.path.join(xsa_dir, 'unet_system.bit'), os.path.join(xsa_dir, 'psu_init.tcl'), total,
                    args.jtag_hz, args.readback_only)
    tcl_file = os.path.join(workdir, 'run.tcl')
    with open(tcl_file, 'w') as f:
        f.write(tcl)
    print('DDR use %.1f MiB from 0x%08x; script %s' % (total / 2**20, DDR_BASE, tcl_file))
    if args.dry_run:
        return 0
    global XSCT
    XSCT = find_xsct()
    print('XSCT: %s' % XSCT)

    log_path = os.path.join(workdir, 'xsct.log')
    st = dict(node_us={}, loads=[], dumps=[], steps=[], network_us=None, retries=[], returncodes=[])
    t0 = time.time()
    run_xsct(tcl_file, log_path, workdir, st)
    network_done = args.readback_only or st['network_us'] is not None
    # If the link failed before the network finished, start over from reset in a
    # fresh XSCT process (uploads are verified, so a restart is always safe).
    for attempt in range(1, args.run_retries + 1):
        if network_done:
            break
        print('\nRestarting the whole run in a fresh XSCT process, attempt %d' % attempt, flush=True)
        run_xsct(tcl_file, log_path, workdir, st)
        network_done = st['network_us'] is not None
    # A corrupted JTAG scan can leave XSCT without a usable debug context; a fresh
    # XSCT process recovers. The tensors stay in DDR, so only missing reads are redone.
    for attempt in range(1, args.readback_retries + 1):
        missing = [n for n in dumps if not os.path.exists(os.path.join(workdir, n + '.bin'))]
        if not missing or not network_done:
            break
        print('\nRelaunching XSCT to read back %d missing node(s), attempt %d' % (len(missing), attempt), flush=True)
        retry_tcl = os.path.join(workdir, 'readback_%d.tcl' % attempt)
        with open(retry_tcl, 'w') as f:
            f.write(build_tcl(nodes, params, buffers, args.image, missing, workdir, '', '', total,
                              args.jtag_hz, readback_only=True))
        run_xsct(retry_tcl, log_path, workdir, st)
    elapsed = time.time() - t0
    node_us, steps, retries = st['node_us'], st['steps'], st['retries']

    rows = []
    for name in dumps:
        path = os.path.join(workdir, name + '.bin')
        if not os.path.exists(path):
            rows.append(dict(node=name, status='not dumped'))
            continue
        actual = np.fromfile(path, dtype='<i2')
        ref_path = os.path.join(golden_dir, name + '.bin') if os.path.isdir(golden_dir) else \
            os.path.join(VECTORS, 'images', args.image, 'expected_final.bin')
        expected = np.fromfile(ref_path, dtype='<i2')
        bad = int((actual != expected).sum()) if actual.size == expected.size else -1
        rows.append(dict(node=name, values=int(expected.size), mismatches=bad))
    ok = network_done and all(r.get('mismatches') == 0 for r in rows)

    print('\n== Comparison with HLS reference (%s)' % args.image)
    for r in rows:
        status = 'PASS' if r.get('mismatches') == 0 else 'FAIL'
        print('%s %-40s %s' % (status, r['node'], 'mismatches=%s' % r.get('mismatches', r.get('status'))))
    if node_us:
        print('\nJTAG-polled node times (upper bounds): total %.1f ms over %d nodes' %
              (sum(node_us.values()) / 1e3, len(node_us)))
    result = dict(image=args.image, time=stamp, readback_only=args.readback_only,
                  xsct_returncodes=st['returncodes'], steps=steps, network_completed=network_done,
                  elapsed_s=elapsed, ddr_base=DDR_BASE, ddr_bytes=total, loads=st['loads'], dumps=st['dumps'],
                  node_us_upper_bound=node_us, network_us_upper_bound=st['network_us'], jtag_hz=args.jtag_hz,
                  jtag_retries=retries, compare=rows, passed=ok)
    with open(os.path.join(workdir, 'result.json'), 'w') as f:
        json.dump(result, f, indent=2)
    print('\n%s  (%s, %.0f s total) -> %s' % ('PASS' if ok else 'FAIL', ', '.join(steps[-1:]), elapsed, workdir))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
