"""Mirror PROD's data into the DEV site's sqlite (Nick, 2026-09-23:
"make sure the dev version duplicates the production version").

Walks /api/admin/snapshot/export on prod table by table and POSTs each
into /api/admin/snapshot/import on dev. The import side is double-
guarded (ALLOW_SNAPSHOT_IMPORT=1 env + sqlite engine), so this can
never write prod even if the URLs are swapped. Station keys are pulled
from az appsettings at runtime and never printed.

Run standalone any time dev drifts:  py dev/sync_dev.py
dev/deploy.py runs it automatically after every deploy.
"""
import json
import subprocess
import sys

import requests

PROD = "https://telcan-rfid.azurewebsites.net"
DEV = "https://telcan-rfid-dev.azurewebsites.net"
RG = "shopify-automation-rg"


def station_key(app_name: str) -> str:
    out = subprocess.run(
        f"az webapp config appsettings list -n {app_name} -g {RG} "
        f"--only-show-errors",
        shell=True, capture_output=True, text=True, check=True,
    ).stdout
    for setting in json.loads(out):
        if setting["name"] == "STATION_KEY":
            return setting["value"].strip()
    sys.exit(f"{app_name}: no STATION_KEY app setting found")


def main() -> int:
    prod_h = {"X-Station-Key": station_key("telcan-rfid")}
    dev_h = {"X-Station-Key": station_key("telcan-rfid-dev")}
    tables = requests.get(
        f"{PROD}/api/admin/snapshot/tables", headers=prod_h, timeout=60
    )
    tables.raise_for_status()
    total = 0
    failures = []
    for name in tables.json()["tables"]:
        try:
            got = requests.get(
                f"{PROD}/api/admin/snapshot/export",
                params={"table": name}, headers=prod_h, timeout=300,
            )
            got.raise_for_status()
            rows = got.json()["rows"]
            put = requests.post(
                f"{DEV}/api/admin/snapshot/import",
                json={"table": name, "rows": rows},
                headers=dev_h, timeout=300,
            )
            put.raise_for_status()
            total += len(rows)
            print(f"  {name}: {len(rows)} row(s)")
        except Exception as error:  # noqa: BLE001 - finish the sweep
            failures.append(name)
            print(f"! {name}: {error}")
    print(f"\n{'FAIL' if failures else 'OK'}  {total} row(s) mirrored"
          + (f"; failed: {', '.join(failures)}" if failures else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
