r"""Part E step 3: move the strain sources into tcp_image_rx/src and register JOB_STRAIN (server v5.19).

Run only AFTER server v5.18 (part A) is deployed: until then a plain `deploy_board_v5.py prepare` builds whatever is
in src/, so the strain files wait here (E:/PLD/strain/step3/rx_src). Idempotent (a second run changes nothing).
  copies strain.c/.h, bm_sad.c/.h, job_strain.c -> src/
  src/job.h   : JOB_STRAIN = 7 in the enum, prototype job_strain_run
  src/job.c   : dispatch, "strain" in the caps job list, caps "server" -> "v5.19"
  makefile    : -ffp-contract=off for strain.o / job_strain.o, -O3 for bm_sad.o (the step-2 benchmark build)

    python apply_to_rx.py [path to tcp_image_rx]      (default E:/PLD/tcp_image/tcp_image_rx)
"""
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RX = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('E:/PLD/tcp_image/tcp_image_rx')
FILES = ('strain.c', 'strain.h', 'bm_sad.c', 'bm_sad.h', 'job_strain.c')


def read(p):
    """text with its own line endings kept (job.h is CRLF, job.c / makefile LF); anchors use \n -> converted"""
    raw = p.read_bytes().decode('utf-8')
    return raw.replace('\r\n', '\n'), '\r\n' in raw


def write(p, text, crlf):
    p.write_bytes((text.replace('\n', '\r\n') if crlf else text).encode('utf-8'))


def sub_once(text, old, new, what):
    if new in text:
        return text
    if old not in text:
        raise SystemExit(f'{what}: anchor not found: {old!r}')
    return text.replace(old, new, 1)


def main():
    for f in FILES:
        shutil.copyfile(HERE / f, RX / 'src' / f)
    p = RX / 'src' / 'job.h'
    t, crlf = read(p)
    t = sub_once(t, '       JOB_OVERLAY = 6 };', '       JOB_OVERLAY = 6, JOB_STRAIN = 7 };', 'job.h enum')
    t = sub_once(t, 'int job_overlay_run(job_t *j);\n', 'int job_overlay_run(job_t *j);\nint job_strain_run(job_t *j);\n',
                 'job.h prototype')
    t = sub_once(t, '/* jobs (job_ct.c, job_reg.c, job_echo.c, job_simpson.c, job_render.c, job_overlay.c);',
                 '/* jobs (job_ct.c, job_reg.c, job_echo.c, job_simpson.c, job_render.c, job_overlay.c, job_strain.c);',
                 'job.h comment')
    write(p, t, crlf)
    p = RX / 'src' / 'job.c'
    t, crlf = read(p)
    t = sub_once(t, '    } else if (j.kind == JOB_OVERLAY) {\n        rc = job_overlay_run(&j);\n',
                 '    } else if (j.kind == JOB_OVERLAY) {\n        rc = job_overlay_run(&j);\n'
                 '    } else if (j.kind == JOB_STRAIN) {\n        rc = job_strain_run(&j);\n', 'job.c dispatch')
    t = sub_once(t, '"\\"render3d\\",\\"overlay\\"]"', '"\\"render3d\\",\\"overlay\\",\\"strain\\"]"', 'job.c caps list')
    t, n = re.subn(r'\\"server\\":\\"v5\.\d+\\"', r'\\"server\\":\\"v5.19\\"', t)
    if n != 1:
        raise SystemExit('job.c: caps server string not found exactly once')
    write(p, t, crlf)
    p = RX / 'makefile'
    t, crlf = read(p)
    t = sub_once(t, '$(OBJDIR)/job_simpson.o: CFLAGS += -ffp-contract=off\n',
                 '$(OBJDIR)/job_simpson.o: CFLAGS += -ffp-contract=off\n'
                 '$(OBJDIR)/strain.o: CFLAGS += -ffp-contract=off\n'
                 '$(OBJDIR)/job_strain.o: CFLAGS += -ffp-contract=off\n'
                 '$(OBJDIR)/bm_sad.o: CFLAGS += -O3\n', 'makefile flags')
    write(p, t, crlf)
    print('applied to', RX)


if __name__ == '__main__':
    main()
