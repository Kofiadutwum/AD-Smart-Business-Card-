"""Put back Python library files that went missing on the server, then restart the site.

On 25 Nov 2026 the host's virus scanner deleted one Pillow file and the site was down for
11 hours. Cron runs this every 10 minutes. It uses only the standard library, so it still
runs when a package is broken, and it prints nothing while every file is in place:

    */10 * * * * cd /home/adsmartt/adsmart && /home/adsmartt/virtualenv/adsmart/3.12/bin/python scripts/repair_libraries.py >> /home/adsmartt/logs/repair-libraries.log 2>&1
"""

import csv
import subprocess
import sys
from datetime import datetime, timezone
from importlib.metadata import distributions
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent


def damaged(dists):
    """{(name, version): [files missing or of the wrong size]} for the installed packages.

    Sizes come from each package's install record (RECORD), read directly because
    ``dist.files`` silently leaves out files that no longer exist. Compiled .pyc files are
    skipped because Python rebuilds them by itself.
    """
    found = {}
    for dist in dists:
        for row in csv.reader((dist.read_text("RECORD") or "").splitlines()):
            if len(row) < 3 or not row[2] or row[0].endswith(".pyc"):
                continue
            try:
                ok = Path(dist.locate_file(row[0])).stat().st_size == int(row[2])
            except OSError:
                ok = False
            if not ok:
                found.setdefault((dist.metadata["Name"], dist.version), []).append(row[0])
    return found


def main():
    broken = damaged(distributions())
    if not broken:
        return 0
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    for (name, version), files in sorted(broken.items()):
        print(f"{stamp} {name} {version}: {len(files)} file(s) missing or changed, first {files[0]}", flush=True)
    specs = [f"{name}=={version}" for name, version in sorted(broken)]
    # Same versions, no dependencies: puts the files back without changing anything else.
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check",
         "--force-reinstall", "--no-deps", *specs],
        capture_output=True, text=True,
    )
    if result.returncode:
        print(f"{stamp} reinstall of {' '.join(specs)} FAILED: {result.stderr.strip()[-800:]}", flush=True)
        return 1
    (APP_ROOT / "tmp").mkdir(exist_ok=True)
    (APP_ROOT / "tmp" / "restart.txt").touch()
    print(f"{stamp} reinstalled {' '.join(specs)} and restarted the site", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
