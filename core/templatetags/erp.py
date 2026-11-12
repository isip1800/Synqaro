import datetime
from decimal import Decimal

from django import template
from django.db.models import Model
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html

register = template.Library()


def resolve(obj, accessor):
    value = obj
    parts = accessor.split(".")
    for i, part in enumerate(parts):
        if value is None:
            return None
        if isinstance(value, dict):
            value = value.get(part)
            continue
        display = getattr(value, f"get_{part}_display", None)
        if display and i == len(parts) - 1:
            return display()
        value = getattr(value, part, None)
        if callable(value) and not isinstance(value, Model):
            value = value()
    return value


def fmt(value):
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "✓" if value else "—"
    if isinstance(value, Decimal):
        return f"{value:,.2f}"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f"{value:,}"
    if isinstance(value, datetime.datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, datetime.date):
        return value.strftime("%Y-%m-%d")
    return str(value)


@register.simple_tag
def cell(obj, accessor, link=False):
    if accessor == "status" and hasattr(obj, "status"):
        return status_badge(obj)
    value = resolve(obj, accessor)
    text = fmt(value)
    if link and hasattr(obj, "get_absolute_url"):
        return format_html('<a href="{}">{}</a>', obj.get_absolute_url(), text)
    if isinstance(value, Model) and hasattr(value, "get_absolute_url"):
        try:
            return format_html('<a href="{}">{}</a>', value.get_absolute_url(), text)
        except NoReverseMatch:
            pass
    return text


@register.simple_tag
def status_badge(obj, field="status"):
    raw = getattr(obj, field, "")
    display = getattr(obj, f"get_{field}_display", lambda: raw)()
    return format_html('<span class="badge badge-{}">{}</span>', raw, display)


@register.filter
def money(value):
    if value in (None, ""):
        return "—"
    return f"{Decimal(value):,.2f}"


@register.filter
def qty(value):
    if value in (None, ""):
        return "—"
    value = Decimal(value)
    if value == value.to_integral():
        return f"{value:,.0f}"
    return f"{value:,.3f}".rstrip("0").rstrip(".")


@register.filter
def unitcost(value):
    if value in (None, ""):
        return "—"
    return f"{Decimal(value):,.4f}"


@register.filter
def pct(value):
    if value in (None, ""):
        return "—"
    return f"{Decimal(value):,.1f}%"


@register.simple_tag(takes_context=True)
def nav_active(context, url_name, exact=False):
    request = context.get("request")
    try:
        url = reverse(url_name)
    except NoReverseMatch:
        return ""
    if not request:
        return ""
    if url == "/" or exact:
        return "active" if request.path == url else ""
    return "active" if request.path.startswith(url) else ""


@register.filter
def get_item(mapping, key):
    return mapping.get(key) if mapping else None


@register.filter
def model_url(obj, action):
    return f"{obj._meta.app_label}:{obj._meta.model_name}_{action}"
