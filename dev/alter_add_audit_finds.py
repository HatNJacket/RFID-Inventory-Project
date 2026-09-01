"""One-off prod migration: create the rfid_audit_finds table.

Tagless boxes barcode-scanned during an audit walk (Nick, 2026-09-01):
work items that become labels, then pairs — never audit evidence on
their own. sqlite test/dev databases recreate themselves; Azure SQL
does not, so run this ONCE against prod before deploying:

    set DATABASE_URL=<prod mssql url, from the app's Azure settings>
    py dev/alter_add_audit_finds.py

Safe to re-run: create() with checkfirst does nothing when the table
already exists.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import get_engine  # noqa: E402
from app.models import AuditFind  # noqa: E402

engine = get_engine()
AuditFind.__table__.create(engine, checkfirst=True)
print("rfid_audit_finds is present.")
print("Done.")
