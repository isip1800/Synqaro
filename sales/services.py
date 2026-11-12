import datetime

from django.core.exceptions import ValidationError
from django.db import transaction

from core.utils import ZERO, money

from .models import Delivery, DeliveryLine, SalesOrder, SalesOrderLine


def credit_check(order):
    customer = order.customer
    if not customer.credit_limit:
        return None
    exposure = customer.balance + customer.open_orders_value + order.total
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
    """Ship goods (quantities: {so_line_id: qty}); relieves inventory at average cost and books COGS."""
    from accounting.services import acct, post_journal
    from inventory.services import issue_stock

    order = SalesOrder.objects.select_for_update().get(pk=order.pk)
    if order.status not in SalesOrder.SHIPPABLE_STATUSES:
        raise ValidationError("Order must be open (confirmed and approved) before shipping.")
    delivery = None
    total_cost = ZERO
    for line in order.lines.select_related("product"):
        qty = quantities.get(line.pk) or ZERO
        if qty <= 0:
            continue
        if qty > line.qty_to_ship:
            raise ValidationError(f"{line.product.sku}: shipping {qty} exceeds remaining {line.qty_to_ship}.")
        if delivery is None:
            delivery = Delivery.objects.create(order=order, warehouse=order.warehouse, date=date, carrier=carrier,
                                               tracking_number=tracking_number, created_by=user)
        unit_cost = issue_stock(line.product, order.warehouse, qty, date, delivery.number)
        DeliveryLine.objects.create(delivery=delivery, so_line=line, quantity=qty, unit_cost=unit_cost)
        line_cost = money(qty * unit_cost)
        total_cost += line_cost
        line.qty_shipped += qty
        line.cost_shipped += line_cost
        line.save(update_fields=["qty_shipped", "cost_shipped"])
    if delivery is None:
        raise ValidationError("Enter a quantity to ship.")
    delivery.journal_entry = post_journal(
        date, f"COGS {delivery.number} — {order.number}",
        [{"account": acct("COGS"), "debit": total_cost}, {"account": acct("INVENTORY"), "credit": total_cost}],
        "delivery", delivery.number, user,
    )
    delivery.save(update_fields=["journal_entry"])
    order.refresh_status()
    return delivery


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
    """Fill defaults from customer when creating an order."""
    customer = order.customer
    if not order.sales_rep_id and customer.sales_rep_id:
        order.sales_rep = customer.sales_rep
    if not order.shipping_address:
        order.shipping_address = customer.shipping_address or customer.full_address
    if customer.tax_exempt:
        order.tax_rate = ZERO


__all__ = ["SalesOrderLine", "confirm_order", "cancel_order", "create_delivery", "create_invoice_from_order"]
