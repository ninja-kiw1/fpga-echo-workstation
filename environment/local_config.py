"""Project-local paths; no changes to the machine-wide environment."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RX = ROOT / "tcp_image/tcp_image_rx"
CONFIG = json.loads((ROOT / "environment/local-tools.json").read_text(encoding="utf-8"))
TOOLS = Path(os.environ.get("PLD_TOOLS_ROOT", CONFIG["tools_root"]))
REQUIRED_VERSION = CONFIG["required_version"]

def tool_path(relative):
    return str(TOOLS / relative)

if __name__ == "__main__":
    for name, value in {
        "PLD_RX": RX,
        "PLD_TOOLS_ROOT": TOOLS,
        "PLD_VIVADO": TOOLS / "Vivado",
        "PLD_VITIS": TOOLS / "Vitis",
    }.items():
        print(f'set "{name}={str(value)}"')
