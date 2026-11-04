"""Real-board v4 acceptance: golden logits, confidence, ROI, morphology, stability.

Requires the running v4 TCP server. Never loads a bitstream or changes the board.
"""
import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy import ndimage

import confidence
import imageio
import imagelink
import proto

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tcp_image_rx/ai/net"))
import vessel_morph_ref as ref

FLAGS = imagelink._parse_flags("proc,mask,overlay,stats,logit,morph")
NAMES = {"proc", "mask", "overlay", "logit", "morph"}
FIELDS = ("w", "h", "vessel_px", "fov_px", "length_x10", "caliber_x100",
          "branches", "endpoints", "segments", "tortuosity_x1000",
          "fractal_x1000", "density_x1000")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stability", type=int, default=50)
    parser.add_argument("--output", default="board_check_v4/acceptance.json")
    args = parser.parse_args()
    output = HERE / args.output
    output.parent.mkdir(exist_ok=True, parents=True)
    report = {"utc": datetime.now(timezone.utc).isoformat(), "host": "192.168.1.254",
              "checks": [], "pass": False}
    link = imagelink.ImageLink(report["host"], 5060, timeout=20)

    def require(condition, message):
        if not condition:
            raise AssertionError(message)

    def exchange(label, rgb, roi=None, threshold=-3, res=512, quiet=False):
        imgs, times, rows, stats = link.round_trip(
            rgb, mode=0, flags=FLAGS, aiparam=proto.make_aiparam(0, threshold, res),
            thr=99, roi=roi)
        item = {"name": label, "roi": roi, "threshold": threshold, "res": res,
                "timings": times, "rows": rows, "stats": stats, "pass": False}
        report["checks"].append(item)
        require(stats is not None and stats["version"] >= 4, "v4 board required")
        require(stats["ai_us"] > 0, "AI did not run")
        require(set(imgs) == NAMES, "missing returned images: " + str(set(imgs)))
        require(all(s == {"rows_ok": 512, "crc_errors": 0, "seq_errors": 0}
                    for s in rows.values()), "CRC/sequence/frame error")
        require("morph" in stats, "missing morphology block")
        plane = confidence.logit_plane(imgs["logit"])
        require(imgs["mask"] == confidence.mask_rgb(plane, threshold), "confidence mask mismatch")
        require(imgs["overlay"] == confidence.overlay_rgb(imgs["proc"], plane, threshold),
                "confidence overlay mismatch")
        logit_rgb = np.frombuffer(imgs["logit"], np.uint8).reshape(512, 512, 3)
        require(np.array_equal(logit_rgb[:, :, 0], logit_rgb[:, :, 1]) and
                np.array_equal(logit_rgb[:, :, 0], logit_rgb[:, :, 2]), "logit RGB lanes mismatch")
        if roi:
            x, y, w, h = roi
            require((stats["crop_w"], stats["crop_h"]) == proto.roi_crop(*roi)[2:], "ROI crop size")
            require(stats["total"] == w * h, "ROI stats area")
        else:
            require(stats["ai_res"] == res and stats["total"] == res * res, "resolution stats")
        item["pass"] = True
        if not quiet:
            print(label, "PASS", "ai=%.1fms" % (stats["ai_us"] / 1000),
                  "total=%.1fms" % times["total_ms"], flush=True)
        return imgs, plane, item

    def check_metrics(rgb, plane, item):
        stats, roi, res, threshold = item["stats"], item["roi"], item["res"], item["threshold"]
        logit = np.frombuffer(plane, np.uint8).reshape(512, 512).astype(np.int16) - 128
        color = np.frombuffer(rgb, np.uint8).reshape(512, 512, 3)
        if roi:
            x, y, w, h = roi
            logit, color = logit[y:y+h, x:x+w], color[y:y+h, x:x+w]
            scale = 512
        else:
            step = 512 // res
            logit = logit[::step, ::step]
            scale = res
        mask = logit >= threshold
        item["reference_vessels"] = int(mask.sum())
        item["reference_components"] = int(ndimage.label(mask)[1])
        require(stats["vessels"] == item["reference_vessels"], "vessel count")
        require(stats["components"] == item["reference_components"], "4-connected component count")
        expected = ref.analyze(mask, ref.fov_from_rgb(color, mask.shape[1], mask.shape[0]), scale)
        actual = stats["morph"]
        errors = {}
        for key in FIELDS:
            tolerance = 1 if key in ("tortuosity_x1000", "fractal_x1000") else 0
            if abs(actual[key] - expected[key]) > tolerance:
                errors[key] = {"board": actual[key], "reference": int(expected[key])}
        quads = [actual["quad01"] & 65535, actual["quad01"] >> 16,
                 actual["quad23"] & 65535, actual["quad23"] >> 16]
        if quads != expected["quad_x1000"]:
            errors["quadrants"] = {"board": quads, "reference": expected["quad_x1000"]}
        item["morphology_reference_errors"] = errors
        require(not errors, "morphology mismatch: " + str(errors))

    def save_images(prefix, imgs, plane):
        for name, data in imgs.items():
            imageio.save_image(str(output.parent / (prefix + "_" + name + ".png")), data)
        imageio.save_image(str(output.parent / (prefix + "_heat.png")), confidence.heat_rgb(plane))

    try:
        golden_dir = HERE.parents[1] / "medical_fpga/deployment/unet_network/vectors/images/im0002"
        rgb = np.fromfile(golden_dir / "input.bin", "<i2").reshape(512, 512, 8)[:, :, :3].astype(np.uint8).tobytes()
        golden = np.fromfile(golden_dir / "golden/final.bin", "<i2").reshape(512, 512, 8)[:, :, 0]
        imgs, plane, item = exchange("golden_full_logits", rgb)
        actual = np.frombuffer(plane, np.uint8).reshape(512, 512).astype(np.int16) - 128
        item["golden_logit_mismatches"] = int(np.count_nonzero(actual != golden))
        require(item["golden_logit_mismatches"] == 0, "golden logits mismatch")
        check_metrics(rgb, plane, item)

        for sample in ("im0001", "im0002", "im0003", "im0004", "im0005"):
            rgb, _ = imageio.load_pattern(sample)
            imgs, plane, item = exchange(sample + "_full", rgb)
            check_metrics(rgb, plane, item)
            if sample == "im0002":
                base_rgb, base_plane = rgb, plane
                save_images(sample, imgs, plane)
                imageio.save_image(str(output.parent / "im0002_input.png"), rgb)

        rois = [(180,150,150,140), (0,0,64,64), (470,500,42,12),
                (0,200,40,40), (480,10,32,300), (201,183,127,95),
                (201,183,119,95), (200,180,128,96), (201,183,127,111),
                (0,0,1,1), (511,511,1,1), (0,0,512,512)]
        for index, roi in enumerate(rois):
            imgs, plane, item = exchange("roi_%d" % index, base_rgb, roi=roi)
            n, bad = confidence.roi_compare(plane, base_plane, roi)
            item["roi_pixels"], item["roi_logit_mismatches"] = n, bad
            require(bad == 0, "ROI/full logit mismatch")
            arr = np.frombuffer(plane, np.uint8).reshape(512, 512).copy()
            x, y, w, h = roi
            arr[y:y+h, x:x+w] = 0
            require(not np.any(arr), "ROI outside logit must be -128")
            check_metrics(base_rgb, plane, item)
            if index == 0:
                save_images("roi", imgs, plane)

        for threshold in (-20, 0, 15):
            imgs, plane, item = exchange("threshold_%d" % threshold, base_rgb, threshold=threshold)
            require(plane == base_plane, "threshold changed network logits")
            check_metrics(base_rgb, plane, item)
        for res in (256, 128, 64, 32):
            imgs, plane, item = exchange("resolution_%d" % res, base_rgb, res=res)
            check_metrics(base_rgb, plane, item)

        expected_digest = None
        for index in range(args.stability):
            imgs, plane, item = exchange("stability_%02d" % (index + 1), base_rgb, quiet=True)
            require(plane == base_plane, "stability logits changed")
            digest = hashlib.sha256(b"".join(imgs[k] for k in sorted(imgs))).hexdigest()
            if expected_digest is None:
                expected_digest = digest
            require(digest == expected_digest, "stability image content changed")
            if (index + 1) % 10 == 0:
                print("stability %d/%d PASS" % (index + 1, args.stability), flush=True)
        report["stability_frames"] = args.stability
        report["pass"] = True
        print("BOARD_V4_ACCEPTANCE_PASS", len(report["checks"]), "frames", flush=True)
    except Exception as exc:
        if report["checks"]:
            report["checks"][-1]["pass"] = False
        report["error"] = repr(exc)
        print("BOARD_V4_ACCEPTANCE_FAIL", repr(exc), flush=True)
        raise
    finally:
        link.close()
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
