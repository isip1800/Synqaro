import datetime

from django.core.exceptions import ValidationError
from django.db import transaction

from core.utils import ZERO, money

from .models import Delivery, DeliveryLine, SalesOrder, SalesOrderLine


def credit_check(order):
    customer = order.customer
    if not customer.credit_limit:
        return None
    others = sum((o.uninvoiced_total for o in customer.sales_orders.filter(status__in=SalesOrder.OPEN_STATUSES)
                  .exclude(pk=order.pk).prefetch_related("lines")), ZERO)
    exposure = customer.balance + others + order.uninvoiced_total
    if exposure > customer.credit_limit:
        return (f"Credit limit exceeded for {customer}: limit {customer.credit_limit:,.2f}, "
                f"exposure with this order {exposure:,.2f}.")
    return None


def confirm_order(order, override_credit=False, user=None):
    """Quote -> Open. Goes to Pending approval when over the credit limit (or approval is always required)."""
    from django.utils import timezone

    from core.models import Company

    if order.status != SalesOrder.QUOTE:
        raise ValidationError("Only quotations can be confirmed.")
    if not order.lines.exists():
        raise ValidationError("Order has no lines.")
    problem = credit_check(order)
    reasons = [r for r in (problem, "Company policy: sales orders require approval."
                           if Company.get().require_so_approval else None) if r]
    if reasons and not override_credit:
        order.status = SalesOrder.PENDING
        order.approval_reason = " ".join(reasons)[:255]
        order.save(update_fields=["status", "approval_reason"])
        return order
    order.status = SalesOrder.CONFIRMED
    order.credit_override = bool(problem)
    if override_credit:
        order.approved_by = user if getattr(user, "is_authenticated", False) else None
        order.approved_at = timezone.now()
    order.save(update_fields=["status", "credit_override", "approved_by", "approved_at"])
    return order


def approve_order(order, user=None):
    """Pending approval -> Open."""
    from django.utils import timezone

    if order.status != SalesOrder.PENDING:
        raise ValidationError("Only orders pending approval can be approved.")
    order.status = SalesOrder.CONFIRMED
    order.credit_override = bool(credit_check(order))
    order.approved_by = user if getattr(user, "is_authenticated", False) else None
    order.approved_at = timezone.now()
    order.save(update_fields=["status", "credit_override", "approved_by", "approved_at"])
    return order


def reject_order(order, user=None):
    """Pending approval -> back to Quotation for changes."""
    if order.status != SalesOrder.PENDING:
        raise ValidationError("Only orders pending approval can be sent back.")
    order.status = SalesOrder.QUOTE
    order.save(update_fields=["status"])
    return order


def cancel_order(order):
    if order.lines.filter(qty_shipped__gt=0).exists():
        raise ValidationError("Goods already shipped; cannot cancel.")
    order.status = SalesOrder.CANCELLED
    order.save(update_fields=["status"])


@transaction.atomic
def create_delivery(order, quantities, date, carrier="", tracking_number="", user=None):
    """Ship goods (quantities: {so_line_id: qty}) from each line's own warehouse.

    Creates one delivery (picking slip) per warehouse; relieves stock at average cost and books COGS.
    Returns the first delivery; all deliveries are on ``order.deliveries``.
    """
    deliveries = ship_order(order, quantities, date, carrier, tracking_number, user)
    return deliveries[0]


@transaction.atomic
def ship_order(order, quantities, date, carrier="", tracking_number="", user=None):
    from accounting.services import acct, post_journal
    from inventory.services import issue_stock

    order = SalesOrder.objects.select_for_update().get(pk=order.pk)
    if order.status not in SalesOrder.SHIPPABLE_STATUSES:
        raise ValidationError("Order must be open (confirmed and approved) before shipping.")
    if order.on_hold:
        raise ValidationError(f"Order is on hold ({order.hold_reason or 'no reason given'}). Remove the hold first.")
    by_wh = {}
    for line in order.lines.select_related("product", "warehouse").order_by("warehouse__code", "id"):
        qty = quantities.get(line.pk) or ZERO
        if qty <= 0:
            continue
        if qty > line.qty_to_ship:
            raise ValidationError(f"{line.product.sku}: shipping {qty} exceeds remaining {line.qty_to_ship}.")
        by_wh.setdefault(line.warehouse, []).append((line, qty))
    if not by_wh:
        raise ValidationError("Enter a quantity to ship.")
    deliveries = []
    for warehouse, items in by_wh.items():
        delivery = Delivery.objects.create(order=order, warehouse=warehouse, date=date, carrier=carrier,
                                           tracking_number=tracking_number, created_by=user)
        total_cost = ZERO
        for line, qty in items:
            unit_cost = issue_stock(line.product, warehouse, qty, date, delivery.number)
            DeliveryLine.objects.create(delivery=delivery, so_line=line, quantity=qty, unit_cost=unit_cost)
            line_cost = money(qty * unit_cost)
            total_cost += line_cost
            line.qty_shipped += qty
            line.cost_shipped += line_cost
            line.save(update_fields=["qty_shipped", "cost_shipped"])
        delivery.journal_entry = post_journal(
            date, f"COGS {delivery.number} — {order.number} ({warehouse.code})",
            [{"account": acct("COGS"), "debit": total_cost}, {"account": acct("INVENTORY"), "credit": total_cost}],
            "delivery", delivery.number, user,
        )
        delivery.save(update_fields=["journal_entry"])
        deliveries.append(delivery)
    order.refresh_status()
    return deliveries


def create_invoice_from_order(order, invoice_date):
    """Draft invoice for everything shipped but not yet invoiced."""
    from accounting.models import CustomerInvoice, CustomerInvoiceLine

    lines = [line for line in order.lines.select_related("product") if line.qty_to_invoice > 0]
    if not lines:
        raise ValidationError("Nothing shipped is waiting to be invoiced.")
    if CustomerInvoice.objects.filter(sales_order=order, status=CustomerInvoice.DRAFT).exists():
        raise ValidationError("A draft invoice already exists for this order.")
    inv = CustomerInvoice.objects.create(
        customer=order.customer, sales_order=order, sales_rep=order.sales_rep, invoice_date=invoice_date,
        due_date=invoice_date + datetime.timedelta(days=order.customer.payment_terms_days),
        tax_rate=order.tax_rate, notes=f"Customer PO: {order.customer_po}" if order.customer_po else "",
    )
    for line in lines:
        CustomerInvoiceLine.objects.create(
            invoice=inv, so_line=line, product=line.product,
            description=line.description or str(line.product), quantity=line.qty_to_invoice,
            unit_price=line.unit_price, discount_pct=line.discount_pct,
        )
    return inv


def copy_order_defaults(order):
    """Fill defaults from the customer; the ship-to address always follows the ship-to location."""
    customer = order.customer
    if not order.sales_rep_id and customer.sales_rep_id:
        order.sales_rep = customer.sales_rep
    if not order.ship_to_id:
        order.ship_to = customer.default_location
    if order.ship_to_id:
        order.shipping_address = order.ship_to.full_address
    elif not order.shipping_address:
        order.shipping_address = customer.shipping_address or customer.full_address
    if customer.tax_exempt:
        order.tax_rate = ZERO


def release_to_warehouse(order, user=None):
    """Mark the order as transmitted to the warehouse; it can no longer be edited (recall it first)."""
    from django.utils import timezone

    if order.status not in SalesOrder.SHIPPABLE_STATUSES:
        raise ValidationError("Only open (approved) orders can be released to the warehouse.")
    if order.on_hold:
        raise ValidationError("Remove the hold before releasing to the warehouse.")
    if order.released_at:
        raise ValidationError("Order is already released.")
    order.released_at = timezone.now()
    order.released_by = user if getattr(user, "is_authenticated", False) else None
    order.save(update_fields=["released_at", "released_by"])
    return order


def recall_from_warehouse(order, user=None):
    if not order.released_at:
        raise ValidationError("Order has not been released to the warehouse.")
    order.released_at, order.released_by = None, None
    order.save(update_fields=["released_at", "released_by"])
    return order


def hold_order(order, reason="", user=None):
    if order.status in (SalesOrder.CLOSED, SalesOrder.CANCELLED, SalesOrder.SHIPPED):
        raise ValidationError("Only open orders can be put on hold.")
    order.on_hold, order.hold_reason = True, (reason or "On hold")[:255]
    order.save(update_fields=["on_hold", "hold_reason"])
    return order


def release_hold(order, user=None):
    if not order.on_hold:
        raise ValidationError("Order is not on hold.")
    order.on_hold, order.hold_reason = False, ""
    order.save(update_fields=["on_hold", "hold_reason"])
    return order


def reopen_order(order, user=None):
    """Cancelled -> back to quotation; shipped / closed -> editable again to add lines or quantity."""
    if order.status == SalesOrder.CANCELLED:
        order.status = SalesOrder.QUOTE
        order.save(update_fields=["status"])
    elif order.status in (SalesOrder.SHIPPED, SalesOrder.CLOSED):
        order.reopened = True
        order.save(update_fields=["reopened"])
    else:
        raise ValidationError("Only cancelled, shipped or closed orders can be reopened.")
    return order


def after_order_edit(order):
    """Re-evaluate status after an order was edited (lines added / changed)."""
    if order.status in (SalesOrder.QUOTE, SalesOrder.PENDING, SalesOrder.CANCELLED):
        return order
    order.reopened = False
    order.save(update_fields=["reopened"])
    order.refresh_status()
    if order.status == SalesOrder.CONFIRMED and not order.credit_override:
        problem = credit_check(order)
        if problem:
            order.status = SalesOrder.PENDING
            order.approval_reason = ("Edited order: " + problem)[:255]
            order.save(update_fields=["status", "approval_reason"])
    return order


__all__ = ["SalesOrderLine", "confirm_order", "cancel_order", "create_delivery", "create_invoice_from_order"]
