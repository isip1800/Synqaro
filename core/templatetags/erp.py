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


@register.inclusion_tag("core/_notes_files.html", takes_context=True)
def notes_and_files(context, obj):
    from django.contrib.contenttypes.models import ContentType

    from core.models import Attachment, EmailLog, Note

    ct = ContentType.objects.get_for_model(obj)
    return {
        "obj": obj, "ct_id": ct.pk, "csrf_token": context.get("csrf_token"), "user": context.get("user"),
        "can_see_costs": context.get("can_see_costs"), "perms": context.get("perms"),
        "files": Attachment.objects.filter(content_type=ct, object_id=obj.pk).defer("data"),
        "notes": Note.objects.filter(content_type=ct, object_id=obj.pk).select_related("author"),
        "emails": EmailLog.objects.filter(content_type=ct, object_id=obj.pk)[:10],
    }


@register.filter
def filesize(n):
    n = int(n or 0)
    for unit in ("B", "KB", "MB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


@register.filter
def thumb_data_uri(product):
    """Base64 data URI of a product's main thumbnail (for PDFs / emails)."""
    import base64

    from products.models import ProductImage

    if product is None:
        return ""
    img = ProductImage.objects.filter(product=product).order_by("-is_primary", "id").only("thumbnail").first()
    if not img:
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(bytes(img.thumbnail)).decode()


@register.simple_tag
def doc_ct(obj):
    from django.contrib.contenttypes.models import ContentType

    return ContentType.objects.get_for_model(obj).pk


@register.inclusion_tag("core/_history.html", takes_context=True)
def history_panel(context, obj, limit=50):
    from core.audit import history_for, visible_changes

    costs = bool(context.get("can_see_costs"))
    entries = []
    for e in history_for(obj, limit):
        changes = visible_changes(e, costs)
        if e.action == "update" and not changes:
            continue  # only hidden (cost) fields changed
        entries.append({"e": e, "is_line": e.object_id != obj.pk or e.content_type.model_class() is not type(obj),
                        "changes": changes})
    return {"entries": entries, "obj": obj}
