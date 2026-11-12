"""Perpetual inventory with moving-average landed cost."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from core.utils import ZERO, cost, money

from .models import StockAdjustment, StockMove, StockQuant, StockTransfer


class InsufficientStock(ValidationError):
    pass


def _quant(product, warehouse):
    quant, _ = StockQuant.objects.select_for_update().get_or_create(product=product, warehouse=warehouse)
    return quant


def total_on_hand(product):
    return StockQuant.objects.filter(product=product).aggregate(t=Sum("quantity"))["t"] or ZERO


def receive_stock(product, warehouse, qty, unit_cost, date, reference="", move_type=StockMove.RECEIPT):
    """Add stock and update the product's moving average cost."""
    if qty <= 0:
        raise ValidationError("Receipt quantity must be positive.")
    product = type(product).objects.select_for_update().get(pk=product.pk)
    on_hand = total_on_hand(product)
    if on_hand > 0:
        product.avg_cost = cost((on_hand * product.avg_cost + qty * unit_cost) / (on_hand + qty))
    else:
        product.avg_cost = cost(unit_cost)
    product.save(update_fields=["avg_cost"])
    quant = _quant(product, warehouse)
    quant.quantity += qty
    quant.save(update_fields=["quantity"])
    return StockMove.objects.create(date=date, product=product, warehouse=warehouse, quantity=qty,
                                    unit_cost=cost(unit_cost), move_type=move_type, reference=reference)


def issue_stock(product, warehouse, qty, date, reference="", move_type=StockMove.SHIPMENT, allow_negative=False):
    """Remove stock at current average cost. Returns the unit cost used."""
    if qty <= 0:
        raise ValidationError("Issue quantity must be positive.")
    product = type(product).objects.select_for_update().get(pk=product.pk)
    quant = _quant(product, warehouse)
    if quant.quantity < qty and not allow_negative:
        raise InsufficientStock(
            f"Insufficient stock for {product.sku} in {warehouse.code}: on hand {quant.quantity}, needed {qty}."
        )
    quant.quantity -= qty
    quant.save(update_fields=["quantity"])
    StockMove.objects.create(date=date, product=product, warehouse=warehouse, quantity=-qty,
                             unit_cost=product.avg_cost, move_type=move_type, reference=reference)
    return product.avg_cost


def revalue_stock(product, amount, received_qty):
    """Capitalize a late cost (e.g. a landed cost billed after receipt) into on-hand stock.

    Only the share still on hand is capitalized; the remainder relates to goods already sold.
    Returns the capitalized amount.
    """
    product = type(product).objects.select_for_update().get(pk=product.pk)
    on_hand = total_on_hand(product)
    if on_hand <= 0 or received_qty <= 0:
        return ZERO
    share = min(on_hand / received_qty, 1)
    capital = money(amount * share)
    product.avg_cost = cost(product.avg_cost + capital / on_hand)
    product.save(update_fields=["avg_cost"])
    return capital


@transaction.atomic
def post_adjustment(adjustment, user=None):
    from accounting.services import acct, post_journal

    adjustment = StockAdjustment.objects.select_for_update().get(pk=adjustment.pk)
    if adjustment.status != StockAdjustment.DRAFT:
        raise ValidationError("Adjustment already posted.")
    lines = list(adjustment.lines.select_related("product"))
    if not lines:
        raise ValidationError("Adjustment has no lines.")
    value = ZERO
    for line in lines:
        if line.quantity > 0:
            unit = line.unit_cost if line.unit_cost is not None else line.product.avg_cost
            receive_stock(line.product, adjustment.warehouse, line.quantity, unit, adjustment.date,
                          adjustment.number, StockMove.ADJUSTMENT)
            value += money(line.quantity * unit)
        elif line.quantity < 0:
            unit = issue_stock(line.product, adjustment.warehouse, -line.quantity, adjustment.date,
                               adjustment.number, StockMove.ADJUSTMENT)
            value -= money(-line.quantity * unit)
    offset = acct("OPENING") if adjustment.reason == "opening" else acct("INV_ADJ")
    post_journal(
        adjustment.date,
        f"Stock adjustment {adjustment.number} ({adjustment.get_reason_display()})",
        [{"account": acct("INVENTORY"), "debit": value}, {"account": offset, "credit": value}],
        "stock_adjustment", adjustment.number, user,
    )
    adjustment.status = StockAdjustment.POSTED
    adjustment.save(update_fields=["status"])
    return adjustment


@transaction.atomic
def post_transfer(transfer, user=None):
    transfer = StockTransfer.objects.select_for_update().get(pk=transfer.pk)
    if transfer.status != StockTransfer.DRAFT:
        raise ValidationError("Transfer already posted.")
    if transfer.from_warehouse_id == transfer.to_warehouse_id:
        raise ValidationError("Source and destination warehouses must differ.")
    lines = list(transfer.lines.select_related("product"))
    if not lines:
        raise ValidationError("Transfer has no lines.")
    for line in lines:
        quant = _quant(line.product, transfer.from_warehouse)
        if quant.quantity < line.quantity:
            raise InsufficientStock(f"Insufficient stock for {line.product.sku} in {transfer.from_warehouse.code}.")
        quant.quantity -= line.quantity
        quant.save(update_fields=["quantity"])
        dest = _quant(line.product, transfer.to_warehouse)
        dest.quantity += line.quantity
        dest.save(update_fields=["quantity"])
        for wh, q, mt in ((transfer.from_warehouse, -line.quantity, StockMove.TRANSFER_OUT),
                          (transfer.to_warehouse, line.quantity, StockMove.TRANSFER_IN)):
            StockMove.objects.create(date=transfer.date, product=line.product, warehouse=wh, quantity=q,
                                     unit_cost=line.product.avg_cost, move_type=mt, reference=transfer.number)
    transfer.status = StockTransfer.POSTED
    transfer.save(update_fields=["status"])
    return transfer
