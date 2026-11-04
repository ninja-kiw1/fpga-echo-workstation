"""Check the running board service without changing its firmware or software.

Run with the srtp-vision Python. Results are saved in board_check/.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import imageio
import imagelink
import proto


def main():
    here = Path(__file__).resolve().parent
    output = here / "board_check"
    output.mkdir(exist_ok=True)
    report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(),
              "host": "192.168.1.254", "port": 5060, "checks": []}
    link = imagelink.ImageLink(report["host"], report["port"])

    def exchange(label, rgb, **kwargs):
        images, timings, rows, stats = link.round_trip(rgb, **kwargs)
        valid = all(r["rows_ok"] == 512 and r["crc_errors"] == 0
                    and r["seq_errors"] == 0 for r in rows.values())
        entry = {"name": label, "timings": timings, "rowstats": rows,
                 "ai_stats": stats, "pass": valid}
        report["checks"].append(entry)
        assert valid, (label, rows)
        print(label, "rows/CRC/sequence PASS", round(timings["total_ms"], 1), "ms")
        return images, entry

    try:
        for pattern in ("bars", "checker", "gradient"):
            rgb, _ = imageio.load_pattern(pattern)
            images, entry = exchange("passthrough_" + pattern, rgb, mode=5)
            entry["byte_mismatches"] = sum(a != b for a, b in zip(rgb, images["proc"]))
            entry["pass"] &= len(images["proc"]) == len(rgb) and entry["byte_mismatches"] == 0
            assert entry["pass"], entry

        rgb, _ = imageio.load_pattern("im0002")
        threshold = imageio.auto_threshold(rgb)[0]
        for mode in (0, 1, 2, 3, 4, 6):
            exchange("processing_mode_%d" % mode, rgb, mode=mode, thr=threshold)

        flags = imagelink._parse_flags("proc,mask,overlay,stats,logit,morph")
        hashes = []
        for i in range(3):
            images, entry = exchange("ai_im0002_%d" % (i + 1), rgb, mode=0,
                                     flags=flags, aiparam=proto.make_aiparam(0, -3, 512))
            stats = entry["ai_stats"]
            entry["pass"] &= stats["ai_res"] == 512 and stats["ai_us"] > 0
            assert entry["pass"], entry
            hashes.append(images["mask"])
        report["ai_repeat_identical"] = all(m == hashes[0] for m in hashes)
        assert report["ai_repeat_identical"]
        report["board_stats_version"] = stats["version"]
        report["capabilities"] = {"logit": "logit" in images,
                                  "roi": stats["version"] >= proto.AI_STAT_VERSION_ROI,
                                  "morph": "morph" in stats}
        for name, data in images.items():
            imageio.save_image(str(output / (name + ".png")), data)
        imageio.save_image(str(output / "input.png"), rgb)

        # Use the exact input accompanying the stored golden, avoiding resize differences.
        golden_dir = here.parents[1] / "medical_fpga/deployment/unet_network/vectors/images/im0002"
        source = np.fromfile(golden_dir / "input.bin", dtype="<i2").reshape(512, 512, 8)
        golden = np.fromfile(golden_dir / "golden/final.bin", dtype="<i2").reshape(512, 512, 8)[:, :, 0]
        golden_rgb = source[:, :, :3].astype(np.uint8).tobytes()
        images, entry = exchange("golden_im0002_mask", golden_rgb, mode=5,
                                 flags=imagelink._parse_flags("mask,stats"),
                                 aiparam=proto.make_aiparam(0, -3, 512))
        actual = np.frombuffer(images["mask"], dtype=np.uint8).reshape(512, 512, 3)
        expected = np.where(golden >= -3, 255, 0).astype(np.uint8)
        mismatch = int(np.count_nonzero(np.any(actual != expected[:, :, None], axis=2)))
        entry["mask_pixel_mismatches"] = mismatch
        entry["mask_pixels"] = 512 * 512
        entry["pass"] &= mismatch == 0
        print("Golden mask mismatch:", mismatch, "/", 512 * 512)
        assert entry["pass"], entry
        report["pass"] = True
    except Exception as exc:
        report["pass"] = False
        report["error"] = repr(exc)
        raise
    finally:
        link.close()
        (output / "results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Board stats version:", report["board_stats_version"], report["capabilities"])
    print("BOARD_CHECK_PASS", output)


if __name__ == "__main__":
    main()
