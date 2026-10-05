"""Seed the users list on prod (2026-10-05): the people who may sign in.

    py dev/seed_users.py            # adds anyone missing, skips the rest
    py dev/seed_users.py --dry-run  # just prints what it would add

Reads STATION_KEY from the web app's settings through az (never printed),
then POSTs each person to /api/users. Re-runnable: an email already on
the list is reported and left alone. Alex (no company email) is added
later from Settings, Users, once the Gmail address is known.
"""
import json
import subprocess
import sys

import requests

SITE = "https://telcan-rfid.azurewebsites.net"
PEOPLE = [
    ("stephen@telescopescanada.ca", "Steve"),
    ("nick@telescopescanada.ca", "Nick"),
    ("matt@telescopescanada.ca", "Matt"),
    ("clay@telescopescanada.ca", "Clay"),
    ("kevin@telescopescanada.ca", "Kevin"),
    ("evie@telescopescanada.ca", "Evie"),
]


def station_key() -> str:
    out = subprocess.run(
        ["az", "webapp", "config", "appsettings", "list", "-n", "telcan-rfid",
         "-g", "shopify-automation-rg", "-o", "json"],
        capture_output=True, text=True, shell=True, check=True,
    ).stdout
    for row in json.loads(out.lstrip("﻿")):
        if row.get("name") == "STATION_KEY":
            return row.get("value") or ""
    raise SystemExit("STATION_KEY is not set on the web app.")


def main() -> None:
    dry = "--dry-run" in sys.argv
    key = station_key()
    have = {
        u["email"] for u in requests.get(
            SITE + "/api/users", headers={"X-Station-Key": key}, timeout=30
        ).json()["users"]
    }
    for email, name in PEOPLE:
        if email in have:
            print(f"  already listed: {email}")
            continue
        if dry:
            print(f"  would add:      {email} ({name})")
            continue
        r = requests.post(
            SITE + "/api/users", headers={"X-Station-Key": key},
            json={"email": email, "name": name, "worker": "seed script"},
            timeout=30,
        )
        print(f"  {'added' if r.status_code == 201 else 'FAILED ' + str(r.status_code)}:"
              f"          {email} ({name})")


if __name__ == "__main__":
    main()
