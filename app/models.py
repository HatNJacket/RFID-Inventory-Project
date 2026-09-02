"""Database models.

Per-package model: each RFID tag (EPC) is one row = one physical package.
Multiple rows can point at the same Shopify variant, which is exactly the
case where you have several identical boxes of the same product.

The Shopify identity fields (variant/product id, titles, sku, barcode) are
denormalized copies captured at assignment time. They're a snapshot for fast
display and offline resilience, not the source of truth -- Shopify remains
authoritative and you can re-sync them later if a product is renamed.
"""
from datetime import datetime

from sqlalchemy import (  # noqa: F401
    Boolean, DateTime, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class RfidAssignment(Base):
    __tablename__ = "rfid_assignments"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Explicit lengths on every string column: SQL Server refuses to index
    # or UNIQUE-constrain unbounded VARCHAR(max) columns.

    # The tag's unique EPC. UNIQUE enforces one assignment per physical tag;
    # reusing a tag means unassigning it first (or the replace endpoint).
    rfid_id: Mapped[str] = mapped_column(
        String(128), unique=True, index=True, nullable=False
    )

    shopify_variant_id: Mapped[str] = mapped_column(
        String(64), index=True, nullable=False
    )
    # 300 not 64: TELCAN-sourced ids are "handle:<shopify-handle>" and
    # handles run up to 255 chars.
    shopify_product_id: Mapped[str | None] = mapped_column(String(300))
    product_title: Mapped[str] = mapped_column(String(255), nullable=False)
    variant_title: Mapped[str | None] = mapped_column(String(255))
    # Indexed: bin checks and the Check step look tags up by SKU, and an
    # unindexed scan here grew with every tag applied.
    sku: Mapped[str | None] = mapped_column(String(100), index=True)
    barcode: Mapped[str | None] = mapped_column(String(64), index=True)
    bin_location: Mapped[str | None] = mapped_column(String(100))
    # Units this ONE tag stands for. None/1 = a single item, the normal
    # case; 8 = the tag is on a sealed case of 8. Counting reads units,
    # display keeps the two apart ("10" = "2 + 8x1").
    case_units: Mapped[int | None] = mapped_column(Integer)

    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    assigned_by: Mapped[str | None] = mapped_column(String(100))

    # True when the scanned EPC doesn't look like a normal tag (every real
    # tag is 24 hex chars) — probably a bad read; re-scan recommended.
    suspect: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )

    # The bin batch this tie came from, when it came from one. Lets a batch
    # be un-tied wholesale (abandon, or "undo pairing" to re-scan a shelf).
    batch_id: Mapped[int | None] = mapped_column(Integer, index=True)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "suspect": self.suspect,
            "batch_id": self.batch_id,
            "rfid_id": self.rfid_id,
            "shopify_variant_id": self.shopify_variant_id,
            "shopify_product_id": self.shopify_product_id,
            "product_title": self.product_title,
            "variant_title": self.variant_title,
            "sku": self.sku,
            "barcode": self.barcode,
            "bin_location": self.bin_location,
            "case_units": self.case_units,
            "assigned_at": (
                self.assigned_at.isoformat() if self.assigned_at else None
            ),
            "assigned_by": self.assigned_by,
        }


class RetiredTag(Base):
    """Tag records pulled OUT of the active table, kept forever (rows of
    text — storage is a non-issue, and Nick wants returns recoverable and
    stray stickers recognizable). Kinds:
      presumed-sold — unheard in a shelf sweep, shortfall matches sales/
                      on-hand; may come back as a return.
      replaced      — sticker peeled off a box, read OFF-box (the product
                      was blocking RF), discarded; a sweep hearing it
                      later means the peel step was skipped.
      dead          — sticker unreadable even off the box, discarded.
    Active-tag queries never touch this table — sweeps check it only to
    NAME an unknown EPC instead of shrugging."""

    __tablename__ = "rfid_retired_tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    rfid_id: Mapped[str] = mapped_column(
        String(128), unique=True, index=True, nullable=False
    )
    sku: Mapped[str | None] = mapped_column(String(100), index=True)
    product_title: Mapped[str | None] = mapped_column(String(255))
    shopify_variant_id: Mapped[str | None] = mapped_column(String(64))
    bin_location: Mapped[str | None] = mapped_column(String(100))
    case_units: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    retired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    retired_by: Mapped[str | None] = mapped_column(String(100))
    note: Mapped[str | None] = mapped_column(String(255))
    # How many sold-ledger units this retirement consumed (presumed-sold
    # only; replaced/dead never touch the ledger). Unretire hands exactly
    # this many back, so undo round-trips conserve the ledger. Prod needs
    # the one-off ALTER script dev/alter_add_retired_ledger.py.
    ledger_consumed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "rfid_id": self.rfid_id,
            "sku": self.sku,
            "product_title": self.product_title,
            "bin_location": self.bin_location,
            "case_units": self.case_units,
            "kind": self.kind,
            "retired_at": (
                self.retired_at.isoformat() if self.retired_at else None
            ),
            "retired_by": self.retired_by,
            "note": self.note,
        }


class ReleasedTag(Base):
    """A full snapshot of an RfidAssignment released from History's
    Assigned Tag undo (Nick, 2026-08-25). Unlike a plain unlink (which
    deletes the row and keeps only a History receipt), a release keeps
    EVERY field so the release itself can be undone: re-apply recreates
    the assignment exactly, original assigned_at/by included. The
    release/re-apply pair can loop forever, each press logged — that's
    by design, it's manual either way. Rows leave this table when
    re-applied. New table needs dev/alter_add_released_tags.py on prod."""

    __tablename__ = "rfid_released_tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    rfid_id: Mapped[str] = mapped_column(
        String(128), unique=True, index=True, nullable=False
    )
    shopify_variant_id: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    shopify_product_id: Mapped[str | None] = mapped_column(String(300))
    product_title: Mapped[str] = mapped_column(String(255), nullable=False)
    variant_title: Mapped[str | None] = mapped_column(String(255))
    sku: Mapped[str | None] = mapped_column(String(100), index=True)
    barcode: Mapped[str | None] = mapped_column(String(64))
    bin_location: Mapped[str | None] = mapped_column(String(100))
    case_units: Mapped[int | None] = mapped_column(Integer)
    suspect: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    batch_id: Mapped[int | None] = mapped_column(Integer)
    assigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    assigned_by: Mapped[str | None] = mapped_column(String(100))
    released_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    released_by: Mapped[str | None] = mapped_column(String(100))

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "rfid_id": self.rfid_id,
            "sku": self.sku,
            "product_title": self.product_title,
            "bin_location": self.bin_location,
            "case_units": self.case_units,
            "released_at": (
                self.released_at.isoformat() if self.released_at else None
            ),
            "released_by": self.released_by,
        }


class BackorderDebt(Base):
    """Units Shopify's on-hand runs BEHIND the shelf because stock went
    negative before a delivery arrived (Nick, 2026-08-26, AirGradient:
    on-hand sat at -1 for a customer backorder, 48 boxes arrived, so
    Shopify counts 47 while 48 tagged boxes stand on the shelf — and the
    daily tag arithmetic would flag that one unit forever). Noted
    automatically when a receiving batch closes, capped at the committed
    (promised-to-customers) units so a plain mistag can never hide here.
    The expected-tag math adds uncleared rows; any operator on-hand
    write supersedes them (that write IS the fresh shelf truth), and
    History's Backorder Noted event can clear one by hand. New table
    needs dev/alter_add_backorder_debt.py on prod."""

    __tablename__ = "rfid_backorder_debt"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    units: Mapped[int] = mapped_column(Integer, nullable=False)
    noted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    source: Mapped[str | None] = mapped_column(String(120))
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cleared_by: Mapped[str | None] = mapped_column(String(100))

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "sku": self.sku,
            "units": self.units,
            "noted_at": self.noted_at.isoformat() if self.noted_at else None,
            "source": self.source,
            "cleared_at": (
                self.cleared_at.isoformat() if self.cleared_at else None
            ),
            "cleared_by": self.cleared_by,
        }


class AuditFind(Base):
    """A tagless box barcode-scanned during an audit walk (Nick,
    2026-09-01). A find is a WORK ITEM, never audit evidence: it counts
    nothing until its label is printed and PAIRED — the paired tag then
    answers the next sweep, and the sweep is the only counter of
    physical presence. Lifecycle: open → printed (label queued, EPC
    remembered) → resolved (an assignment with that EPC appeared) /
    dismissed. Never-printed rows expire after an hour (lazy, on read);
    printed rows stay flagged until resolved or dismissed so owed
    labels can't vanish silently. New table needs
    dev/alter_add_audit_finds.py on prod."""

    __tablename__ = "rfid_audit_finds"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    product_title: Mapped[str | None] = mapped_column(String(255))
    variant_title: Mapped[str | None] = mapped_column(String(255))
    shopify_variant_id: Mapped[str | None] = mapped_column(String(64))
    shopify_product_id: Mapped[str | None] = mapped_column(String(300))
    barcode: Mapped[str | None] = mapped_column(String(64))
    # What the gun actually read (may be an alias or a folded rescue).
    scanned_code: Mapped[str] = mapped_column(String(200), nullable=False)
    # The product's HOME bin at scan time — where the label sends the box.
    bin_location: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open", server_default="open"
    )
    print_job_id: Mapped[int | None] = mapped_column(Integer)
    print_epc: Mapped[str | None] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(100))
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    resolved_by: Mapped[str | None] = mapped_column(String(100))

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "sku": self.sku,
            "product_title": self.product_title,
            "variant_title": self.variant_title,
            "barcode": self.barcode,
            "scanned_code": self.scanned_code,
            "bin_location": self.bin_location,
            "status": self.status,
            "print_job_id": self.print_job_id,
            "print_epc": self.print_epc,
            "created_at": (
                self.created_at.isoformat() if self.created_at else None
            ),
            "created_by": self.created_by,
            "resolved_at": (
                self.resolved_at.isoformat() if self.resolved_at else None
            ),
            "resolved_by": self.resolved_by,
        }


class OrderReceipt(Base):
    """One "Receive entire shipment" run (Nick, 2026-09-01): the RFID
    side loaded a whole TC-Planner stock order, printed every remaining
    label, and the operator paired what physically arrived. Tracks the
    lifecycle the 1-hour watchdog needs: printed_at (labels queued),
    settled_at (operator pressed "all boxes labelled - counting unused
    labels"; the clock starts here), stock_updated_at (TC-Planner
    reported the Shopify stock update; clears/forestalls the Review
    task). New table needs dev/alter_add_order_receipts.py on prod."""

    __tablename__ = "rfid_order_receipts"

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_order_id: Mapped[int] = mapped_column(
        Integer, index=True, nullable=False
    )
    reference: Mapped[str | None] = mapped_column(String(60))
    vendor: Mapped[str | None] = mapped_column(String(100))
    batch_id: Mapped[int] = mapped_column(Integer, index=True,
                                          nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(100))
    printed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    settled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    stock_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    review_task_id: Mapped[int | None] = mapped_column(Integer)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "stock_order_id": self.stock_order_id,
            "reference": self.reference,
            "vendor": self.vendor,
            "batch_id": self.batch_id,
            "created_by": self.created_by,
            "printed_at": (
                self.printed_at.isoformat() if self.printed_at else None
            ),
            "settled_at": (
                self.settled_at.isoformat() if self.settled_at else None
            ),
            "stock_updated_at": (
                self.stock_updated_at.isoformat()
                if self.stock_updated_at else None
            ),
            "review_task_id": self.review_task_id,
        }


class HeldLabelList(Base):
    """A strip of printed-but-unpaired labels, kept on the liner in a
    vendor-labelled container (Nick, 2026-09-01): products a stock order
    listed that didn't physically ship. The strip is swept as ONE pool
    of EPCs (labels aren't RFID-encoded per product, so the pool covers
    the whole strip; product accounting lives in the per-SKU counts).
    Held labels are labels-in-a-bag, never boxes: they count in nothing.
    Pairing an EPC from the pool later - sticking the label on the box
    that finally arrived - removes it from the pool and decrements its
    product's count."""

    __tablename__ = "rfid_held_lists"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int | None] = mapped_column(Integer, index=True)
    stock_order_id: Mapped[int | None] = mapped_column(Integer, index=True)
    reference: Mapped[str | None] = mapped_column(String(60))
    vendor: Mapped[str | None] = mapped_column(String(100), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(100))
    # Newline-joined EPC pool from sweeping the strip.
    epcs: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=""
    )

    def epc_set(self) -> set:
        return {
            e.strip().upper() for e in (self.epcs or "").split("\n")
            if e.strip()
        }

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "batch_id": self.batch_id,
            "stock_order_id": self.stock_order_id,
            "reference": self.reference,
            "vendor": self.vendor,
            "created_at": (
                self.created_at.isoformat() if self.created_at else None
            ),
            "created_by": self.created_by,
            "epc_count": len(self.epc_set()),
        }


class HeldLabelItem(Base):
    """Per-product count on a held-label strip: how many unused labels
    of this SKU are on it. Decremented when a held label is paired to a
    real box; a zero row means every one of that product's labels found
    its box."""

    __tablename__ = "rfid_held_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    list_id: Mapped[int] = mapped_column(Integer, index=True,
                                         nullable=False)
    sku: Mapped[str] = mapped_column(String(100), index=True,
                                     nullable=False)
    product_title: Mapped[str | None] = mapped_column(String(255))
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "list_id": self.list_id,
            "sku": self.sku,
            "product_title": self.product_title,
            "count": self.count,
        }


class LabelDismissal(Base):
    """An operator dismissed the audit's "printed label answered but was
    never paired" warning for this EPC (Nick, 2026-09-01): the label is
    accounted for - binned, a known blank, whatever - and must not
    resurface on every future sweep. Dismissed EPCs vanish from the
    printed-labels-heard list AND the unknown list. New table needs
    dev/alter_add_label_dismissals.py on prod."""

    __tablename__ = "rfid_label_dismissals"

    id: Mapped[int] = mapped_column(primary_key=True)
    epc: Mapped[str] = mapped_column(
        String(128), unique=True, index=True, nullable=False
    )
    dismissed_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BarcodeAlias(Base):
    """Maps a foreign ("fake") barcode — e.g. a manufacturer barcode on the
    box — to a known product, after an operator confirmed the link. Lives in
    its own app-owned table rather than a column on the TELCAN mirror tables,
    which get rewritten by the Shopify sync.

    Scans of an alias resolve to the product but carry a warning flag so the
    UI can ask for confirmation (skippable per session for bulk work)."""

    __tablename__ = "rfid_barcode_aliases"

    id: Mapped[int] = mapped_column(primary_key=True)

    # The scanned foreign barcode.
    alias_barcode: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )

    # Product anchor + display snapshot (SKU is the stable key both TELCAN
    # and the Shopify API agree on).
    sku: Mapped[str | None] = mapped_column(String(100), index=True)
    barcode: Mapped[str | None] = mapped_column(String(64))
    product_title: Mapped[str | None] = mapped_column(String(255))

    created_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # "manual" = an operator linked it deliberately (permanent until
    # unlinked). "label" = derived from a saved custom label line and
    # EPHEMERAL: replaced automatically when that line changes (Nick,
    # 2026-08-25). New column needs dev/alter_add_alias_kind.py on prod.
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="manual",
        server_default="manual",
    )

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "alias_barcode": self.alias_barcode,
            "sku": self.sku,
            "barcode": self.barcode,
            "product_title": self.product_title,
            "created_by": self.created_by,
            "kind": self.kind or "manual",
            "created_at": (
                self.created_at.isoformat() if self.created_at else None
            ),
        }


class SerialPrefix(Base):
    """Brand serial-number prefixes. Some manufacturers (Astronomik) put a
    product identifier in the first digits of each unit's serial number and
    barcode the serial — so the barcode differs per unit, but its prefix
    identifies the product. Loaded from the manufacturer's mapping sheet via
    load_astronomik.py; scans resolve prefix -> SKU."""

    __tablename__ = "rfid_serial_prefixes"

    prefix: Mapped[str] = mapped_column(String(8), primary_key=True)
    brand: Mapped[str] = mapped_column(String(50), nullable=False)
    sku: Mapped[str | None] = mapped_column(String(100), index=True)
    item_name: Mapped[str | None] = mapped_column(String(255))
    # Operator-preferred short name, printed on labels in place of the store
    # name. Starts empty (a default is derived from item_name at lookup
    # time); once an operator saves one it sticks — the xlsx loader never
    # writes this column, so sheet reloads can't clobber it.
    label_name: Mapped[str | None] = mapped_column(String(255))
    # Optional operator note shown loudly whenever this prefix is scanned
    # (e.g. "part of a 3-filter set — ONE tag per set"). Loader never
    # touches it.
    scan_note: Mapped[str | None] = mapped_column(String(255))

    def as_dict(self) -> dict:
        return {
            "prefix": self.prefix,
            "brand": self.brand,
            "sku": self.sku,
            "item_name": self.item_name,
            "label_name": self.label_name,
            "scan_note": self.scan_note,
        }


class BarcodeChange(Base):
    """Audit log of barcode overwrites: an operator replaced a product's
    real Shopify barcode with a scanned (usually manufacturer) barcode.
    One row per change — who, when, old and new — so accidents are easy
    to trace and reverse."""

    __tablename__ = "rfid_barcode_changes"

    id: Mapped[int] = mapped_column(primary_key=True)

    sku: Mapped[str | None] = mapped_column(String(100), index=True)
    product_title: Mapped[str | None] = mapped_column(String(255))
    shopify_variant_id: Mapped[str | None] = mapped_column(String(64))
    # What was replaced: "barcode" or "sku" (old_/new_ hold either kind).
    changed_field: Mapped[str] = mapped_column(
        String(20), nullable=False, default="barcode",
        server_default="barcode",
    )
    old_barcode: Mapped[str | None] = mapped_column(String(64))
    new_barcode: Mapped[str] = mapped_column(String(64), index=True)

    changed_by: Mapped[str | None] = mapped_column(String(100))
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "sku": self.sku,
            "product_title": self.product_title,
            "shopify_variant_id": self.shopify_variant_id,
            "changed_field": self.changed_field,
            "old_barcode": self.old_barcode,
            "new_barcode": self.new_barcode,
            "changed_by": self.changed_by,
            "changed_at": (
                self.changed_at.isoformat() if self.changed_at else None
            ),
        }


class PrintJob(Base):
    """One queued Zebra label: print the barcode AND encode the EPC into the
    sticker's RFID chip in a single pass.

    One row = one physical label = one pre-generated EPC. The local print
    agent (print_agent.py on the printer laptop) claims pending jobs, drives
    the printer, and reports back; on success the server auto-creates the
    matching RfidAssignment — no manual tag scan needed for printed labels.

    Lifecycle: pending -> printing -> done | error   (pending -> canceled)
    """

    __tablename__ = "rfid_print_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)

    # The EPC this label will carry, generated at queue time.
    epc: Mapped[str] = mapped_column(
        String(128), unique=True, index=True, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), index=True, nullable=False, default="pending"
    )

    # Product snapshot for the label text (same shape as assignments).
    barcode: Mapped[str | None] = mapped_column(String(64))
    sku: Mapped[str | None] = mapped_column(String(100))
    product_title: Mapped[str] = mapped_column(String(255), nullable=False)
    variant_title: Mapped[str | None] = mapped_column(String(255))
    bin_location: Mapped[str | None] = mapped_column(String(100))
    # Printed under the bin ("BIN: G2-1. Other: B17") for products whose
    # boxes are split across shelves.
    other_bins: Mapped[str | None] = mapped_column(String(255))
    shopify_variant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # 300 not 64: TELCAN-sourced ids are "handle:<shopify-handle>" and
    # handles run up to 255 chars.
    shopify_product_id: Mapped[str | None] = mapped_column(String(300))

    # Operator-preferred label name (serialized brands like Astronomik, or
    # per-SKU custom names); when set, the agent prints it per placement.
    label_name: Mapped[str | None] = mapped_column(String(255))
    # Where the name goes: "header" replaces the store name, "sku" replaces
    # the SKU line above the barcode. NULL = header (back-compat).
    label_placement: Mapped[str | None] = mapped_column(String(10))
    # Explicit centre-line text, when it differs from a name placed via
    # label_placement (both lines customized differently). Newer print
    # agents apply it; older ones fall back to label_name+placement.
    label_sku: Mapped[str | None] = mapped_column(String(56))

    # Units this label's tag stands for. Set only for a sealed case, where
    # the label has to say "8 x 93581" so nobody treats the box as one item.
    case_units: Mapped[int | None] = mapped_column(Integer)

    requested_by: Mapped[str | None] = mapped_column(String(100))
    error: Mapped[str | None] = mapped_column(String(500))

    # Set when the job came from a bin batch (Batch Tagging tab); the Print
    # Queue groups and reports per batch.
    batch_id: Mapped[int | None] = mapped_column(Integer, index=True)

    # Target printer (rfid_printers.name). NULL = any agent may claim it —
    # the pre-selector behavior, and what legacy agents still expect.
    printer: Mapped[str | None] = mapped_column(String(100))

    # Scan-station print session: one token per product LOAD, so every
    # print pressed before the next barcode reset shares it. The Queue
    # tab groups a product's loose jobs by this (Nick, 2026-08-25:
    # 1 + 9 + 4 labels of one product were 14 flat rows). NULL on batch
    # jobs and on jobs from before the column existed. Prod needs
    # dev/alter_add_print_session.py once.
    print_session: Mapped[str | None] = mapped_column(String(24))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    printed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "epc": self.epc,
            "status": self.status,
            "batch_id": self.batch_id,
            "barcode": self.barcode,
            "sku": self.sku,
            "product_title": self.product_title,
            "variant_title": self.variant_title,
            "bin_location": self.bin_location,
            "other_bins": self.other_bins,
            "shopify_variant_id": self.shopify_variant_id,
            "shopify_product_id": self.shopify_product_id,
            "label_name": self.label_name,
            "label_placement": self.label_placement,
            "label_sku": self.label_sku,
            # The agent prints "8 x SKU" when this is set.
            "case_units": self.case_units,
            "requested_by": self.requested_by,
            "error": self.error,
            "printer": self.printer,
            "print_session": self.print_session,
            "created_at": (
                self.created_at.isoformat() if self.created_at else None
            ),
            "printed_at": (
                self.printed_at.isoformat() if self.printed_at else None
            ),
        }


class Printer(Base):
    """One known label printer, keyed by the name its print agent reports.

    Rows are DETECTED, never hand-entered: every agent claim upserts its
    printer's row and stamps last_seen, so the Scan Station's printer
    picker always shows what has actually checked in. Agents older than
    the printer param claim under the DEFAULT_PRINTER name, which keeps
    the single-printer warehouse working unchanged."""

    __tablename__ = "rfid_printers"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stable identity, as reported by the agent (--printer-id).
    name: Mapped[str] = mapped_column(
        String(100), unique=True, index=True, nullable=False
    )
    # Human descriptor shown on the picker card ("ZD621R · RFID encoder").
    kind: Mapped[str | None] = mapped_column(String(100))
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "last_seen": (
                self.last_seen.isoformat() if self.last_seen else None
            ),
        }


class RefreshLog(Base):
    """Duration log for the site's refresh buttons, manual and automatic.

    Feeds every refresh button's ETA ("Estimated 30 seconds") and its
    left-to-right fill: clients post how long a finished refresh took,
    the stats endpoint serves a recent median per kind, and server-side
    auto refreshes log here too so the estimate covers both."""

    __tablename__ = "rfid_refresh_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    source: Mapped[str] = mapped_column(
        String(10), nullable=False, default="manual"
    )  # manual | auto
    ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ScanNote(Base):
    """A per-product scan note, shown LOUDLY every time the product is
    scanned — at the Scan Station (in the product card) and on the C72
    (own sound, warning-coloured text). "Open the case before tagging",
    "one tag per set of three". One row per SKU; empty = no note."""

    __tablename__ = "rfid_scan_notes"

    sku: Mapped[str] = mapped_column(String(100), primary_key=True)
    note: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False
    )


class SoldRecord(Base):
    """One fulfilled-order line for a SKU the RFID system tracks: the
    order shipped, Shopify's on-hand dropped, but the box left with its
    tag still on file — so the EXPECTED tag count for the SKU drops while
    the tags themselves stay listed. `retired` counts how many of these
    units have since had a tag marked sold during an audit; the ledger's
    unretired remainder is what audits use to explain missing tags.

    Filled by the read-only orders sync (app/orders_sync.py). Rows are
    facts about Shopify, never writes to it."""

    __tablename__ = "rfid_sold_ledger"
    __table_args__ = (
        UniqueConstraint("order_id", "sku", name="uq_sold_order_sku"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    order_name: Mapped[str | None] = mapped_column(String(32))
    sku: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    # Units of this line whose physical tag has been marked sold (audit).
    retired: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "order_id": self.order_id,
            "order_name": self.order_name,
            "sku": self.sku,
            "quantity": self.quantity,
            "retired": self.retired,
            "fulfilled_at": (
                self.fulfilled_at.isoformat() if self.fulfilled_at else None
            ),
            "created_at": (
                self.created_at.isoformat() if self.created_at else None
            ),
        }


class Batch(Base):
    """One bin-tagging session (Batch Tagging tab): walk to a bin, scan every
    box, print one label per box, apply them, pair each label's EPC, verify.

    Lifecycle: collecting -> printing -> pairing -> done  (any -> abandoned)
    Inventory/bin mismatches never block the batch — they become ReviewTasks
    at completion instead of demanding fixes at the shelf."""

    __tablename__ = "rfid_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    bin_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # None = a normal bin batch. "receiving" = a shipment worked at the
    # desk/pallet: no home bin (bin_name is the RECEIVING sentinel), labels
    # carry each ITEM's bin, printing repeats per pass, and finishing files
    # per-bin inventory checks instead of running verify. Receiving batches
    # never count a bin as done, like side trips.
    # (Prod needs dev/alter_add_batch_kind.py once — sqlite tests recreate.)
    kind: Mapped[str | None] = mapped_column(String(20), index=True)
    status: Mapped[str] = mapped_column(
        String(20), index=True, nullable=False, default="collecting"
    )
    created_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    # Stamped the first time the operator runs the Verify sweep.
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    # Which step whoever's driving is on (collect/check/print/pair/verify).
    # Status alone can't carry this — collect and check are both
    # "collecting" — so terminals watching the same batch use this to
    # follow along.
    ui_step: Mapped[str | None] = mapped_column(String(20))

    # When a baseline sweep was applied: the shelf was RFID-swept before
    # collecting, so items carry tagged_before counts and the Check step
    # can flag "tags on file here but none read". None = no sweep, and
    # those checks stay silent.
    baseline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    # A "side trip": strays found in the bin being worked that actually
    # belong on another shelf. Carrying them home is a small batch of its
    # own so the labels, tags and history all say the RIGHT bin — and this
    # points back at the batch to return to when it's done. Side trips
    # close without a shelf sweep; they only ever cover a few boxes, not
    # the whole of their bin.
    parent_batch_id: Mapped[int | None] = mapped_column(Integer, index=True)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "bin_name": self.bin_name,
            "kind": self.kind,
            "status": self.status,
            "parent_batch_id": self.parent_batch_id,
            "baseline_at": (
                self.baseline_at.isoformat() if self.baseline_at else None
            ),
            "created_by": self.created_by,
            "created_at": (
                self.created_at.isoformat() if self.created_at else None
            ),
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "verified_at": (
                self.verified_at.isoformat() if self.verified_at else None
            ),
            "ui_step": self.ui_step,
        }


class BatchItem(Base):
    """One unique product inside a batch: how many boxes were scanned, the
    product snapshot for labels, and pairing progress. Unknown barcodes are
    kept as unresolved rows so the physical count isn't lost."""

    __tablename__ = "rfid_batch_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)

    # What the scanner actually read the first time (serial, alias, barcode).
    scanned_code: Mapped[str] = mapped_column(String(64), nullable=False)
    resolved: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )

    shopify_variant_id: Mapped[str | None] = mapped_column(String(64))
    shopify_product_id: Mapped[str | None] = mapped_column(String(300))
    product_title: Mapped[str | None] = mapped_column(String(255))
    variant_title: Mapped[str | None] = mapped_column(String(255))
    sku: Mapped[str | None] = mapped_column(String(100), index=True)
    barcode: Mapped[str | None] = mapped_column(String(64))
    # The product's SAVED bin at scan time (labels use the batch's bin).
    bin_location: Mapped[str | None] = mapped_column(String(100))
    # Other shelves this same product also lives on, comma-joined.
    other_bins: Mapped[str | None] = mapped_column(String(255))
    serial_prefix: Mapped[str | None] = mapped_column(String(8))
    label_name: Mapped[str | None] = mapped_column(String(255))
    image_url: Mapped[str | None] = mapped_column(String(500))

    qty_scanned: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Shopify on-hand (TELCAN mirror) when first scanned; None when unknown.
    expected_qty: Mapped[int | None] = mapped_column(Integer)
    paired_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # "multi_box" | "bundle" | None (single-box, nothing to decide). A
    # snapshot of the ProductKind answer at scan time so the row travels
    # with its own verdict.
    kind: Mapped[str | None] = mapped_column(String(16))

    # Sealed cases counted into this row. Until now qty_scanned was boxes
    # AND labels AND tags AND units all at once; a case of 8 breaks that —
    # it is one box, one label, one tag, but eight units. So loose boxes
    # stay in qty_scanned and sealed cases are counted separately:
    #   units  = qty_scanned + case_count * case_units
    #   labels = qty_scanned + case_count
    case_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    case_units: Mapped[int | None] = mapped_column(Integer)
    # Tags for this product read off the shelf by a baseline sweep BEFORE
    # collecting started — boxes already tagged in an earlier session. They
    # count as units on the shelf but never queue labels.
    tagged_before: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # "I can't do this one": no barcode, wrapped so it can't be identified,
    # damaged label. The row STAYS, with the reason, so the shelf's story is
    # intact — it just prints no label and blocks nothing. Deliberately
    # local: skipping never writes a quantity anywhere, least of all to
    # Shopify, and never sets a count to zero.
    skipped: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    skip_reason: Mapped[str | None] = mapped_column(String(120))
    # The operator explicitly picked a listing (USE THIS LISTING /
    # reassign): the "ambiguous" flag stops re-raising for this row —
    # candidate count alone can never clear it, since the other listings
    # keep existing (Nick, 2026-08-25). Prod needs the one-off ALTER
    # script dev/alter_add_listing_locked.py.
    listing_locked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    # When this product was FIRST physically scanned in this batch - the
    # operator's walking order. Labels queue in this order so the printed
    # stack matches the shelf walk instead of coming out shuffled (Nick,
    # 2026-08-25). NULL on rows never actually scanned (pre-seeded,
    # record-only). Prod needs dev/alter_add_first_scanned.py.
    first_scanned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "batch_id": self.batch_id,
            "scanned_code": self.scanned_code,
            "resolved": self.resolved,
            "shopify_variant_id": self.shopify_variant_id,
            "shopify_product_id": self.shopify_product_id,
            "product_title": self.product_title,
            "variant_title": self.variant_title,
            "sku": self.sku,
            "barcode": self.barcode,
            "bin_location": self.bin_location,
            "other_bins": self.other_bins,
            "serial_prefix": self.serial_prefix,
            "label_name": self.label_name,
            "image_url": self.image_url,
            "qty_scanned": self.qty_scanned,
            "expected_qty": self.expected_qty,
            "paired_count": self.paired_count,
            "kind": self.kind,
            "case_count": self.case_count,
            "case_units": self.case_units,
            "tagged_before": self.tagged_before,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "listing_locked": self.listing_locked,
            # The operator's walking order - also the PRINT order (labels
            # queue by it), which the C72's pair auto-advance walks
            # (Nick, 2026-08-26).
            "first_scanned_at": (
                self.first_scanned_at.isoformat()
                if self.first_scanned_at else None
            ),
            # Precomputed so every client shows the same two numbers rather
            # than each reinventing the arithmetic. Baseline-tagged boxes
            # are units on the shelf (so old C72 builds show the combined
            # tracker for free), but they are NOT labels — they already
            # wear one.
            "units_total": self.qty_scanned
            + self.case_count * (self.case_units or 0)
            + self.tagged_before,
            "labels_total": self.qty_scanned + self.case_count,
        }


class BinMapEntry(Base):
    """Which bin each variant lives in, per Shopify metafields. Bins exist
    ONLY as metafields (the TELCAN mirror's Bin_Name is empty store-wide),
    so a background job walks the whole catalog through the Shopify API and
    rewrites this table — batch creation then answers "what's expected in
    bin X" instantly, even right after an app restart."""

    __tablename__ = "rfid_bin_map"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str | None] = mapped_column(String(100), index=True)
    barcode: Mapped[str | None] = mapped_column(String(64))
    product_title: Mapped[str | None] = mapped_column(String(255))
    variant_title: Mapped[str | None] = mapped_column(String(255))
    shopify_variant_id: Mapped[str | None] = mapped_column(String(64))
    shopify_product_id: Mapped[str | None] = mapped_column(String(300))
    bin: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    # One row per bin: a product split across shelves ("G2-1 & B17") gets a
    # row for each, and each row names the others here.
    other_bins: Mapped[str | None] = mapped_column(String(255))
    # SHELF-expected units: Shopify on_hand MINUS the Unavailable bucket
    # (reserved/damaged/safety/QC) since 2026-09-01 (Nick, W9160A) - the
    # unavailable count rides along so audits can EXPLAIN an over-count.
    # Prod needs dev/alter_add_binmap_unavailable.py.
    qty: Mapped[int | None] = mapped_column(Integer)
    unavailable: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    image_url: Mapped[str | None] = mapped_column(String(500))
    # Shopify's product vendor — the brand, for filtering/sorting.
    vendor: Mapped[str | None] = mapped_column(String(150), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LabelName(Base):
    """Operator-preferred label header for NON-serialized products (serial
    brands keep theirs on SerialPrefix). When set, panel prints put this
    name at the top of the label instead of the store header."""

    __tablename__ = "rfid_label_names"

    sku: Mapped[str] = mapped_column(String(100), primary_key=True)
    label_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # "header" (replaces the store name) or "sku" (replaces the SKU line).
    placement: Mapped[str] = mapped_column(
        String(10), nullable=False, default="header", server_default="header"
    )
    # Set only when the operator customized BOTH lines with DIFFERENT text
    # (label_name+placement can't express that): label_name is the top
    # line, this is the centre line.
    sku_text: Mapped[str | None] = mapped_column(String(56))
    updated_by: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RfidIncompatible(Base):
    """Products whose applied tag never answers a sweep — the label reads
    fine in hand, dead once it's on the box (ZWO Desicc, several Optolong
    lines). Labels still print and tags still pair (counts stay honest);
    this flag tells every sweep-side check not to expect an answer."""

    __tablename__ = "rfid_incompatible"

    sku: Mapped[str] = mapped_column(String(100), primary_key=True)
    set_by: Mapped[str | None] = mapped_column(String(100))
    set_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class NonTaggable(Base):
    """Products not worth individual tags at all — a big bin of
    thumbscrews or dew-heater straps (Nick, 2026-08-25). Stronger than
    RfidIncompatible (which still labels and counts): a non-taggable
    product is never seeded into batches, never gets labels, and audits
    skip it entirely. A single tag MAY still be paired to it by hand as
    a bag/bin marker so Locate can find the container; that tag carries
    no inventory meaning. New table needs dev/alter_add_non_taggable.py
    on prod."""

    __tablename__ = "rfid_non_taggable"

    sku: Mapped[str] = mapped_column(String(100), primary_key=True)
    set_by: Mapped[str | None] = mapped_column(String(100))
    note: Mapped[str | None] = mapped_column(String(255))
    set_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CaseCode(Base):
    """A barcode that is not a listing at all: the manufacturer's case /
    inner-pack code, meaning "N units of one product". Scanning it used to
    come back empty, which is how a worker ends up holding a box nobody can
    place.

    Kept local because these codes genuinely aren't in Shopify (verified for
    0234935810 — a case of 8 × 93581, whose own barcode is 050234935814).
    Defining one never writes anything to the store."""

    __tablename__ = "rfid_case_codes"

    barcode: Mapped[str] = mapped_column(String(64), primary_key=True)
    # What's inside, and how many. One product per case by design.
    sku: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    units: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Free text shown on EVERY surface that resolves this code — the whole
    # point is that the warning follows the barcode, not the tab.
    scan_note: Mapped[str | None] = mapped_column(String(255))
    product_title: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def as_dict(self) -> dict:
        return {
            "barcode": self.barcode,
            "sku": self.sku,
            "units": self.units,
            "scan_note": self.scan_note,
            "product_title": self.product_title,
            "created_by": self.created_by,
        }


class ProductKind(Base):
    """Why one listing occupies several box slots: is it ONE product that
    ships as several boxes, or a BUNDLE whose "boxes" are really separate
    products that each have their own listing?

    The two look identical in the bin metafield ("B18-1, G3-3"), but they
    need opposite handling — a multi-box product wants a tag on every box,
    while a bundle has no physical box of its own and must not be tagged at
    all (its components are tagged as themselves). The catalog usually says
    which is which (title "BUNDLE: ...", SKU "91519+93973"), so this table
    only stores the operator's answer where the guess is wrong or absent.

    Keyed by SKU so it is set once and every later batch already knows."""

    __tablename__ = "rfid_product_kinds"

    sku: Mapped[str] = mapped_column(String(100), primary_key=True)
    # "multi_box" (tag every box) or "bundle" (tag nothing).
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    # Bundles that shouldn't be in the RFID system at all: never seeded into
    # a batch, never labelled. Kept as a row, not a delete, so it can come
    # back if the call was wrong.
    excluded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    updated_by: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class HiddenBin(Base):
    """Bins the operator ticked off the work list — empty shelves, bins
    someone else handles, anything not worth tagging. Hidden, never
    deleted: the board can show them again on demand."""

    __tablename__ = "rfid_hidden_bins"

    bin: Mapped[str] = mapped_column(String(100), primary_key=True)
    hidden_by: Mapped[str | None] = mapped_column(String(100))
    hidden_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FlaggedBin(Base):
    """Bins marked "ask first" — the operator isn't confident scanning them
    without checking with someone who knows the inventory better. A visible
    warning on the work list, nothing more: the bin still counts as to-do
    and nothing about it is blocked."""

    __tablename__ = "rfid_flagged_bins"

    bin: Mapped[str] = mapped_column(String(100), primary_key=True)
    # Why it needs a second pair of eyes, e.g. "mixed consignment stock".
    note: Mapped[str | None] = mapped_column(String(255))
    flagged_by: Mapped[str | None] = mapped_column(String(100))
    flagged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BundleContent(Base):
    """What ONE unit of a bundle SKU physically contains — e.g. the
    W9184B bundle-of-10 is 10 × W9184B, nothing else on the shelf. Set
    once per bundle, used everywhere: batch collect stops listing the
    bundle as its own countable product (the component count covers it),
    and the could-not-scan desk flow can offer the components to tag."""

    __tablename__ = "rfid_bundle_contents"

    id: Mapped[int] = mapped_column(primary_key=True)
    bundle_sku: Mapped[str] = mapped_column(String(100), index=True,
                                            nullable=False)
    component_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    def as_dict(self) -> dict:
        return {
            "component_sku": self.component_sku,
            "qty": self.qty,
        }


class ReviewNote(Base):
    """Operator notes pinned to a Review entry. Keyed by STRING so notes
    stick to both stored tasks (their integer id as text) and the live
    synthetic bin-mismatch entries ("binmm:SKU"), which have no row of
    their own. Notes survive resolution — they're the context trail."""

    __tablename__ = "rfid_review_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_key: Mapped[str] = mapped_column(String(120), index=True,
                                          nullable=False)
    note: Mapped[str] = mapped_column(String(500), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "task_key": self.task_key,
            "note": self.note,
            "created_by": self.created_by,
            "created_at": (
                self.created_at.isoformat() if self.created_at else None
            ),
        }


class MismatchDismissal(Base):
    """A dismissed live bin-mismatch. The synthetic entries have no row to
    mark dismissed, so the exact disagreement is recorded instead: the
    entry stays suppressed while (sku, tags' bin, Shopify's bin) all still
    match — if either shelf changes, that's a NEW disagreement and it
    reappears. Undoable from History (deleting the row un-dismisses)."""

    __tablename__ = "rfid_mismatch_dismissals"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    tag_bin: Mapped[str] = mapped_column(String(100), nullable=False)
    shopify_bin: Mapped[str] = mapped_column(String(255), nullable=False)
    dismissed_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class C72Tuning(Base):
    """Live-tunable C72 parameters, one JSON row. The gun polls this
    every ~2 s while its Locate tab is up and applies changes without an
    APK build — field tuning happens as a conversation instead of a
    deploy loop. Diagnostic plumbing, not inventory data."""

    __tablename__ = "rfid_c72_tuning"

    id: Mapped[int] = mapped_column(primary_key=True)
    values: Mapped[str] = mapped_column(String(2000), nullable=False,
                                        default="{}")
    updated_by: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False
    )


class C72DebugEvent(Base):
    """One telemetry line from the gun (locate ticks, applied tuning),
    kept as a pruned ring so the table can't grow unbounded."""

    __tablename__ = "rfid_c72_debug"

    id: Mapped[int] = mapped_column(primary_key=True)
    device: Mapped[str | None] = mapped_column(String(100))
    line: Mapped[str] = mapped_column(String(400), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class C72Command(Base):
    """One remote command for the gun (get_state, set_pref, set_power,
    say, beep, recreate…). The C72 polls pending commands every ~2 s,
    executes, and acks with a result — so field debugging can reach
    INTO the app without an APK build. Diagnostic plumbing, not
    inventory data."""

    __tablename__ = "rfid_c72_commands"

    id: Mapped[int] = mapped_column(primary_key=True)
    command: Mapped[str] = mapped_column(String(50), nullable=False)
    arg: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    done_by: Mapped[str | None] = mapped_column(String(100))
    result: Mapped[str | None] = mapped_column(String(400))


class LocateQueueEntry(Base):
    """A product queued for a physical tag hunt. Added from any product
    preview on the web terminal (Review is the usual source — mismatched
    bins that need a walk); the C72's Locate tab lists these so nobody
    types a 24-hex EPC by hand. Removable from either side."""

    __tablename__ = "rfid_locate_queue"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    label: Mapped[str | None] = mapped_column(String(255))
    added_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AppSetting(Base):
    """Server-stored key/value switches the web UI can flip without an
    app-settings change (no restart, no az CLI). First user: the 1-left
    auto-confirm pause switch."""

    __tablename__ = "rfid_app_settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(String(500), nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
        onupdate=func.now(), nullable=False
    )


class OneLeftCheck(Base):
    """One action taken against the 1-left dashboard's verification queue
    (the Inventory Verification Function App): an auto-confirm backed by
    RFID evidence, an operator's manual confirm, or a re-queue (the undo).
    The dashboard side only records a name and a date — THIS row is where
    the actual evidence lives, and History renders it."""

    __tablename__ = "rfid_oneleft_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    product_title: Mapped[str | None] = mapped_column(String(255))
    vendor: Mapped[str | None] = mapped_column(String(150))
    # Shopify's stock claim at action time (None = their API couldn't say).
    claimed: Mapped[int | None] = mapped_column(Integer)
    evidence_units: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Human-readable evidence summary ("2 tags paired 2026-08-17 by Nick").
    evidence: Mapped[str | None] = mapped_column(String(500))
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    # The name their confirm endpoint accepted (its fixed employee list).
    employee: Mapped[str | None] = mapped_column(String(100))
    # Who, in RFID terms, caused it: the operator, or the trigger for autos.
    operator: Mapped[str | None] = mapped_column(String(100))
    ok: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "sku": self.sku,
            "product_title": self.product_title,
            "vendor": self.vendor,
            "claimed": self.claimed,
            "evidence_units": self.evidence_units,
            "evidence": self.evidence,
            "action": self.action,
            "employee": self.employee,
            "operator": self.operator,
            "ok": self.ok,
            "error": self.error,
            "created_at": (
                self.created_at.isoformat() if self.created_at else None
            ),
        }


class AuditSession(Base):
    """A named, resumable audit (the EasyScan-stocktake shape Nick
    picked): bundle a scope — a set of bins, or a slice of the 1-left
    queue — walk it across as many gun sessions and days as it takes,
    and finish with everything accounted for. Progress is derived from
    the item rows, never stored."""

    __tablename__ = "rfid_audit_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # "bins" (walk-scan each bin) or "oneleft" (confirm 1-left checks).
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), index=True, nullable=False, default="open"
    )
    created_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    completed_by: Mapped[str | None] = mapped_column(String(100))

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "status": self.status,
            "created_by": self.created_by,
            "created_at": (
                self.created_at.isoformat() if self.created_at else None
            ),
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "completed_by": self.completed_by,
        }


class AuditSessionItem(Base):
    """One unit of an audit session's scope: a bin to walk-scan, or a
    1-left check (keyed by SKU) to settle. Ticked by an operator; 1-left
    items also tick themselves when a dashboard confirm for their SKU
    lands after the session started."""

    __tablename__ = "rfid_audit_session_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        Integer, index=True, nullable=False
    )
    # Bin name for "bins" sessions, SKU for "oneleft" sessions.
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str | None] = mapped_column(String(255))
    done: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="0"
    )
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    done_by: Mapped[str | None] = mapped_column(String(100))
    note: Mapped[str | None] = mapped_column(String(255))

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "key": self.key,
            "label": self.label,
            "done": self.done,
            "done_at": self.done_at.isoformat() if self.done_at else None,
            "done_by": self.done_by,
            "note": self.note,
        }


class EpcCapture(Base):
    """One RFID sweep sent from the C72 companion app: the operator scans a
    shelf freely (everything held on the device), then hits Send once —
    Wi-Fi to Azure, no Bluetooth involved. The PC browser pulls the latest
    capture into the batch-verify step (or future audits)."""

    __tablename__ = "rfid_epc_captures"

    id: Mapped[int] = mapped_column(primary_key=True)
    device: Mapped[str | None] = mapped_column(String(100))
    note: Mapped[str | None] = mapped_column(String(255))
    # Set when the sweep was taken during a bin batch, so the terminal
    # watching that batch knows the sweep is meant for it.
    batch_id: Mapped[int | None] = mapped_column(Integer, index=True)
    epc_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Newline-joined unique EPCs. Text, not String: sweeps of a full rack
    # can be thousands of tags.
    epcs: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def as_dict(self, with_epcs: bool = False) -> dict:
        d = {
            "id": self.id,
            "device": self.device,
            "note": self.note,
            "batch_id": self.batch_id,
            "epc_count": self.epc_count,
            "created_at": (
                self.created_at.isoformat() if self.created_at else None
            ),
        }
        if with_epcs:
            d["epcs"] = self.epcs.split("\n") if self.epcs else []
        return d


class ReviewTask(Base):
    """The system's task inbox (Review tab): anything the software noticed
    but shouldn't fix on its own — inventory count mismatches, unresolved
    barcodes, incomplete pairing. Created by batches (and later audits);
    resolved or dismissed by an operator, never auto-closed."""

    __tablename__ = "rfid_review_tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    sku: Mapped[str | None] = mapped_column(String(100), index=True)
    product_title: Mapped[str | None] = mapped_column(String(255))
    detail: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), index=True, nullable=False, default="open"
    )
    batch_id: Mapped[int | None] = mapped_column(Integer)

    created_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_by: Mapped[str | None] = mapped_column(String(100))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(String(255))

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "category": self.category,
            "sku": self.sku,
            "product_title": self.product_title,
            "detail": self.detail,
            "status": self.status,
            "batch_id": self.batch_id,
            "created_by": self.created_by,
            "created_at": (
                self.created_at.isoformat() if self.created_at else None
            ),
            "resolved_by": self.resolved_by,
            "resolved_at": (
                self.resolved_at.isoformat() if self.resolved_at else None
            ),
            "resolution_note": self.resolution_note,
        }


class LinkScan(Base):
    """One scan relayed from the C72's LINK tab to the web terminal.

    The gun POSTs every read (BT barcode or trigger RFID) here instead of
    acting on it; the web terminal polls with an id cursor, feeds the value
    through its normal input paths, and posts the outcome back so the gun
    can ding or buzz. Rows are transient plumbing, not history — the actions
    they trigger do their own History logging — and get swept after a day.
    """

    __tablename__ = "link_scans"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)  # barcode|epc
    value: Mapped[str] = mapped_column(String(200), nullable=False)
    rssi: Mapped[str | None] = mapped_column(String(20))
    device: Mapped[str | None] = mapped_column(String(100), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Set by the consuming terminal once the scan has been acted on.
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ok: Mapped[bool | None] = mapped_column(Boolean)
    outcome: Mapped[str | None] = mapped_column(String(300))

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "value": self.value,
            "rssi": self.rssi,
            "device": self.device,
            "created_at": (
                self.created_at.isoformat() if self.created_at else None
            ),
            "consumed_at": (
                self.consumed_at.isoformat() if self.consumed_at else None
            ),
            "ok": self.ok,
            "outcome": self.outcome,
        }
