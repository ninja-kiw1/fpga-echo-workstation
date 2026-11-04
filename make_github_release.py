"""Copy the public subset of the project into github/fpga-echo-workstation (the public GitHub repo).

    skin/.venv/Scripts/python make_github_release.py          # refresh github/fpga-echo-workstation (keeps its .git)
    skin/.venv/Scripts/python make_github_release.py --list   # only print what would be copied

(Needs pypdf to clear the report PDF's author metadata.)

Starts from make_share_package.collect() (the teammate package) and drops everything that may not or need
not be public:
- datasets and anything cut from them: arrays (.npy / .npz), videos, raw frames, sample / screenshot images,
  test-vector images and golden outputs, per-video label tables (EchoNet-Dynamic forbids redistribution;
  CAMUS / HMC-QU / CardiacUDA are non-commercial, ISIC / MSD / STARE / HRF / Chaksu are downloaded by the user);
- competition documents (文档/, .docx / .pdf) except the anonymous design report, backups, the vendored SSH
  tools, generated Vitis workspaces;
- intermediate versions: only the current firmware (f150nv) and its rollback (f150) with their HLS IP and reports,
  and only the latest board acceptance runs, moved under acceptance/.
All quantised weights (model library 0-22, including the EchoNet-trained 16 / 21 / 22) are kept.

The AMD contest guide (3.3.5.4) asks for directory and file names made of lowercase ASCII letters, digits, '_' and
'-' only: every published name is lower-cased, Chinese file names get the English names in CN_NAMES, and text files
are rewritten so that references follow the renames. Unpacked HLS IP (tool-generated mixed-case names) is replaced
by its exported archive; tcp_image_rx/ai/build/unpack_hls_ip.py restores it.
"""
import argparse
import fnmatch
import os
import re
import shutil
import sys

import make_share_package as share

ROOT = share.ROOT
OUT = os.path.join(ROOT, 'github', 'fpga-echo-workstation')
RX = 'Tcp_Image/Tcp_Image_Rx/'

EXCLUDE = [
    # competition documents, backups, duplicated review / delivery snapshots, vendored paramiko
    '文档/*', '*.docx', '*.pdf', '*.doc', '*.pptx',
    '*/_backup*', '*.bak', '*.bak_*', '*.py.v5*', 'board_station_backup_*', 'Tcp_Image/hardware_info.txt',
    'xu2dp_review_*', '交付包_*', '*/Fz3b_BoardCheck_backup_*', 'Tcp_Image/.board_tools/*', 'EchoNet-Dynamic.zip',
    RX + '&1',
    # the private tree's ignore rules would hide published files
    '.gitignore',
    # host build of the board C (rebuilt by keyframe/build_core.sh)
    'keyframe/echo_core.dll',
    # generated Vitis platform workspaces (BSP sources, libraries, copies of the bitstream)
    RX + 'vws/*', RX + 'vws_*/*',
    # dataset arrays, videos, raw frames and test-vector images
    '*.npy', '*.npz', '*.y8', '*.mp4', '*.avi', '*.mhd', '*.nii', '*.nii.gz', '*.ppm', '*.pgm', '*.rgb', '*.raw',
    '*/data/*', '*/samples/*', '*/shots/*', '*/images/*', '*/vectors/*', '*/golden/*',
    '*montage*', '*overlay*', '*sample*', 'Tcp_Image/Tcp_Image_UI/board_check_v*/*',
    'Tcp_Image/Tcp_Image_UI/reports/*',
    # per-video EchoNet EF errors (prediction - expert label) for 1101 VAL videos
    'quality/live_camus/echonet_f1.json',
    # intermediate versions: older board acceptance / check / demo runs, one-off probes, root-level logs and plans
    'board_acceptance_*', 'board_check_*', 'demo_*', 'uifix_check_*', 'board_hp0_test/*', 'board_storage_b/*',
    'vivado.log', 'retry_open_nr.log', 'dfx_runtime.txt', '分部实施计划_*.md', '待办优先级_*.md',
    # firmware history: older and diagnostic bitstreams, their HLS IP, build logs and reports
    RX + 'bitstream/*', RX + 'ai/build/*', RX + 'ai/opt_hls/build/*', RX + 'boot/orig_BOOT.BIN',
    RX + 'vivado/ai_sys_*', RX + 'vivado/hp0_probe_*', RX + 'vivado/*.dat', RX + 'vivado/vivado_pid*',
    RX + 'vivado/axil_build.log', RX + 'vivado/build.log',
]
# Checked before EXCLUDE: the versions that stay.
KEEP = [
    'board_acceptance_v526/*', 'board_check_v526/*', 'board_acceptance_nv/*',
    RX + 'bitstream/ai_sys_f150nv.*', RX + 'bitstream/ai_sys_f150.*', RX + 'bitstream/boot_design.*',
    RX + 'ai/build/f_*', RX + 'ai/build/unpack_hls_ip.py', RX + 'vivado/ai_sys_f150*',
]
# Exported HLS IP: only the archive (the unpacked folder has tool-generated mixed-case names).
HLS_IMPL = '*/solution1/impl/*'
HLS_IP_ZIP = '*/solution1/impl/ip/xilinx_com_hls_*.zip'
# Images are dropped unless they match one of these (and the rules above).
PNG_KEEP = [
    # result figures and UI screenshots checked by hand (CAMUS / MSD / ISIC / fundus, attributed in the README)
    'Tcp_Image/Tcp_Image_UI/screenshots_layout_20261012/*.png', 'quality/scan_sim/results/shot_*.png',
    'strain/step3/results/strainwin_offline.png', 'strain/step2/results/gls_val.png', 'fpga_evidence/*.png',
    'keyframe/results/*.png', 'ct/results/eval/*/val_*.png', 'echo/results/quant/*/val_panels_*.png',
    'skin/results/test/*/panels_*.png', 'hrf/results/c4/fig_*.png', 'lwnet/results/stare_fp32/overview.png',
    'medical_fpga/*/results/*.png', 'opcomp/results/*.png', 'lwnet/models/fig2.png',
]
# Published path prefixes (applied before the name rules below).
MOVE = [('board_acceptance_v526/', 'acceptance/board_acceptance_v526/'),
        ('board_acceptance_nv/', 'acceptance/board_acceptance_nv/'),
        ('board_check_v526/', 'acceptance/board_check_v526/'),
        # The repo's front page is README_github.md; the project READMEs keep their content under other names.
        ('README.md', 'project_notes_20261014.md'), ('README_成果展示.md', 'showcase_20261014.md'),
        ('README_github.md', 'README.md')]
# Published files that are not in the project tree under that name: published path -> source path.
EXTRA = {'report/design_report.pdf': '文档/设计报告_国赛模板版.pdf'}
# English names for the Chinese file names.
CN_NAMES = {
    '恢复说明.md': 'recovery_guide.md',
    'AI全分辨率条带方案.md': 'ai_full_resolution_strip_plan.md',
    'AI识别耗时分解与优化方案.md': 'ai_latency_breakdown_and_optimization.md',
    'AI识别设计.md': 'ai_inference_design.md',
    'FPGA资源利用报告.md': 'fpga_resource_utilization.md',
    'PL优化指导_队友_2026-10-09.md': 'pl_optimization_guide_2026-10-09.md',
    'PL后处理流水线_可行性_2026-10-12.md': 'pl_postprocess_pipeline_feasibility_2026-10-12.md',
    '临床测量_2026-10-13.md': 'clinical_measurements_2026-10-13.md',
    '图像处理设计.md': 'image_processing_design.md',
    '实时化_紧凑帧与板端跟踪.md': 'realtime_compact_frames_and_board_tracking.md',
    '血管形态指标.md': 'vessel_morphology_metrics.md',
    '血管形态指标_评估.json': 'vessel_morphology_metrics_eval.json',
    '血管形态指标_评估.md': 'vessel_morphology_metrics_eval.md',
    '设计说明.md': 'design_notes.md',
    '测试报告_2026-10-01.md': 'test_report_2026-10-01.md',
    '测试报告_2026-10-06_conv并行化.md': 'test_report_2026-10-06_conv_parallel.md',
    '进度与交接.md': 'progress_and_handoff.md',
    '测试记录_2026-10-08.md': 'test_log_2026-10-08.md',
    '现场验收演示清单_2026-11-01.md': 'onsite_acceptance_demo_checklist_2026-11-01.md',
    '右心程序_RV_RA_2026-10-23.md': 'right_heart_rv_ra_2026-10-23.md',
    '最终测试结果_2026-10-25.md': 'final_test_results_2026-10-25.md',
    'F2_F3_F4_结果_2026-10-25.md': 'f2_f3_f4_results_2026-10-25.md',
    'C4_ZoneB_管径_2026-10-18.md': 'c4_zone_b_vessel_calibre_2026-10-18.md',
    '关键帧仿真_2026-10-23.md': 'keyframe_simulation_2026-10-23.md',
    '板上C移植_①②③_2026-10-24.md': 'board_c_port_123_2026-10-24.md',
    '现场模式_2026-10-25.md': 'live_mode_2026-10-25.md',
    '验证结果.md': 'verification_results.md',
    'J3_结果_f150_2026-10-17.md': 'j3_results_f150_2026-10-17.md',
    'J_结果_2026-10-16.md': 'j_results_2026-10-16.md',
    'CAMUS口径现场质量灯_v5.26_2026-11-02.md': 'camus_live_quality_light_v5.26_2026-11-02.md',
    '模拟扫查_离线版_2026-10-31.md': 'scan_simulation_offline_2026-10-31.md',
    '质量引导与保形EF_2026-10-24.md': 'quality_guidance_and_conformal_ef_2026-10-24.md',
    'E第一步_vis_layer块匹配可行性_2026-10-17.md': 'e_step1_vis_layer_block_matching_feasibility_2026-10-17.md',
    'E第二步_PS块匹配与GLS原型_2026-10-17.md': 'e_step2_ps_block_matching_gls_prototype_2026-10-17.md',
    'E第三步_应变上板_主机部分_2026-10-17.md': 'e_step3_strain_on_board_host_part_2026-10-17.md',
    'E第三步_计划_2026-10-17.md': 'e_step3_plan_2026-10-17.md',
    '节段室壁运动_HMC-QU_2026-10-25.md': 'segmental_wall_motion_hmc-qu_2026-10-25.md',
    '性能对比_f150nv.md': 'performance_comparison_f150nv.md',
}
# Names tools look up by exact spelling.
KEEP_CASE = {'README.md', 'LICENSE', 'CMakeLists.txt'}
NAME_OK = re.compile(r'^[a-z0-9_.\-]+$')
# Files whose text is rewritten to follow the renames.
TEXT_EXT = {'.py', '.md', '.txt', '.json', '.tcl', '.bat', '.sh', '.c', '.h', '.cpp', '.hpp', '.yaml', '.yml',
            '.cfg', '.ini', '.log', '.rpt', '.csv', '.html', '.ps1', '.vbs', '.mk', '.dts', '.dtsi', '.bif',
            '.service', '.conf', '.xdc', ''}
# Copied verbatim: the packaging scripts work on the private project tree, whose names they must keep.
NO_REWRITE = {'make_github_release.py', 'make_share_package.py'}


def included(rel):
    if rel.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp')) and not any(fnmatch.fnmatch(rel, p) for p in PNG_KEEP):
        return False
    if fnmatch.fnmatch(rel, HLS_IMPL):
        return fnmatch.fnmatch(rel, HLS_IP_ZIP)
    if any(fnmatch.fnmatch(rel, p) for p in KEEP):
        return not any(fnmatch.fnmatch(rel, p) for p in ('*.npy', '*.npz', '*.y8', '*/images/*', '*/golden/*'))
    return not any(fnmatch.fnmatch(rel, p) for p in EXCLUDE)


def rename_part(name):
    if name in KEEP_CASE:
        return name
    return CN_NAMES.get(name, name).lower()


def published(rel):
    for src, dst in MOVE:
        if rel == src:
            return dst
        if src.endswith('/') and rel.startswith(src):
            return dst + '/'.join(rename_part(p) for p in rel[len(src):].split('/'))
    return '/'.join(rename_part(p) for p in rel.split('/'))


def collect():
    """[(source path, published path)] with every published name checked."""
    pairs = [(f, published(f)) for f in share.collect() if included(f)]
    pairs += [(src, dst) for dst, src in EXTRA.items()]
    bad = sorted({p for _, dst in pairs for p in dst.split('/') if p not in KEEP_CASE and not NAME_OK.match(p)})
    assert not bad, 'names breaking the naming rule (add them to CN_NAMES): %s' % bad
    seen = {}
    for src, dst in pairs:
        assert dst.lower() not in seen, 'two files publish to %s: %s, %s' % (dst, seen[dst.lower()], src)
        seen[dst.lower()] = src
    return pairs


def renames(pairs):
    """Old name -> new name for every path component that changes (text references follow these)."""
    out = {src: dst for src, dst in MOVE if not src.endswith('/') and src != dst and src != 'README.md'}
    for src, _ in pairs:
        if src in EXTRA.values():
            continue
        for old in src.split('/'):
            if rename_part(old) != old:
                out.setdefault(old, rename_part(old))
    return out


def rewriter(mapping):
    cn = {k: v for k, v in mapping.items() if re.search(r'[^\x00-\x7f]', k)}
    # ASCII names are replaced only as whole tokens (not inside longer identifiers).
    ascii_names = sorted((k for k in mapping if k not in cn), key=len, reverse=True)
    pattern = re.compile(r'(?<![A-Za-z0-9_\-])(%s)(?![A-Za-z0-9_\-])' % '|'.join(map(re.escape, ascii_names)))

    def rewrite(text):
        for k in sorted(cn, key=len, reverse=True):
            text = text.replace(k, cn[k])
        return pattern.sub(lambda m: mapping[m.group(1)], text)
    return rewrite


def scrub_pdf(src, dst):
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter(clone_from=PdfReader(src))
    writer.metadata = None
    writer.add_metadata({'/Title': 'Design report', '/Author': '', '/Creator': '', '/Producer': ''})
    writer.xmp_metadata = None
    with open(dst, 'wb') as f:
        writer.write(f)


def copy(pairs):
    if os.path.isdir(OUT):
        for name in os.listdir(OUT):
            if name != '.git':
                p = os.path.join(OUT, name)
                # Vitis exports are read-only: clear the flag and retry
                shutil.rmtree(p, onerror=lambda fn, q, _: (os.chmod(q, 0o666), fn(q))) if os.path.isdir(p) else os.remove(p)
    rewrite = rewriter(renames(pairs))
    for src, dst in pairs:
        path, out = os.path.join(ROOT, src), os.path.join(OUT, dst)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        ext = os.path.splitext(dst)[1].lower()
        if src in EXTRA.values() and ext == '.pdf':
            scrub_pdf(path, out)
            continue
        if ext in TEXT_EXT and src not in NO_REWRITE:
            try:
                text = open(path, encoding='utf-8', newline='').read()
            except UnicodeDecodeError:
                text = None
            if text is not None:
                open(out, 'w', encoding='utf-8', newline='').write(rewrite(text))
                continue
        shutil.copy2(path, out)
        os.chmod(out, 0o666)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--renames', action='store_true', help='print the renamed path components')
    args = parser.parse_args()
    pairs = collect()
    size = sum(os.path.getsize(os.path.join(ROOT, s)) for s, _ in pairs)
    if args.renames:
        for k, v in sorted(renames(pairs).items()):
            print('%s -> %s' % (k, v))
    elif args.list:
        for _, dst in pairs:
            print(dst)
    else:
        copy(pairs)
    print('%d files, %.1f MB' % (len(pairs), size / 1e6))
    return 0


if __name__ == '__main__':
    sys.exit(main())
