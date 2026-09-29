"""ONE deploy command: build the zip with mkdeploy's Python zipfile and
deploy PROD. Nick, 2026-09-29: deploys go to production only - the dev
twin (telcan-rfid-dev) and its prod->dev data mirror are retired from
the default pipeline (dev was barely used, and the mirror's full-table
export loaded the shared Basic database on every deploy).

    py dev/deploy.py               # prod only (the default)
    py dev/deploy.py --with-dev    # ALSO deploy the dev twin + mirror
                                   # (only if Nick asks for dev again)

The az command is the SAME hard-rule command as ever (mkdeploy's
Python zipfile, az webapp deploy) - never PowerShell Compress-Archive.
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RG = "shopify-automation-rg"


def run(cmd: str) -> None:
    print(f"\n=== {cmd}")
    res = subprocess.run(cmd, shell=True, cwd=os.path.dirname(HERE))
    if res.returncode != 0:
        sys.exit(res.returncode)


def deploy(app_name: str) -> None:
    run(f"az webapp deploy -n {app_name} -g {RG} --type zip "
        f"--src-path dev/deploy.zip --only-show-errors")


def main() -> None:
    args = set(sys.argv[1:])
    run("py dev/mkdeploy.py")
    deploy("telcan-rfid")
    if "--with-dev" not in args:
        return
    deploy("telcan-rfid-dev")
    print("\n=== mirroring prod data into dev")
    sys.path.insert(0, HERE)
    import sync_dev
    if sync_dev.main() != 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
