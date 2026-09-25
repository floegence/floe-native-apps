"""Actual GIO child status reproduction; no graphical application is required."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    root = Path(sys.argv[1]).resolve()
    evidence = Path(tempfile.mkdtemp(prefix="launcher-status-", dir=root))
    desktop = evidence / "fixture.desktop"
    child = evidence / "exit.py"
    child.write_text("raise SystemExit(46)\n")
    desktop.write_text("[Desktop Entry]\nType=Application\nName=Floe launcher failure fixture\n"
                       "Exec=/usr/bin/python3 " + str(child) + "\nTerminal=false\n")
    receipt = evidence / "receipt.json"
    result = subprocess.run(["python3", str(root / "application.py"), str(desktop), str(receipt)],
                            capture_output=True, text=True, timeout=10, env=os.environ.copy())
    actual = json.loads(receipt.read_text())
    outcome = {"expected_exit_code": 46, "supervisor_exit_code": result.returncode,
               "receipt": actual, "evidence": str(evidence)}
    outcome["passed"] = actual.get("exit_code") == 46 and result.returncode == 46
    (evidence / "result.json").write_text(json.dumps(outcome, indent=2) + "\n")
    (evidence / "launcher.log").write_text(result.stdout + result.stderr)
    print(json.dumps(outcome, indent=2))
    if not outcome["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
