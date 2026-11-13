import datetime

from django.contrib import messages
from django.db.models import F, Sum
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render

from core.utils import ZERO

from .crud import ErpListView
from .forms import model_form
from .mixins import internal_required, is_vendor_user
from .models import Company, Currency, ExchangeRate


def dashboard(request):
    if not request.user.is_authenticated:
        return redirect("login")
    if is_vendor_user(request.user):
        return redirect("portal:dashboard")
    return _dashboard(request)


@internal_required
def _dashboard(request):
    from accounting.models import CustomerInvoice, VendorBill
    from inventory.models import StockQuant
    from products.models import Product
    from purchasing.models import PurchaseOrder, Shipment
    from sales.models import SalesOrder

    today = datetime.date.today()
    month_start = today.replace(day=1)
    posted = CustomerInvoice.objects.filter(status__in=CustomerInvoice.POSTED_STATUSES)
    month_invoices = posted.filter(invoice_date__gte=month_start).prefetch_related("lines")
    sales_mtd = sum((i.subtotal for i in month_invoices), ZERO)
    margin_mtd = sum((i.gross_margin for i in month_invoices), ZERO)

    from django.db.models import Count, ExpressionWrapper, DecimalField

    open_ar = CustomerInvoice.objects.filter(status__in=CustomerInvoice.OPEN_STATUSES)
    bal = F("posted_total") - F("amount_paid")
    ar_total = open_ar.aggregate(t=Sum(bal))["t"] or ZERO
    overdue_agg = open_ar.filter(due_date__lt=today).aggregate(t=Sum(bal), n=Count("id"))
    open_ap = VendorBill.objects.filter(status__in=VendorBill.OPEN_STATUSES)
    bal_base = ExpressionWrapper((F("posted_total") - F("amount_paid")) * F("exchange_rate"),
                                 output_field=DecimalField(max_digits=20, decimal_places=4))
    ap_total = open_ap.aggregate(t=Sum(bal_base))["t"] or ZERO
    ap_due_week = open_ap.filter(due_date__lte=today + datetime.timedelta(days=7)).aggregate(
        t=Sum(bal_base))["t"] or ZERO

    inv_value = StockQuant.objects.aggregate(v=Sum(F("quantity") * F("product__avg_cost")))["v"] or ZERO
    shipments = (Shipment.objects.exclude(status__in=[Shipment.RECEIVED, Shipment.CANCELLED])
                 .select_related("vendor").order_by("eta")[:8])
    from inventory import availability

    candidates = list(Product.objects.filter(is_active=True, reorder_point__gt=0).select_related("preferred_vendor"))
    data = availability.compute([p.pk for p in candidates]) if candidates else {}
    low_stock = []
    for p in candidates:
        b = data[p.pk]
        on_order = b["po_open"] + b["booked"] + b["booked_approved"] + b["in_transit"] + b["po_new"]
        if b["on_hand"] + on_order <= p.reorder_point:
            p.dash_on_hand, p.dash_on_order = b["on_hand"], on_order
            low_stock.append(p)

    ctx = {
        "sales_mtd": sales_mtd,
        "margin_mtd": margin_mtd,
        "margin_pct": (margin_mtd / sales_mtd * 100) if sales_mtd else None,
        "ar_total": ar_total,
        "overdue_count": overdue_agg["n"],
        "overdue_total": overdue_agg["t"] or ZERO,
        "ap_total": ap_total,
        "ap_due_week": ap_due_week,
        "inv_value": inv_value,
        "open_pos": PurchaseOrder.objects.filter(status__in=PurchaseOrder.OPEN_STATUSES).count(),
        "shipments": shipments,
        "low_stock": low_stock[:10],
        "low_stock_count": len(low_stock),
        "orders_to_ship": SalesOrder.objects.filter(status__in=SalesOrder.SHIPPABLE_STATUSES + (SalesOrder.PENDING,))
        .select_related("customer")[:8],
        "quotes": SalesOrder.objects.filter(status=SalesOrder.QUOTE).count(),
        "today": today,
    }
    return render(request, "core/dashboard.html", ctx)


_CompanyBase = model_form(Company, ["name", "legal_name", "tax_id", "address", "phone", "email", "base_currency",
                                    "default_sales_tax_rate", "default_payment_terms_days",
                                    "default_warehouse", "require_so_approval", "upc_company_prefix",
                                    "upc_next_item_ref", "cbm_20", "cbm_40", "cbm_40hq", "cbm_45",
                                    "avail_no_negative", "sales_avail_no_negative"])


class CompanyForm(_CompanyBase):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django import forms

        from inventory.availability import FLOORABLE

        for name in ("avail_no_negative", "sales_avail_no_negative"):
            self.fields[name] = forms.MultipleChoiceField(
                choices=FLOORABLE, required=False, widget=forms.CheckboxSelectMultiple,
                label=Company._meta.get_field(name).verbose_name.capitalize(),
                initial=getattr(self.instance, name),
                help_text="Ticked fields show 0 instead of a negative number on this screen")


@internal_required
def company(request):
    obj = Company.get()
    form = CompanyForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Company settings saved.")
        return redirect("core:company")
    return render(request, "core/form.html", {"form": form, "title": "Company Settings"})


class ExchangeRateList(ErpListView):
    model = ExchangeRate
    title = "Currencies & Exchange Rates"
    template_name = "core/exchange_rates.html"
    columns = (("Currency", "currency"), ("Date", "date"), ("Rate to base", "rate"))
    select_related = ("currency",)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["currencies"] = Currency.objects.all()
        return ctx


# ----------------------------------------------------------------------------- column chooser / saved filters

def _safe_next(request, default="/"):
    from django.utils.http import url_has_allowed_host_and_scheme

    nxt = request.POST.get("next") or request.GET.get("next") or default
    return nxt if url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}) else default


@internal_required
def columns_save(request):
    from django.views.decorators.http import require_POST  # noqa: F401

    from .columns import reset_columns, save_columns

    if request.method == "POST":
        screen = request.POST.get("screen", "")[:90]
        if request.POST.get("reset"):
            reset_columns(request.user, screen)
            messages.success(request, "Columns reset to the default.")
        else:
            keys = request.POST.getlist("col")
            if not keys:
                messages.error(request, "Pick at least one column.")
            else:
                save_columns(request.user, screen, keys)
                messages.success(request, "Columns saved.")
    return redirect(_safe_next(request))


@internal_required
def filter_save(request):
    from .models import SavedFilter

    if request.method == "POST":
        name = request.POST.get("name", "").strip()[:80]
        screen = request.POST.get("screen", "")[:90]
        if not name:
            messages.error(request, "Give the filter a name.")
        else:
            SavedFilter.objects.update_or_create(
                user=request.user, screen=screen, name=name,
                defaults={"query": request.POST.get("query", ""), "shared": bool(request.POST.get("shared"))})
            messages.success(request, f"Filter “{name}” saved.")
    return redirect(_safe_next(request))


@internal_required
def filter_delete(request, pk):
    from .models import SavedFilter

    if request.method == "POST":
        f = SavedFilter.objects.filter(pk=pk).first()
        if f and (f.user_id == request.user.id or request.user.is_superuser):
            f.delete()
            messages.success(request, f"Filter “{f.name}” deleted.")
    return redirect(_safe_next(request))


# ----------------------------------------------------------------------------- email settings / log

@internal_required
def email_settings(request):
    from django.conf import settings as dj

    from . import documents
    from .models import EmailLog

    templates = [documents.get_template(k) for k, _ in documents.DEFAULT_TEMPLATES.items()]
    return render(request, "core/email_settings.html", {
        "title": "Email", "configured": dj.EMAIL_CONFIGURED, "host": dj.EMAIL_HOST, "port": dj.EMAIL_PORT,
        "from_email": dj.DEFAULT_FROM_EMAIL, "user_name": dj.EMAIL_HOST_USER, "templates": templates,
        "recent": EmailLog.objects.all()[:10],
    })


class EmailLogList(ErpListView):
    from .models import EmailLog as model  # noqa: N813

    title = "Email Log"
    columns = (("Sent", "created_at"), ("Document", "document"), ("To", "to"), ("Subject", "subject"),
               ("Status", "status"), ("By", "sent_by"), ("Error", "error"))
    search_fields = ("document", "to", "subject")
    create_url = None


# ----------------------------------------------------------------------------- companies, users & roles

def _member_companies(user):
    from .models import Membership

    if user.is_superuser:
        return Company.objects.filter(is_active=True)
    return Company.objects.filter(memberships__user=user, memberships__is_active=True, is_active=True)


@internal_required
def switch_company(request):
    if request.method == "POST":
        cid = int(request.POST.get("company_id") or 0)
        if _member_companies(request.user).filter(pk=cid).exists():
            request.session["company_id"] = cid
            Company.clear_cache()
            messages.success(request, f"Now working in {Company.objects.get(pk=cid).name}.")
        else:
            messages.error(request, "You are not a member of that company.")
    return redirect("dashboard")


def _is_owner_somewhere(user):
    from .models import Membership

    return user.is_superuser or Membership.objects.filter(user=user, role__name="Owner", is_active=True).exists()


@internal_required
def company_list(request):
    from django import forms

    from .models import Membership

    class NewCompanyForm(forms.Form):
        name = forms.CharField(max_length=200)
        base_currency = forms.ModelChoiceField(queryset=Currency.objects.all(), initial="USD")
        copy_setup = forms.BooleanField(required=False, initial=True,
                                        label="Copy item classes, item details and HTS codes from the current company")

    can_create = _is_owner_somewhere(request.user)
    form = NewCompanyForm(request.POST or None)
    if request.method == "POST":
        if not can_create:
            messages.error(request, "Only company owners can create companies.")
        elif form.is_valid():
            from .services import create_company

            new = create_company(form.cleaned_data["name"], form.cleaned_data["base_currency"].code, request.user,
                                 copy_setup_from=request.company if form.cleaned_data["copy_setup"] else None)
            request.session["company_id"] = new.pk
            messages.success(request, f"Company {new.name} created — you are its owner and now working in it.")
            return redirect("core:company")
    rows = []
    for c in _member_companies(request.user).order_by("name"):
        m = Membership.objects.filter(user=request.user, company=c).select_related("role").first()
        rows.append({"company": c, "role": m.role.name if m else ("Platform admin" if request.user.is_superuser
                                                                  else ""),
                     "seats": c.memberships.filter(is_active=True).count(), "current": c.pk == request.company.pk})
    return render(request, "core/company_list.html", {"title": "Companies", "rows": rows, "form": form,
                                                      "can_create": can_create})


@internal_required
def user_list(request):
    from .models import Membership, Role
    from .permissions import ensure_roles

    ensure_roles(request.company)
    members = Membership.objects.filter(company=request.company).select_related("user", "role")
    return render(request, "core/user_list.html", {
        "title": "Users & Roles", "members": members, "roles": Role.objects.filter(company=request.company)
        .prefetch_related("memberships"), "seats": members.filter(is_active=True).count()})


@internal_required
def user_form(request, pk=None):
    from django import forms
    from django.contrib.auth import get_user_model

    from .models import Membership, Role

    User = get_user_model()
    membership = get_object_or_404(Membership, pk=pk, company=request.company) if pk else None
    roles = Role.objects.filter(company=request.company)

    class MemberForm(forms.Form):
        username = forms.CharField(max_length=150, help_text="Existing login (adds them to this company) or a "
                                                            "new one")
        first_name = forms.CharField(max_length=150, required=False)
        last_name = forms.CharField(max_length=150, required=False)
        email = forms.EmailField(required=False)
        role = forms.ModelChoiceField(queryset=roles)
        password = forms.CharField(widget=forms.PasswordInput, required=False, min_length=8,
                                   help_text="Required for a new login; leave blank to keep the current password")
        is_active = forms.BooleanField(required=False, initial=True, label="Active in this company")

    initial = {}
    if membership:
        u = membership.user
        initial = {"username": u.username, "first_name": u.first_name, "last_name": u.last_name, "email": u.email,
                   "role": membership.role_id, "is_active": membership.is_active}
    form = MemberForm(request.POST or None, initial=initial)
    if membership:
        form.fields["username"].disabled = True
    if request.method == "POST" and form.is_valid():
        cd = form.cleaned_data
        with transaction.atomic():
            user = membership.user if membership else User.objects.filter(username=cd["username"]).first()
            if user is None:
                if not cd["password"]:
                    form.add_error("password", "Set a password for the new login.")
                    return render(request, "core/form.html", {"form": form, "title": "Add user"})
                user = User.objects.create_user(cd["username"], email=cd["email"], password=cd["password"])
            elif hasattr(user, "vendor_contact"):
                form.add_error("username", "That login is a vendor-portal user.")
                return render(request, "core/form.html", {"form": form, "title": "Add user"})
            if cd["first_name"] or cd["last_name"] or cd["email"]:
                user.first_name, user.last_name = cd["first_name"], cd["last_name"]
                user.email = cd["email"] or user.email
            if cd["password"] and (membership or not user.has_usable_password()):
                user.set_password(cd["password"])
            user.save()
            m, _ = Membership.objects.update_or_create(user=user, company=request.company,
                                                       defaults={"role": cd["role"], "is_active": cd["is_active"]})
            owners = Membership.objects.filter(company=request.company, role__name="Owner", is_active=True)
            if not owners.exists():
                raise_error = "The company must keep at least one active Owner."
                transaction.set_rollback(True)
                messages.error(request, raise_error)
                return redirect("core:user_list")
        messages.success(request, f"{user.username} — {m.role} in {request.company.name}.")
        return redirect("core:user_list")
    return render(request, "core/form.html", {"form": form, "title": f"Edit {membership.user}" if membership
                                              else "Add user", "cancel_url": "/settings/users/"})


@internal_required
def role_form(request, pk=None):
    from .models import Role
    from .permissions import PERMISSIONS

    role = get_object_or_404(Role, pk=pk, company=request.company) if pk else None
    if request.method == "POST":
        name = request.POST.get("name", "").strip()[:60]
        perms = [p for p in request.POST.getlist("perm") if p in dict((x[0], 1) for x in PERMISSIONS)]
        if request.POST.get("delete") and role:
            if role.is_system or role.memberships.exists():
                messages.error(request, "Built-in roles and roles in use cannot be deleted.")
            else:
                role.delete()
                messages.success(request, "Role deleted.")
            return redirect("core:user_list")
        if not name:
            messages.error(request, "Give the role a name.")
        elif Role.objects.filter(company=request.company, name__iexact=name).exclude(pk=getattr(role, "pk", None)) \
                .exists():
            messages.error(request, "A role with that name exists.")
        else:
            if role and role.name == "Owner" and "users.manage" not in perms:
                perms.append("users.manage")  # owners can always manage users
            if role is None:
                role = Role.objects.create(company=request.company, name=name, permissions=perms,
                                           description=request.POST.get("description", "")[:255])
            else:
                role.name, role.permissions = name, perms
                role.description = request.POST.get("description", "")[:255]
                role.save()
            messages.success(request, f"Role {role.name} saved.")
            return redirect("core:user_list")
    groups = {}
    for code, group, label in PERMISSIONS:
        groups.setdefault(group, []).append((code, label, role is not None and code in role.permissions))
    return render(request, "core/role_form.html", {"title": f"Role: {role.name}" if role else "New role",
                                                   "role": role, "groups": groups.items()})


class AuditLogList(ErpListView):
    from .models import AuditLog as model  # noqa: N813

    title = "Change log"
    template_name = "core/audit_list.html"
    paginate_by = 100
    search_fields = ("object_repr", "user__username")
    select_related = ("user", "content_type")

    def get_queryset(self):
        qs = super().get_queryset()
        g = self.request.GET
        if g.get("type"):
            qs = qs.filter(content_type_id=g["type"])
        if g.get("user"):
            qs = qs.filter(user_id=g["user"])
        if g.get("action"):
            qs = qs.filter(action=g["action"])
        if g.get("start"):
            qs = qs.filter(at__date__gte=g["start"])
        if g.get("end"):
            qs = qs.filter(at__date__lte=g["end"])
        return qs

    def get_context_data(self, **kwargs):
        from django.contrib.auth import get_user_model
        from django.contrib.contenttypes.models import ContentType

        from .audit import TRACKED, visible_changes
        from .models import AuditLog

        ctx = super().get_context_data(**kwargs)
        types = [ContentType.objects.get_for_model(__import__("django.apps", fromlist=["apps"]).apps.get_model(l))
                 for l in TRACKED]
        ctx.update(types=sorted(types, key=lambda t: t.name), actions=AuditLog.ACTIONS, params=self.request.GET,
                   users=get_user_model().objects.filter(memberships__company=self.request.company).distinct(),
                   rows=[(e, visible_changes(e, "costs.view" in getattr(self.request, "perms", ())))
                         for e in ctx["object_list"]])
        return ctx
