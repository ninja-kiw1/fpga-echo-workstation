"""Check the tool release; same-series patches are allowed by local policy."""
import re
import subprocess
from local_config import TOOLS, REQUIRED_VERSION, CONFIG

def main():
    failed = False
    for label, relative, args in (
        ("Vivado", "Vivado/bin/vivado.bat", ["-version"]),
        ("Vitis HLS", "Vitis/bin/vitis-run.bat", ["--version"]),
    ):
        path = TOOLS / relative
        if not path.is_file():
            print(f"FAIL {label}: missing {path}")
            failed = True
            continue
        result = subprocess.run([str(path), *args], capture_output=True, text=True,
                                errors="replace", timeout=90)
        output = result.stdout + result.stderr
        versions = re.findall(r"\bv(202\d\.\d+(?:\.\d+)?)\b", output)
        actual = versions[0] if versions else "unknown"
        same_series = actual.split('.')[:2] == REQUIRED_VERSION.split('.')[:2]
        compatible = CONFIG.get("allow_same_series", False) and same_series
        # This Windows Vivado installation returns 1 for a successful -version.
        version_ok = result.returncode == 0 or (
            label == "Vivado" and result.returncode == 1
            and "SW Build" in output and not re.search(r"(?im)^ERROR", output))
        ok = version_ok and (actual == REQUIRED_VERSION or compatible)
        status = "PASS" if actual == REQUIRED_VERSION and ok else "WARN" if ok else "FAIL"
        print(f"{status} {label}: {actual}; baseline {REQUIRED_VERSION}; {path}")
        if status == "WARN":
            print("Same-series version accepted by local policy. Rebuilt outputs require full regression.")
        if not ok:
            failed = True
            if not versions:
                print(output[:1500])
    return 2 if failed else 0

if __name__ == "__main__":
    raise SystemExit(main())
