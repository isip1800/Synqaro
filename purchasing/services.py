"""Purchasing and import management: PO lifecycle, shipments, landed costs and receiving."""
from collections import defaultdict

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.utils import ZERO, cost, money

from .models import (
    GoodsReceipt,
    GoodsReceiptLine,
    LandedCost,
    PurchaseOrder,
    PurchaseOrderLine,
    Shipment,
    ShipmentLine,
)


def send_po(po):
    if po.status != PurchaseOrder.DRAFT:
        raise ValidationError("Only draft POs can be sent.")
    if not po.lines.exists():
        raise ValidationError("PO has no lines.")
    po.status = PurchaseOrder.SENT
    po.save(update_fields=["status"])
    return po


def confirm_po(po, ship_date=None, vendor_reference="", notes="", by=""):
    """Vendor (or buyer on the vendor's behalf) confirms the PO."""
    if po.status not in (PurchaseOrder.SENT, PurchaseOrder.DRAFT):
        raise ValidationError("PO cannot be confirmed in its current status.")
    po.status = PurchaseOrder.CONFIRMED
    po.vendor_confirmed_at = timezone.now()
    po.vendor_confirmed_ship_date = ship_date
    if vendor_reference:
        po.vendor_reference = vendor_reference
    po.vendor_notes = (f"[{by}] " if by else "") + (notes or "")
    po.save()
    return po


def cancel_po(po):
    if po.lines.filter(qty_received__gt=0).exists():
        raise ValidationError("Goods have been received on this PO; close it instead.")
    if ShipmentLine.objects.filter(po_line__order=po).exclude(shipment__status=Shipment.CANCELLED).exists():
        raise ValidationError("PO is on an active shipment.")
    po.status = PurchaseOrder.CANCELLED
    po.save(update_fields=["status"])


def close_po(po):
    if po.status not in (PurchaseOrder.PARTIAL, PurchaseOrder.RECEIVED, PurchaseOrder.CONFIRMED,
                         PurchaseOrder.SENT):
        raise ValidationError("PO cannot be closed in its current status.")
    po.status = PurchaseOrder.CLOSED
    po.save(update_fields=["status"])


def create_shipment_for_po(po, **fields):
    """Create a planned shipment carrying every unshipped quantity on the PO."""
    if po.status not in PurchaseOrder.OPEN_STATUSES:
        raise ValidationError("PO must be sent or confirmed before booking a shipment.")
    shipment = Shipment.objects.create(vendor=po.vendor, incoterm=po.incoterm, warehouse=po.warehouse, **fields)
    for line in po.lines.all():
        if line.qty_unshipped > 0:
            ShipmentLine.objects.create(shipment=shipment, po_line=line, quantity=line.qty_unshipped)
    if not shipment.lines.exists():
        raise ValidationError("Everything on this PO is already booked on shipments.")
    return shipment


def validate_shipment_lines(shipment):
    for line in shipment.lines.select_related("po_line__order"):
        po = line.po_line.order
        if po.status not in PurchaseOrder.OPEN_STATUSES:
            raise ValidationError(f"{po.number} is not open (status {po.get_status_display()}).")
        if shipment.vendor_id and po.vendor_id != shipment.vendor_id:
            raise ValidationError(f"{po.number} is from a different vendor than the shipment.")
        if line.quantity <= 0:
            raise ValidationError("Shipment quantities must be positive.")
        if line.quantity > line.po_line.qty_open:
            raise ValidationError(f"{line.po_line}: shipping {line.quantity} exceeds open qty {line.po_line.qty_open}.")


def _basis(line, method):
    product = line.po_line.product
    if method == Shipment.ALLOC_QTY:
        return line.quantity
    if method == Shipment.ALLOC_WEIGHT:
        return line.quantity * product.weight_kg
    if method == Shipment.ALLOC_VOLUME:
        return line.quantity * product.volume_cbm
    return line.quantity * line.po_line.unit_price * line.po_line.order.exchange_rate


def allocate(shipment, amount, lines=None):
    """Split `amount` across shipment lines by the shipment's allocation method. Exact to the cent."""
    lines = lines if lines is not None else list(shipment.lines.select_related("po_line__product",
                                                                               "po_line__order"))
    if not lines:
        return {}
    method = shipment.allocation_method
    bases = {line.pk: _basis(line, method) for line in lines}
    total = sum(bases.values(), ZERO)
    if total <= 0:  # e.g. weights not maintained: fall back to value
        bases = {line.pk: _basis(line, Shipment.ALLOC_VALUE) for line in lines}
        total = sum(bases.values(), ZERO)
    result, running = {}, ZERO
    for i, line in enumerate(lines):
        if i == len(lines) - 1:
            share = money(amount) - running
        else:
            share = money(amount * bases[line.pk] / total) if total else ZERO
        result[line.pk] = share
        running += share
    return result


def estimate_duty(shipment):
    """Create/refresh a customs duty landed cost from product duty rates (HS-code based)."""
    if shipment.status == Shipment.RECEIVED:
        raise ValidationError("Shipment already received; add duty as a landed cost instead.")
    duty = tariff = ZERO
    for line in shipment.lines.select_related("po_line__product", "po_line__order"):
        duty += line.value_base * line.po_line.product.duty_rate / 100
        tariff += line.value_base * line.po_line.product.tariff_rate / 100
    LandedCost.objects.filter(shipment=shipment, cost_type__in=["duty", "tariff"],
                              description__in=["Estimated duty", "Estimated tariff"], capitalized=False).delete()
    result = None
    if money(tariff) > 0:
        result = LandedCost.objects.create(shipment=shipment, cost_type="tariff", description="Estimated tariff",
                                           amount=money(tariff))
    if money(duty) > 0:
        result = LandedCost.objects.create(shipment=shipment, cost_type="duty", description="Estimated duty",
                                           amount=money(duty))
    return result


@transaction.atomic
def receive_shipment(shipment, date, user=None):
    """Receive all goods on a shipment, capitalizing allocated landed costs into inventory."""
    from accounting.services import acct, post_journal
    from inventory.services import receive_stock

    shipment = Shipment.objects.select_for_update().get(pk=shipment.pk)
    if shipment.status in (Shipment.RECEIVED, Shipment.CANCELLED):
        raise ValidationError("Shipment is already received or cancelled.")
    lines = list(shipment.lines.select_related("po_line__product", "po_line__order"))
    if not lines:
        raise ValidationError("Shipment has no lines.")
    validate_shipment_lines(shipment)

    landed = list(shipment.landed_costs.filter(capitalized=False))
    landed_total = sum((lc.amount for lc in landed), ZERO)
    alloc = allocate(shipment, landed_total, lines)

    pos = {line.po_line.order_id for line in lines}
    receipt = GoodsReceipt.objects.create(
        purchase_order=lines[0].po_line.order if len(pos) == 1 else None,
        shipment=shipment, warehouse=shipment.warehouse, date=date, created_by=user,
    )
    goods_total = ZERO
    for line in lines:
        pol = PurchaseOrderLine.objects.select_for_update().get(pk=line.po_line_id)
        unit_goods = cost(pol.unit_price * pol.order.exchange_rate)
        unit_landed = cost(alloc[line.pk] / line.quantity)
        receive_stock(pol.product, shipment.warehouse, line.quantity, unit_goods + unit_landed, date,
                      receipt.number)
        GoodsReceiptLine.objects.create(receipt=receipt, po_line=pol, product=pol.product,
                                        quantity=line.quantity, unit_goods_cost=unit_goods,
                                        unit_landed_cost=unit_landed)
        goods_total += money(line.quantity * pol.unit_price * pol.order.exchange_rate)
        pol.qty_received += line.quantity
        pol.save(update_fields=["qty_received"])
        line.allocated_landed_cost = alloc[line.pk]
        line.unit_landed_cost = unit_landed
        line.save(update_fields=["allocated_landed_cost", "unit_landed_cost"])

    receipt.journal_entry = post_journal(
        date, f"Receipt {receipt.number} — shipment {shipment.number}",
        [
            {"account": acct("INVENTORY"), "debit": goods_total + landed_total,
             "description": "Goods + landed costs"},
            {"account": acct("GRNI"), "credit": goods_total, "description": "Goods received not invoiced"},
            {"account": acct("ACCRUED_LANDED"), "credit": landed_total, "description": "Accrued landed costs"},
        ],
        "goods_receipt", receipt.number, user,
    )
    receipt.save(update_fields=["journal_entry"])
    for lc in landed:
        lc.capitalized = True
        lc.journal_entry = receipt.journal_entry
        lc.save(update_fields=["capitalized", "journal_entry"])
    shipment.status = Shipment.RECEIVED
    shipment.received_on = date
    shipment.save(update_fields=["status", "received_on"])
    for po in PurchaseOrder.objects.filter(pk__in=pos):
        po.refresh_receipt_status()
    return receipt


@transaction.atomic
def receive_po(po, quantities, date, warehouse=None, user=None):
    """Direct receipt against a PO (domestic purchases / no shipment tracking). quantities: {line_id: qty}."""
    from accounting.services import acct, post_journal
    from inventory.services import receive_stock

    po = PurchaseOrder.objects.select_for_update().get(pk=po.pk)
    if po.status not in PurchaseOrder.OPEN_STATUSES:
        raise ValidationError("PO must be sent/confirmed to receive goods.")
    warehouse = warehouse or po.warehouse
    receipt = None
    goods_total = ZERO
    for line in po.lines.select_related("product"):
        qty = quantities.get(line.pk) or ZERO
        if qty <= 0:
            continue
        if qty > line.qty_open:
            raise ValidationError(f"{line.product.sku}: receiving {qty} exceeds open qty {line.qty_open}.")
        if receipt is None:
            receipt = GoodsReceipt.objects.create(purchase_order=po, warehouse=warehouse, date=date, created_by=user)
        unit = cost(line.unit_price * po.exchange_rate)
        receive_stock(line.product, warehouse, qty, unit, date, receipt.number)
        GoodsReceiptLine.objects.create(receipt=receipt, po_line=line, product=line.product, quantity=qty,
                                        unit_goods_cost=unit)
        goods_total += money(qty * line.unit_price * po.exchange_rate)
        line.qty_received += qty
        line.save(update_fields=["qty_received"])
    if receipt is None:
        raise ValidationError("Enter a quantity to receive.")
    receipt.journal_entry = post_journal(
        date, f"Receipt {receipt.number} — {po.number}",
        [{"account": acct("INVENTORY"), "debit": goods_total},
         {"account": acct("GRNI"), "credit": goods_total}],
        "goods_receipt", receipt.number, user,
    )
    receipt.save(update_fields=["journal_entry"])
    po.refresh_receipt_status()
    return receipt


@transaction.atomic
def capitalize_late_landed_cost(landed_cost, date, user=None):
    """Post a landed cost added after the shipment was received.

    The share relating to stock still on hand raises its average cost; the rest goes to COGS.
    """
    from accounting.services import acct, post_journal
    from inventory.services import revalue_stock

    lc = LandedCost.objects.select_for_update().get(pk=landed_cost.pk)
    shipment = lc.shipment
    if lc.capitalized:
        raise ValidationError("Landed cost already capitalized.")
    if shipment.status != Shipment.RECEIVED:
        raise ValidationError("Shipment not received yet — the cost will be capitalized on receipt.")
    lines = list(shipment.lines.select_related("po_line__product", "po_line__order"))
    alloc = allocate(shipment, lc.amount, lines)
    per_product = defaultdict(lambda: [ZERO, ZERO])  # product -> [amount, qty]
    products = {}
    for line in lines:
        product = line.po_line.product
        products[product.pk] = product
        per_product[product.pk][0] += alloc[line.pk]
        per_product[product.pk][1] += line.quantity
        line.allocated_landed_cost += alloc[line.pk]
        line.save(update_fields=["allocated_landed_cost"])
    capital = ZERO
    for pid, (amount, qty) in per_product.items():
        capital += revalue_stock(products[pid], amount, qty)
    lc.journal_entry = post_journal(
        date, f"Late landed cost — {shipment.number} {lc.get_cost_type_display()}",
        [
            {"account": acct("INVENTORY"), "debit": capital, "description": "Capitalized to on-hand stock"},
            {"account": acct("COGS"), "debit": lc.amount - capital, "description": "Share of goods already sold"},
            {"account": acct("ACCRUED_LANDED"), "credit": lc.amount},
        ],
        "landed_cost", shipment.number, user,
    )
    lc.capitalized = True
    lc.save(update_fields=["capitalized", "journal_entry"])
    return lc


def create_bill_from_po(po, vendor_invoice_number, bill_date, quantities=None, portal=False):
    """Draft vendor bill for received-but-unbilled quantities (or explicit quantities)."""
    from accounting.models import VendorBill, VendorBillLine

    bill = VendorBill.objects.create(
        vendor=po.vendor, vendor_invoice_number=vendor_invoice_number, purchase_order=po, bill_date=bill_date,
        currency=po.currency, exchange_rate=po.exchange_rate, submitted_via_portal=portal,
    )
    for line in po.lines.select_related("product"):
        if quantities is not None:
            qty = quantities.get(line.pk) or ZERO
        else:
            qty = line.qty_received - line.qty_billed
        qty = min(qty, line.quantity - line.qty_billed)
        if qty > 0:
            VendorBillLine.objects.create(bill=bill, po_line=line, description=str(line.product),
                                          quantity=qty, unit_price=line.unit_price)
    if not bill.lines.exists():
        bill.delete()
        raise ValidationError("Nothing to bill on this PO.")
    return bill
