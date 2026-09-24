"""One-off (Nick, 2026-09-24): the product History timelines drown in
the launch-day flood of Opened/Resolved Review events from Aug 18 -
"remove all but one of the mass events". Those events DERIVE from
ReviewTask rows, so this trims the rows themselves: within each
category's Aug-18 flood, the NEWEST resolved task survives as the
specimen and the rest go. Only tasks BOTH created on 2026-08-18 AND
already resolved qualify - open tasks and human-filed work are never
touched, and small groups (5 or fewer) are left alone because they are
history, not noise.

Usage:
    set DATABASE_URL first (prod), then:
    py dev/cleanup_aug18_reviews.py           # dry run - counts only
    py dev/cleanup_aug18_reviews.py --delete  # do it
"""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.database import get_engine  # noqa: E402
from app.models import ReviewTask  # noqa: E402

TARGET = date(2026, 8, 18)
KEEP_THRESHOLD = 5


def main() -> None:
    do_delete = "--delete" in sys.argv
    with Session(get_engine()) as session:
        rows = [
            t for t in session.scalars(select(ReviewTask))
            if t.created_at is not None
            and t.created_at.date() == TARGET
            and t.status == "resolved"
        ]
        by_cat: dict = {}
        for t in rows:
            by_cat.setdefault(t.category or "?", []).append(t)
        total_doomed = 0
        for cat, group in sorted(by_cat.items()):
            group.sort(key=lambda t: (t.resolved_at or t.created_at, t.id))
            doomed = group[:-1] if len(group) > KEEP_THRESHOLD else []
            print(f"{cat}: {len(group)} resolved Aug-18 task(s)"
                  + (f" -> deleting {len(doomed)}, keeping 1"
                     if doomed else " -> left alone"))
            total_doomed += len(doomed)
            if do_delete:
                for t in doomed:
                    session.delete(t)
        if do_delete:
            session.commit()
            print(f"DELETED {total_doomed} task row(s).")
        else:
            print(f"DRY RUN - would delete {total_doomed} row(s). "
                  f"Re-run with --delete.")


if __name__ == "__main__":
    main()
