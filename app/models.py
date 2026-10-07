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
    Boolean, DateTime, Float, Index, Integer, String, Text,
    UniqueConstraint, func,
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

    # Per-BOX condition (Nick, 2026-09-16): what state THIS physical unit
    # is in - "open-box", "used", "damaged", "needs-parts", "display",
    # "safety-stock". NULL = good (the normal box). Distinct from stock
    # buckets (available/committed/on-hand); vocabulary lives in
    # main.BOX_CONDITIONS. Prod needs dev/alter_add_condition.py.
    condition: Mapped[str | None] = mapped_column(String(20))

    # Scored audit queue (2026-09-28): the moment a sweep last heard
    # this tag (any ingest - C72 SEND, bin check, shelf sweep). NULL on
    # tags never swept since the column landed; consumers treat
    # COALESCE(last_heard_at, assigned_at) as the truth, because the
    # pairing scan itself physically read the sticker. Added to prod by
    # init_db's idempotent column upgrade.
    last_heard_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    # What heard it last and where (2026-10-06): "I1-2 audit", "D7-2
    # batch tag sweep", "packing scan", "locate". The gun can't know a
    # position, only what it was doing and where it was told it was;
    # that's enough for the ladder's "heard elsewhere" row. Prod column
    # via init_db's upgrade list.
    last_heard_ctx: Mapped[str | None] = mapped_column(String(120))

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
            "condition": self.condition,
            "assigned_at": (
                self.assigned_at.isoformat() if self.assigned_at else None
            ),
            "assigned_by": self.assigned_by,
            "last_heard_at": (
                (self.last_heard_at or self.assigned_at).isoformat()
                if (self.last_heard_at or self.assigned_at) else None
            ),
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
    # The box's condition at retirement - carried so an unretire (a
    # return coming back, an undo) restores it. NULL = good.
    condition: Mapped[str | None] = mapped_column(String(20))

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "rfid_id": self.rfid_id,
            "sku": self.sku,
            "product_title": self.product_title,
            "bin_location": self.bin_location,
            "case_units": self.case_units,
            "kind": self.kind,
            "condition": self.condition,
            "retired_at": (
                self.retired_at.isoformat() if self.retired_at else None
            ),
            "retired_by": self.retired_by,
            "note": self.note,
        }


class OpenboxReturn(Base):
    """A sold, RFID-tagged product that came BACK as an open-box return
    (Nick, 2026-09-15). The Scan Station files one of these when the
    operator presses Set as Open Box: the unit now sells as its -O twin,
    but its ORIGINAL tag (retired presumed-sold at sale time) is still
    somewhere on the packaging. The row keeps a watch open: when a sweep
    or audit hears any presumed-sold tag of the original SKU, the
    operator is asked whether that box is this open-box unit - YES either
    adopts the old tag as the -O product's live tag (no fresh label was
    paired) or says "peel the old sticker" (a fresh -O label already
    went on). One linked ReviewTask (category openbox-return) shows the
    open watch in the Review tab. Prod needs
    dev/alter_add_openbox_returns.py."""

    __tablename__ = "rfid_openbox_returns"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The ORIGINAL (sealed) product whose retired tags are watched.
    sku: Mapped[str] = mapped_column(String(100), index=True,
                                     nullable=False)
    product_title: Mapped[str | None] = mapped_column(String(255))
    # The open-box twin the unit now sells as.
    openbox_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    openbox_variant_id: Mapped[str | None] = mapped_column(String(64))
    openbox_product_id: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(
        String(20), index=True, nullable=False, default="open"
    )
    # The old tag's EPC when the operator knew it at filing time.
    known_epc: Mapped[str | None] = mapped_column(String(128))
    # EPCs the operator answered NO for - never re-prompted on this row.
    not_epcs: Mapped[str | None] = mapped_column(String(2000))
    # The linked Review task (closed together with this row).
    task_id: Mapped[int | None] = mapped_column(Integer)
    # How it closed: adopted / peel / peeled / dismissed.
    resolution: Mapped[str | None] = mapped_column(String(40))
    created_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_by: Mapped[str | None] = mapped_column(String(100))
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "sku": self.sku,
            "product_title": self.product_title,
            "openbox_sku": self.openbox_sku,
            "openbox_variant_id": self.openbox_variant_id,
            "openbox_product_id": self.openbox_product_id,
            "status": self.status,
            "known_epc": self.known_epc,
            "not_epcs": self.not_epcs,
            "task_id": self.task_id,
            "resolution": self.resolution,
            "created_by": self.created_by,
            "created_at": (
                self.created_at.isoformat() if self.created_at else None
            ),
            "resolved_by": self.resolved_by,
            "resolved_at": (
                self.resolved_at.isoformat() if self.resolved_at else None
            ),
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
    # Snapshot of the box's condition - re-apply restores it. NULL = good.
    condition: Mapped[str | None] = mapped_column(String(20))

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


class OnhandLog(Base):
    """One row per SKU per on-hand CHANGE the nightly sync observed
    (Nick, 2026-09-02): the memory behind "raised from 0 to 2 at
    receiving" in the Inventory Check window. Rows are written only
    when the fetched value differs from the newest row, so the log
    stays small; movement is explained by joining the delta with the
    sold ledger and receiving receipts inside the same span."""

    __tablename__ = "rfid_onhand_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    on_hand: Mapped[int] = mapped_column(Integer, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    source: Mapped[str | None] = mapped_column(String(40))

    def as_dict(self) -> dict:
        return {
            "sku": self.sku,
            "on_hand": self.on_hand,
            "observed_at": (
                self.observed_at.isoformat() if self.observed_at else None
            ),
            "source": self.source,
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
    # These two double as generic History payload slots (event detail
    # strings, not just barcodes) - 255 so summaries stop getting chopped
    # (widened from 64 on 2026-09-15, dev/alter_perf_and_widen.py).
    old_barcode: Mapped[str | None] = mapped_column(String(255))
    new_barcode: Mapped[str] = mapped_column(String(255), index=True)

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
    other_bins: Mapped[str | None] = mapped_column(String(500))
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

    # "companion" = the label for box 2..N of a multi-box unit: printing
    # it registers a CompanionTag (recognized, counted nowhere) instead
    # of an RfidAssignment. NULL = a normal counting label.
    kind: Mapped[str | None] = mapped_column(String(20))

    requested_by: Mapped[str | None] = mapped_column(String(100))
    error: Mapped[str | None] = mapped_column(String(1000))

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
            "kind": self.kind,
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
    # --- ShipStation sourcing (2026-09-23) ---------------------------------
    # Which feed proved this sale. NULL/'shopify' = the Shopify fulfilled-
    # orders feed (the original source, kept as the fallback and the
    # gap-filler for orders shipped outside ShipStation); 'shipstation' =
    # a real label off the store's ShipStation account; 'ss-manual' = a
    # manual (non-Shopify) ShipStation order - stock that leaves the shelf
    # WITHOUT Shopify's on-hand moving, which the expected-count math then
    # correctly surfaces as an on-hand that needs lowering.
    # These columns are added to existing databases by init_db()'s
    # idempotent column upgrade (database.py), sqlite and Azure SQL both.
    source: Mapped[str | None] = mapped_column(String(16))
    # ShipStation's numeric orderId, the merge key across parcels.
    ss_order_id: Mapped[str | None] = mapped_column(String(32), index=True)
    # JSON {shipmentId: units} - which labels this row's quantity stands
    # on. Makes void handling exact and idempotent: a voided label's id
    # is removed and the quantity recomputed, a re-ship adds a new id.
    ss_shipments: Mapped[str | None] = mapped_column(String(2000))
    # The order line's total units (fetched only when a second parcel
    # shows up for the same order+SKU): the cap that stops overlapping
    # parcel item lists from double-counting a reprinted label.
    ss_line_qty: Mapped[int | None] = mapped_column(Integer)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "order_id": self.order_id,
            "order_name": self.order_name,
            "sku": self.sku,
            "quantity": self.quantity,
            "retired": self.retired,
            "source": self.source or "shopify",
            "fulfilled_at": (
                self.fulfilled_at.isoformat() if self.fulfilled_at else None
            ),
            "created_at": (
                self.created_at.isoformat() if self.created_at else None
            ),
        }


class StockSnapshot(Base):
    """One observed set of Shopify stock buckets for a SKU, kept forever.
    Shopify's admin only shows ~6 months of adjustment history and no API
    serves bucket values AT a past moment, so the app records its own:
    every time it fetches a live breakdown and any bucket differs from the
    SKU's newest stored row, one row lands here (identical reads store
    nothing - that is the whole storage optimization). The Shopify-info
    tab's hover-diffs anchor their estimates on these rows, and the
    estimates sharpen as snapshots accumulate."""

    __tablename__ = "rfid_stock_snapshots"
    __table_args__ = (
        Index("ix_stock_snap_sku_at", "sku", "taken_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(100), nullable=False)
    taken_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    available: Mapped[int | None] = mapped_column(Integer)
    committed: Mapped[int | None] = mapped_column(Integer)
    on_hand: Mapped[int | None] = mapped_column(Integer)
    unavailable: Mapped[int | None] = mapped_column(Integer)


class BinAudit(Base):
    """One COMPLETED audit of a bin (scored-queue anchor, 2026-09-28):
    the moment the shelf was last squared with the records, and the
    per-SKU heard counts at that moment. The audit queue's per-product
    diff walks forward from here - |on-hand − (heard here − sold since
    + received since)| - and "sold since" always means "since this
    row". One row per completed audit, newest wins; never edited."""

    __tablename__ = "rfid_bin_audits"

    id: Mapped[int] = mapped_column(primary_key=True)
    bin: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    audited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    audited_by: Mapped[str | None] = mapped_column(String(100))
    # JSON {"SKU": heard_count} for every product the audit covered.
    baseline: Mapped[str | None] = mapped_column(Text)

    def baseline_map(self) -> dict:
        import json as _json
        try:
            return {
                (k or "").upper(): int(v)
                for k, v in _json.loads(self.baseline or "{}").items()
            }
        except Exception:  # noqa: BLE001 — a bad row anchors nothing
            return {}


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
    other_bins: Mapped[str | None] = mapped_column(String(500))
    serial_prefix: Mapped[str | None] = mapped_column(String(8))
    label_name: Mapped[str | None] = mapped_column(String(255))
    image_url: Mapped[str | None] = mapped_column(String(1000))

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
    # "Part of a set" mark (Nick, 2026-09-15, the multi-box redo): a
    # lightweight note taken AT COLLECT (C72 or web) - the master SKU
    # plus "Box X of Y" - and dealt with on the WEB during
    # verification, where the marks seed the set builder. A mark never
    # resolves anything by itself. Prod needs dev/alter_add_setmarks.py.
    set_mark_master: Mapped[str | None] = mapped_column(String(100))
    set_mark_box: Mapped[int | None] = mapped_column(Integer)
    set_mark_total: Mapped[int | None] = mapped_column(Integer)

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
            "set_mark_master": self.set_mark_master,
            "set_mark_box": self.set_mark_box,
            "set_mark_total": self.set_mark_total,
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
    # Indexed: the barcode equality test is the FIRST query of every
    # scan-station lookup (prod index via dev/alter_perf_and_widen.py).
    barcode: Mapped[str | None] = mapped_column(String(64), index=True)
    product_title: Mapped[str | None] = mapped_column(String(255))
    variant_title: Mapped[str | None] = mapped_column(String(255))
    shopify_variant_id: Mapped[str | None] = mapped_column(String(64))
    shopify_product_id: Mapped[str | None] = mapped_column(String(300))
    bin: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    # One row per bin: a product split across shelves ("G2-1 & B17") gets a
    # row for each, and each row names the others here.
    other_bins: Mapped[str | None] = mapped_column(String(500))
    # SHELF-expected units: Shopify on_hand MINUS the Unavailable bucket
    # (reserved/damaged/safety/QC) since 2026-09-01 (Nick, W9160A) - the
    # unavailable count rides along so audits can EXPLAIN an over-count.
    # Prod needs dev/alter_add_binmap_unavailable.py.
    qty: Mapped[int | None] = mapped_column(Integer)
    unavailable: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    image_url: Mapped[str | None] = mapped_column(String(1000))
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
    # --- 2026-09-24 (Nick's four-box label editor) -------------------------
    # What the barcode block encodes: NULL/"auto" = the product barcode
    # (SKU when none on file, the long-standing fallback), "sku" = always
    # the SKU. Applied when the agent CLAIMS a job, so edits reach even
    # already-queued labels.
    barcode_mode: Mapped[str | None] = mapped_column(String(10))
    # Free-text override for WHAT the barcode encodes (2026-09-24 round
    # 4: the box became editable, not just a toggle). NULL = barcode_mode
    # decides (product barcode, or the SKU).
    barcode_text: Mapped[str | None] = mapped_column(String(64))
    # Custom bin-line VALUE (the "BIN: " prefix stays the printer's);
    # NULL = the product's real bin, as always.
    bin_text: Mapped[str | None] = mapped_column(String(100))
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


class MislabelFlag(Base):
    """The VENDOR printed the wrong barcode on this product's boxes
    (Nick, 2026-09-08: EXOS2CWB5's barcode on EXOS2CW 10lb boxes, so
    scans resolve to the 5lb variant). The flag warns on EVERY scan,
    everywhere - it rides the scan-note channel, so the Scan Station
    card and every C72 surface that shows notes says "check the
    physical product" before anyone trusts the resolution."""

    __tablename__ = "rfid_mislabel_flags"

    sku: Mapped[str] = mapped_column(String(100), primary_key=True)
    set_by: Mapped[str | None] = mapped_column(String(100))
    # The products this label might ACTUALLY be (Nick, 2026-09-08):
    # newline-separated SKUs. When a flagged product is scanned, every
    # surface offers a picker across [this SKU] + these, so the operator
    # names what's physically in hand instead of trusting the barcode.
    # Prod needs dev/alter_add_flag_kinds.py.
    alt_skus: Mapped[str | None] = mapped_column(Text)
    set_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def alt_list(self) -> list[str]:
        return [s.strip() for s in (self.alt_skus or "").splitlines()
                if s.strip()]


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
    # Two strengths (Nick, 2026-09-08, the CR2032 assorted box):
    #   "non-taggable"    — fully outside RFID; no labels at all.
    #   "unlabelable-box" — the BOX gets ONE label + a bin for location
    #                       and clarity; on-hand still shows and can be
    #                       updated; per-unit tag counts never happen.
    # Both kinds skip batches, receiving labels, and the tags-vs-on-hand
    # arithmetic. Prod needs dev/alter_add_flag_kinds.py.
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default="non-taggable",
        server_default="non-taggable",
    )
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


class BundleInfo(Base):
    """Bundle-level facts beside the per-component rows: the listing's
    title (bundles rarely sit in the bin map, so nothing else knows it)
    and where the definition came from - "app" (the bundles.app pull or
    the per-SKU Shopify import) or "manual" (typed on the terminal). A
    re-pull refreshes "app" bundles and leaves "manual" ones alone."""

    __tablename__ = "rfid_bundles"

    bundle_sku: Mapped[str] = mapped_column(String(100), primary_key=True)
    title: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(16), nullable=False,
                                        default="manual")
    synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    updated_by: Mapped[str | None] = mapped_column(String(100))

    def as_dict(self) -> dict:
        return {
            "bundle_sku": self.bundle_sku,
            "title": self.title,
            "source": self.source,
            "synced_at": (
                self.synced_at.isoformat() if self.synced_at else None
            ),
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
    note: Mapped[str] = mapped_column(String(1000), nullable=False)
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
    # Newline-joined SPECIFIC EPCs to hunt (Nick, 2026-09-08: the audit
    # queues the SILENT tags - hunting every tag of the SKU let the
    # on-shelf boxes drown out the missing one). Empty = hunt them all.
    epcs: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def epc_list(self) -> list[str]:
        return [e.strip().upper() for e in (self.epcs or "").split("\n")
                if e.strip()]


class AuditUnsure(Base):
    """A silent tag the auditor couldn't call (Nick, 2026-09-29): marked
    UNSURE on the C72 with an optional note, worked later from the web
    Audits hub's "Marked unsure" list (unpair / sold / locate / dismiss).
    One open row per EPC; a row whose tag is no longer active resolves
    itself the next time the list is read."""

    __tablename__ = "rfid_audit_unsure"

    id: Mapped[int] = mapped_column(primary_key=True)
    epc: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    sku: Mapped[str | None] = mapped_column(String(100))
    product_title: Mapped[str | None] = mapped_column(String(255))
    bin: Mapped[str | None] = mapped_column(String(100))
    note: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # open | resolved
    status: Mapped[str] = mapped_column(String(16), nullable=False,
                                        default="open")
    resolution: Mapped[str | None] = mapped_column(String(32))
    resolved_by: Mapped[str | None] = mapped_column(String(100))
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class AuditUnavailNote(Base):
    """"This product's unavailable units aren't on this shelf" (Nick,
    2026-09-29, F9160A): the C72's one-tap answer when Shopify's
    Unavailable bucket explains an audit's shortfall. The check reads
    the notes left since the bin's last completed audit and treats that
    many units as set aside elsewhere - the card goes green when that
    was the only difference."""

    __tablename__ = "rfid_audit_unavail_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    bin: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    qty: Mapped[int] = mapped_column(nullable=False, default=0)
    note: Mapped[str | None] = mapped_column(String(500))
    created_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuditStockConfirm(Base):
    """"There are N of this product on this shelf" (Nick, 2026-09-29,
    F9172D): the C72's Resolve on an in-range "never got a label" flag.
    The check reads the newest one per SKU since the bin's last completed
    audit; labels owed become N minus the tags on file, so a shelf the
    operator confirmed stops asking for labels it doesn't need. Local
    only - never a stock write."""

    __tablename__ = "rfid_audit_stock_confirms"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    bin: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    qty: Mapped[int] = mapped_column(nullable=False, default=0)
    created_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RfidUser(Base):
    """Who may sign in to the web terminal (Nick, 2026-10-05) and the
    names the gun offers at launch. One row per email; Microsoft
    (telescopescanada.ca) or Google signs them in, this list decides
    whether they get past the login page. Everyone is admin for now
    ("we're all trusted"); the role column is here for later. prefs
    will hold per-user hub layouts once hubs become customizable.
    Created by init_db's create_all - no ALTER needed on prod."""

    __tablename__ = "rfid_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(
        String(200), unique=True, index=True, nullable=False
    )
    name: Mapped[str | None] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(
        String(20), nullable=False, default="admin", server_default="admin"
    )
    # Which service last signed this person in, and its stable subject id.
    provider: Mapped[str | None] = mapped_column(String(20))
    subject: Mapped[str | None] = mapped_column(String(200))
    # The Shopify staff id (session token "sub") linked to this person,
    # so the embedded app knows who's clicking without a second login.
    shopify_sub: Mapped[str | None] = mapped_column(String(100), index=True)
    added_by: Mapped[str | None] = mapped_column(String(100))
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    prefs: Mapped[str | None] = mapped_column(Text)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "role": self.role,
            "provider": self.provider,
            "shopify_linked": bool(self.shopify_sub),
            "added_by": self.added_by,
            "added_at": self.added_at.isoformat() if self.added_at else None,
            "last_seen_at": (
                self.last_seen_at.isoformat() if self.last_seen_at else None
            ),
        }


class AppSetting(Base):
    """Server-stored key/value switches the web UI can flip without an
    app-settings change (no restart, no az CLI). First user: the 1-left
    auto-confirm pause switch."""

    __tablename__ = "rfid_app_settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(String(2000), nullable=False)
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
    # The bin or rack this sweep audited (Nick, 2026-09-28): audit
    # checks stamp it, so each location keeps its own sweep history -
    # "open a bin, see its latest audit" on the gun and the web.
    # Uppercased. Added to prod by init_db's idempotent upgrade.
    bin: Mapped[str | None] = mapped_column(String(100), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # Stamped when the packed-orders audit retired this sweep's tags
    # (Nick, 2026-09-14): the sweep can only be spent ONCE - the
    # listing shows the stamp instead of buttons, and the History undo
    # clears it. New columns: dev/alter_add_packed_retired.py on prod.
    packed_retired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    packed_retired_by: Mapped[str | None] = mapped_column(String(100))

    def as_dict(self, with_epcs: bool = False) -> dict:
        d = {
            "id": self.id,
            "device": self.device,
            "note": self.note,
            "batch_id": self.batch_id,
            "bin": self.bin,
            "epc_count": self.epc_count,
            "created_at": (
                self.created_at.isoformat() if self.created_at else None
            ),
            "packed_retired_at": (
                self.packed_retired_at.isoformat()
                if self.packed_retired_at else None
            ),
            "packed_retired_by": self.packed_retired_by,
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
    detail: Mapped[str] = mapped_column(String(1000), nullable=False)
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


class PackScan(Base):
    """One box scanned at the packing desk (phase 6, 2026-09-28):
    allocated against ShipStation's awaiting-shipment orders, flipped
    to shipped when the hourly sync sees the label - at which point an
    RFID-scanned row retires its EXACT tag (no last-heard inference).
    A working surface, not history: rows sweep after a few days, and
    the retirements they trigger do their own History logging."""

    __tablename__ = "rfid_pack_scans"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(200), nullable=False)
    epc: Mapped[str | None] = mapped_column(String(128), index=True)
    sku: Mapped[str | None] = mapped_column(String(100), index=True)
    product_title: Mapped[str | None] = mapped_column(String(255))
    # allocated / duplicate / not-in-shipping / unknown / shipped
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    ss_order_id: Mapped[str | None] = mapped_column(String(32), index=True)
    order_number: Mapped[str | None] = mapped_column(String(32))
    scanned_by: Mapped[str | None] = mapped_column(String(100))
    device: Mapped[str | None] = mapped_column(String(100))
    scanned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    shipped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "code": self.code,
            "epc": self.epc,
            "sku": self.sku,
            "product_title": self.product_title,
            "status": self.status,
            "ss_order_id": self.ss_order_id,
            "order_number": self.order_number,
            "scanned_by": self.scanned_by,
            "device": self.device,
            "scanned_at": (
                self.scanned_at.isoformat() if self.scanned_at else None
            ),
            "shipped_at": (
                self.shipped_at.isoformat() if self.shipped_at else None
            ),
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


class BoxPhoto(Base):
    """One photo from the gun's Box photos collector (Steve, 2026-10-07):
    a Svbony box with its SKU printed in plain text, read by Azure on
    upload and filed into a SKU folder. The photos are the training set
    for the camera SKU reader, so an "auto" filing only counts as a
    label once a person confirms it. Files live on disk (BOX_PHOTO_DIR);
    this row is the folder record."""

    __tablename__ = "rfid_box_photos"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The gun's own id for the shot, so a retried upload never doubles.
    uid: Mapped[str] = mapped_column(
        String(40), unique=True, index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    worker: Mapped[str | None] = mapped_column(String(100))
    batch_id: Mapped[int | None] = mapped_column(Integer, index=True)
    file_name: Mapped[str] = mapped_column(String(200), nullable=False)
    # auto | ask | none | confirmed | deleted
    status: Mapped[str] = mapped_column(String(12), index=True, nullable=False)
    sku: Mapped[str | None] = mapped_column(String(100), index=True)
    # JSON list of {sku, title, score} the reader offered.
    guesses: Mapped[str | None] = mapped_column(Text)
    ocr_text: Mapped[str | None] = mapped_column(Text)
    ocr_error: Mapped[str | None] = mapped_column(String(255))
    # A "Read the box" shot is a new box; a "Keep in SKU" shot is
    # another angle of one already counted.
    new_box: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    # Set once the folder went to the web sorter (Send to sorter).
    handoff_id: Mapped[int | None] = mapped_column(Integer, index=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    confirmed_by: Mapped[str | None] = mapped_column(String(100))
    # Focus telemetry from the gun: the lens distance in diopters
    # (1 / metres; 4.0 = 25 cm), ms from focus start to lock, and
    # locked | not_locked | timeout | continuous.
    focus_diopters: Mapped[float | None] = mapped_column(Float)
    af_ms: Mapped[int | None] = mapped_column(Integer)
    af_result: Mapped[str | None] = mapped_column(String(16))
    focus_mode: Mapped[str | None] = mapped_column(String(16))
    # The physical box (one per Next box on the gun): box counts come
    # from these, so deleting or moving a photo never changes them.
    box_uid: Mapped[str | None] = mapped_column(String(40), index=True)
    # The operator's verdict: good | blurry | angle (training data).
    quality: Mapped[str | None] = mapped_column(String(12))
    # Where each line and word sat in the photo, with Azure's confidence
    # (JSON) - for cutting out SKU lines to train a reader on the gun.
    ocr_layout: Mapped[str | None] = mapped_column(Text)
    # The reader's first answer at upload, kept apart from corrections.
    read_status: Mapped[str | None] = mapped_column(String(12))
    read_sku: Mapped[str | None] = mapped_column(String(100))
    torch: Mapped[bool | None] = mapped_column(Boolean)
    app_version: Mapped[str | None] = mapped_column(String(20))


class BoxPhotoHandoff(Base):
    """A shipment's confirmed boxes sent from the gun to the web sorter
    (Batch tab > Sort a shipment), which loads them like scans. Svbony
    rarely ships what the stock order says, so the sorter decides which
    order each box belongs to."""

    __tablename__ = "rfid_box_photo_handoffs"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    worker: Mapped[str | None] = mapped_column(String(100))
    batch_id: Mapped[int | None] = mapped_column(Integer)
    # JSON list of {sku, title, qty}.
    items: Mapped[str] = mapped_column(Text, nullable=False)
    boxes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    loaded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    loaded_by: Mapped[str | None] = mapped_column(String(100))
    dismissed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class DevLogLine(Base):
    """A line the gun's remote debug link sent (Settings > DEVELOPER >
    Remote debugging, 2026-10-07): app events, camera steps, crashes,
    stalls. Pruned to the newest few thousand."""

    __tablename__ = "rfid_dev_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    device: Mapped[str | None] = mapped_column(String(100), index=True)
    line: Mapped[str] = mapped_column(Text, nullable=False)


class DevCommand(Base):
    """A command queued for the gun's debug link (screenshot, logcat,
    view dump, camera restart). The gun claims it, runs it and posts
    the result; a screenshot lands on disk beside the photos."""

    __tablename__ = "rfid_dev_commands"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # "*" = whichever gun asks first.
    device: Mapped[str] = mapped_column(String(100), nullable=False)
    cmd: Mapped[str] = mapped_column(String(40), nullable=False)
    taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    taken_by: Mapped[str | None] = mapped_column(String(100))
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[str | None] = mapped_column(Text)
    has_shot: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
