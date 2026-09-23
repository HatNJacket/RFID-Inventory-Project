"""ONE deploy command (Nick, 2026-09-23: dev must duplicate prod
automatically): build the zip, deploy PROD, deploy DEV, then mirror
prod's data into dev's sqlite via dev/sync_dev.py.

    py dev/deploy.py              # the whole pipeline
    py dev/deploy.py --prod-only  # prod deploy, no dev, no sync
    py dev/deploy.py --dev-only   # dev deploy + data sync only
    py dev/deploy.py --no-sync    # both deploys, skip the data mirror

The az commands are the SAME hard-rule commands as ever (mkdeploy's
Python zipfile, az webapp deploy) - this just stops anyone forgetting
the dev half.
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
    if "--dev-only" not in args:
        deploy("telcan-rfid")
    if "--prod-only" in args:
        return
    deploy("telcan-rfid-dev")
    if "--no-sync" not in args:
        print("\n=== mirroring prod data into dev")
        sys.path.insert(0, HERE)
        import sync_dev
        if sync_dev.main() != 0:
            sys.exit(1)


if __name__ == "__main__":
    main()
