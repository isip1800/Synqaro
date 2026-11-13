import datetime

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Prefetch, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_POST

from core.crud import ErpDetailView, ErpListView, run_action
from core.mixins import internal_required
from core.models import Company
from core.utils import ZERO

from . import services
from .forms import (
    PRODUCT_SECTIONS,
    CrossRefFormSet,
    HTSCodeForm,
    HTSImportForm,
    ItemClassForm,
    LookupForm,
    ProductForm,
    VendorPriceFormSet,
    WarehouseFormSet,
)
from .models import HTSCode, ItemClass, LookupValue, PriceList, Product, ProductImage

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ----------------------------------------------------------------------------- product list / export

def filtered_products(request):
    qs = Product.objects.select_related(
        "item_class__parent__parent", "brand", "season", "gender", "material", "customer_code", "package_size",
        "size_range", "size_breakdown", "preferred_vendor",
    ).prefetch_related(Prefetch("images", queryset=ProductImage.objects.only("id", "is_primary", "product_id",
                                                                             "width", "height")))
    g = request.GET
    q = g.get("q", "").strip()
    if q:
        qs = qs.filter(Q(sku__icontains=q) | Q(name__icontains=q) | Q(hs_code__icontains=q) | Q(barcode__icontains=q)
                       | Q(style_number__icontains=q) | Q(cross_references__alternate_id__icontains=q)).distinct()
    if g.get("tier1"):
        qs = qs.filter(item_class__parent__parent_id=g["tier1"])
    if g.get("tier2"):
        qs = qs.filter(item_class__parent_id=g["tier2"])
    if g.get("item_class"):
        qs = qs.filter(item_class_id=g["item_class"])
    for key in ("brand", "season", "gender", "material", "customer_code", "size_range"):
        if g.get(key):
            qs = qs.filter(**{f"{key}_id": g[key]})
    if g.get("is_active") in ("True", "False"):
        qs = qs.filter(is_active=g["is_active"] == "True")
    wh = g.get("warehouse")
    if wh:
        qs = qs.annotate(on_hand=Sum("quants__quantity", filter=Q(quants__warehouse_id=wh)))
        if g.get("stocked") == "1":
            qs = qs.filter(Q(on_hand__gt=0) | Q(warehouse_settings__warehouse_id=wh)).distinct()
    else:
        qs = qs.annotate(on_hand=Sum("quants__quantity"))
    return qs.order_by("sku")


PRODUCT_COLUMNS = [
    # key, label, kind (text / num / money / cost / pct / qty)
    ("name", "Description", "text"), ("style_number", "Style #", "text"), ("item_class", "Item Class", "text"),
    ("product_type", "Product Type", "text"), ("brand", "Brand", "text"), ("season", "Season", "text"),
    ("gender", "Gender", "text"), ("size_range", "Size Range", "text"), ("material", "Material", "text"),
    ("customer_code", "Customer Code", "text"), ("barcode", "UPC", "text"), ("hs_code", "HTS Code", "text"),
    ("total_duty_rate", "Duty + Tariff %", "pct"), ("carton_qty", "Carton Qty", "qty"),
    ("carton_cbm", "Carton CBM", "cost"), ("qty_per_40hq", "Qty / 40'HQ", "qty"), ("on_hand", "On Hand", "qty"),
    ("avg_cost", "Avg Landed Cost", "cost"), ("list_price", "List Price", "money"), ("margin_pct", "Margin %", "pct"),
]
DEFAULT_PRODUCT_COLUMNS = ["name", "item_class", "brand", "season", "hs_code", "total_duty_rate", "carton_qty",
                           "qty_per_40hq", "on_hand", "avg_cost", "list_price"]


COST_PRODUCT_COLUMNS = {"avg_cost", "margin_pct"}


def visible_product_columns(request):
    if "costs.view" in getattr(request, "perms", ()):
        return PRODUCT_COLUMNS
    return [c for c in PRODUCT_COLUMNS if c[0] not in COST_PRODUCT_COLUMNS]


def product_cell(p, key):
    from inventory.views import attr_value

    if key in ("item_class", "product_type", "brand", "season", "size_range", "material", "customer_code"):
        return attr_value(p, key)
    if key == "gender":
        return p.gender.code if p.gender else ""
    return getattr(p, key)


class ProductList(ErpListView):
    model = Product
    template_name = "products/product_list.html"
    paginate_by = 50

    def get_queryset(self):
        return filtered_products(self.request)

    def get_context_data(self, **kwargs):
        from core.columns import chooser_context, chosen_columns
        from inventory.models import Warehouse

        ctx = super().get_context_data(**kwargs)
        screen = "list:products.product"
        columns = visible_product_columns(self.request)
        keys = chosen_columns(self.request, screen, [c[0] for c in columns], DEFAULT_PRODUCT_COLUMNS)
        meta = {c[0]: c for c in columns}
        cols = [meta[k] for k in keys]
        ctx["cols"] = cols
        ctx["rows"] = [(p, [(c, product_cell(p, c[0])) for c in cols]) for p in ctx["object_list"]]
        ctx.update(chooser_context(self.request, screen, [(c[0], c[1]) for c in columns], keys))
        lookups = {k: LookupValue.objects.filter(kind=k, is_active=True)
                   for k in ("brand", "season", "size_range", "material", "customer_code")}
        ctx.update(
            tier1=ItemClass.objects.filter(level=1), tier2=ItemClass.objects.filter(level=2).select_related("parent"),
            tier3=ItemClass.objects.filter(level=3).select_related("parent__parent"), lookups=lookups,
            warehouses=Warehouse.objects.filter(is_active=True), params=self.request.GET,
            query=self.request.GET.urlencode(),
        )
        return ctx


@internal_required
def product_export(request):
    products = list(filtered_products(request).prefetch_related(
        Prefetch("images", queryset=ProductImage.objects.defer("data"), to_attr="export_images")))
    for p in products:
        p.export_on_hand = p.on_hand or 0
    mode = "float" if request.GET.get("images") == "float" else "cell"
    data = services.export_products_xlsx(products, image_mode=mode,
                                         include_costs="costs.view" in getattr(request, "perms", ()))
    resp = HttpResponse(data, content_type=XLSX)
    resp["Content-Disposition"] = f'attachment; filename="products-{datetime.date.today():%Y%m%d}.xlsx"'
    return resp


# ----------------------------------------------------------------------------- product form (create / edit)

@internal_required
def product_form(request, pk=None):
    product = get_object_or_404(Product, pk=pk) if pk else None
    initial = {}
    if product is None and request.GET.get("copy"):
        src = Product.objects.filter(pk=request.GET["copy"]).first()
        if src:
            initial = {f: getattr(src, f"{f}_id", getattr(src, f, None)) for _, fs in PRODUCT_SECTIONS for f in fs
                       if f not in ("sku", "barcode")}
    form = ProductForm(request.POST or None, instance=product, initial=initial)
    sets = [("Warehouses", WarehouseFormSet(request.POST or None, instance=product, prefix="wh")),
            ("Cross-reference", CrossRefFormSet(request.POST or None, instance=product, prefix="xref")),
            ("Vendors", VendorPriceFormSet(request.POST or None, instance=product, prefix="vp"))]
    if request.method == "POST":
        if form.is_valid() and all(fs.is_valid() for _, fs in sets):
            try:
                with transaction.atomic():
                    obj = form.save()
                    for _, fs in sets:
                        fs.instance = obj
                        fs.save()
                messages.success(request, f"Product {obj.sku} saved.")
                return redirect(obj.get_absolute_url())
            except ValidationError as exc:
                form.add_error(None, exc)
    company = Company.get()
    sections = [(title, [form[f] for f in fields]) for title, fields in PRODUCT_SECTIONS]
    return render(request, "products/product_form.html", {
        "form": form, "sections": sections, "formsets": sets, "product": product,
        "title": f"Edit {product.sku}" if product else "New Product", "containers": company.containers,
        "lookup_kinds": dict(LookupValue.KINDS),
    })


class ProductDetail(ErpDetailView):
    model = Product
    template_name = "products/product_detail.html"

    def get_queryset(self):
        return super().get_queryset().select_related(
            "item_class__parent__parent", "brand", "package_size", "season", "seasonal_cutoff", "gender",
            "size_range", "size_breakdown", "item_spec", "material", "customer_code", "label1", "label2", "uom")

    def get_context_data(self, **kwargs):
        from inventory.models import StockQuant, Warehouse

        ctx = super().get_context_data(**kwargs)
        p = self.object
        quants = {q.warehouse_id: q.quantity for q in StockQuant.objects.filter(product=p)}
        settings = {s.warehouse_id: s for s in p.warehouse_settings.all()}
        warehouses = []
        for wh in Warehouse.objects.all():
            if wh.pk in quants or wh.pk in settings:
                warehouses.append({"warehouse": wh, "qty": quants.get(wh.pk, ZERO), "settings": settings.get(wh.pk)})
        ctx.update(
            warehouses=warehouses,
            moves=p.moves.select_related("warehouse")[:25],
            vendor_prices=p.vendor_products.select_related("vendor", "currency"),
            price_tiers=p.price_list_items.select_related("price_list"),
            cross_refs=p.cross_references.select_related("vendor", "customer", "uom"),
            images=p.images.only("id", "is_primary", "filename", "width", "height", "product_id"),
            open_po_lines=p.po_lines.filter(order__status__in=["sent", "confirmed", "partial"])
            .select_related("order", "order__vendor"),
            hts_row=p.hts_match, company=Company.get(),
        )
        from inventory import availability

        whs = [w["warehouse"] for w in warehouses] or list(Warehouse.objects.filter(is_active=True)[:1])
        total = availability.for_product(p)
        per_wh = [(w, availability.for_product(p, w.pk)) for w in whs] if len(whs) > 1 else []
        ctx["avail_rows"] = [
            {"key": key, "label": label, "group": group, "help": help_text, "total": total[key],
             "per_wh": [b[key] for _, b in per_wh], "formula": key in availability.FORMULAS,
             "drill": key in availability.DRILLABLE}
            for key, label, group, help_text in availability.BUCKETS
        ]
        ctx["avail_whs"] = [w for w, _ in per_wh]
        return ctx


# ----------------------------------------------------------------------------- images & UPC

def _image_response(data, etag):
    resp = HttpResponse(bytes(data), content_type="image/jpeg")
    resp["ETag"] = etag
    return resp


@internal_required
@cache_control(private=True, max_age=86400)
def image(request, pk):
    img = ProductImage.objects.filter(pk=pk).only("data").first() or None
    if img is None:
        raise Http404
    return _image_response(img.data, f'"img{pk}"')


@internal_required
@cache_control(private=True, max_age=86400)
def image_thumb(request, pk):
    img = ProductImage.objects.filter(pk=pk).only("thumbnail").first()
    if img is None:
        raise Http404
    return _image_response(img.thumbnail, f'"thumb{pk}"')


@internal_required
@require_POST
def image_upload(request, pk):
    product = get_object_or_404(Product, pk=pk)
    files = request.FILES.getlist("images")
    if not files:
        messages.error(request, "Choose one or more image files to upload.")
    for f in files:
        try:
            services.add_image(product, f, make_primary=request.POST.get("primary") == "1" and f is files[0])
            messages.success(request, f"Uploaded {f.name}.")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
    return redirect(product.get_absolute_url() + "#tab-images")


@internal_required
@require_POST
def image_primary(request, pk):
    img = get_object_or_404(ProductImage, pk=pk)
    services.set_primary(img)
    return redirect(img.product.get_absolute_url() + "#tab-images")


@internal_required
@require_POST
def image_delete(request, pk):
    img = get_object_or_404(ProductImage, pk=pk)
    product = img.product
    services.delete_image(img)
    messages.success(request, "Image deleted.")
    return redirect(product.get_absolute_url() + "#tab-images")


@internal_required
@require_POST
def generate_upc(request, pk):
    product = get_object_or_404(Product, pk=pk)
    upc = run_action(request, product, services.generate_upc, "UPC generated.", product)
    if upc:
        messages.info(request, f"{product.sku} UPC: {upc}")
    return redirect(product.get_absolute_url() + "#tab-xref")


# ----------------------------------------------------------------------------- product setup (lookups)

@internal_required
def setup_index(request):
    counts = dict(LookupValue.objects.values_list("kind").annotate(n=Count("id")))
    kinds = [(k, label, counts.get(k, 0)) for k, label in LookupValue.KINDS]
    return render(request, "products/setup_index.html", {
        "title": "Product Setup", "kinds": kinds, "item_classes": ItemClass.objects.count(),
        "hts_count": HTSCode.objects.count(), "hts_waiting": HTSCode.objects.filter(hts_code="").count(),
        "uom_count": __import__("products.models", fromlist=["UnitOfMeasure"]).UnitOfMeasure.objects.count(),
        "price_lists": PriceList.objects.count(),
    })


def _kind_or_404(kind):
    kinds = dict(LookupValue.KINDS)
    if kind not in kinds:
        raise Http404
    return kinds[kind]


@internal_required
def lookup_list(request, kind):
    label = _kind_or_404(kind)
    values = LookupValue.objects.filter(kind=kind)
    q = request.GET.get("q", "").strip()
    if q:
        values = values.filter(Q(code__icontains=q) | Q(description__icontains=q))
    field = "label1" if kind == "label" else kind
    used = dict(Product.objects.filter(**{f"{field}__kind": kind}).values_list(f"{field}_id")
                .annotate(n=Count("id")))
    rows = [(v, used.get(v.pk, 0)) for v in values]
    return render(request, "products/lookup_list.html", {
        "title": label, "kind": kind, "rows": rows, "q": q, "singular": LookupValue.SINGULAR[kind],
        "kinds": LookupValue.KINDS,
    })


@internal_required
def lookup_form(request, kind, pk=None):
    label = _kind_or_404(kind)
    obj = get_object_or_404(LookupValue, pk=pk, kind=kind) if pk else LookupValue(kind=kind)
    form = LookupForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        if LookupValue.objects.filter(kind=kind, code__iexact=form.cleaned_data["code"]).exclude(pk=obj.pk).exists():
            form.add_error("code", "This code already exists.")
        else:
            form.save()
            messages.success(request, f"{LookupValue.SINGULAR[kind]} {obj.code} saved.")
            if request.POST.get("another"):
                return redirect("products:lookup_create", kind=kind)
            return redirect("products:lookup_list", kind=kind)
    return render(request, "products/lookup_form.html", {
        "form": form, "title": f"{'Edit' if pk else 'New'} {LookupValue.SINGULAR[kind]}", "kind": kind,
        "kind_label": label, "object": obj if pk else None,
    })


@internal_required
@require_POST
def lookup_delete(request, kind, pk):
    from django.db.models import ProtectedError

    obj = get_object_or_404(LookupValue, pk=pk, kind=kind)
    try:
        obj.delete()
        messages.success(request, f"{obj.code} deleted.")
    except ProtectedError:
        messages.error(request, f"{obj.code} is used by products or HTS codes; mark it inactive instead.")
    return redirect("products:lookup_list", kind=kind)


# ----------------------------------------------------------------------------- item classes

@internal_required
def itemclass_list(request):
    tree = []
    for t1 in ItemClass.objects.filter(level=1).prefetch_related("children__children"):
        tree.append((t1, [(t2, list(t2.children.all())) for t2 in t1.children.all()]))
    counts = dict(Product.objects.values_list("item_class_id").annotate(n=Count("id")))
    return render(request, "products/itemclass_list.html", {"title": "Item Classes", "tree": tree,
                                                             "counts": counts})


@internal_required
def itemclass_form(request, pk=None):
    obj = get_object_or_404(ItemClass, pk=pk) if pk else None
    initial = {"parent": request.GET.get("parent")} if not pk else None
    form = ItemClassForm(request.POST or None, instance=obj, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            saved = form.save()
            messages.success(request, f"Item class {saved.full_code} saved.")
            return redirect("products:itemclass_list")
        except Exception as exc:  # unique (parent, code)
            form.add_error("code", f"Could not save: {exc}")
    return render(request, "core/form.html", {"form": form,
                                              "title": f"Edit item class {obj.full_code}" if obj else "New Item Class",
                                              "cancel_url": "/products/item-classes/"})


# ----------------------------------------------------------------------------- HTS codes

class HTSList(ErpListView):
    model = HTSCode
    template_name = "products/hts_list.html"
    paginate_by = 100
    search_fields = ("hts_code", "item_class__code", "item_class__parent__code", "item_class__parent__parent__code",
                     "material__code", "material__description", "notes")
    select_related = ("item_class__parent__parent", "material", "gender")

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.GET.get("tab") == "waiting":
            qs = qs.filter(hts_code="")
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(tab=self.request.GET.get("tab", "all"), waiting=HTSCode.objects.filter(hts_code="").count(),
                   total=HTSCode.objects.count(), import_form=HTSImportForm(), query=self.request.GET.urlencode())
        return ctx


@internal_required
def hts_form(request, pk=None):
    obj = get_object_or_404(HTSCode, pk=pk) if pk else None
    form = HTSCodeForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        dup = HTSCode.objects.filter(item_class=form.cleaned_data["item_class"], material=form.cleaned_data["material"],
                                     gender=form.cleaned_data["gender"]).exclude(pk=getattr(obj, "pk", None))
        if dup.exists():
            form.add_error(None, "A row for this item class / material / gender already exists.")
        else:
            row = form.save()
            n = Product.objects.filter(item_class=row.item_class, material=row.material, hts_override=False).count()
            messages.success(request, f"HTS row saved; {n} matching product(s) updated.")
            return redirect("products:htscode_list")
    return render(request, "core/form.html", {"form": form, "title": "Edit HTS Code" if obj else "New HTS Code",
                                              "cancel_url": "/products/hts-codes/"})


@internal_required
@require_POST
def hts_delete(request, pk):
    get_object_or_404(HTSCode, pk=pk).delete()
    messages.success(request, "HTS row deleted (products keep their current HTS values).")
    return redirect("products:htscode_list")


@internal_required
def hts_export(request):
    view = HTSList()
    view.request = request
    view.kwargs = {}
    data = services.export_hts_xlsx(list(view.get_queryset()))
    resp = HttpResponse(data, content_type=XLSX)
    resp["Content-Disposition"] = f'attachment; filename="hts-codes-{datetime.date.today():%Y%m%d}.xlsx"'
    return resp


@internal_required
@require_POST
def hts_import(request):
    form = HTSImportForm(request.POST, request.FILES)
    if not form.is_valid():
        messages.error(request, "Choose an .xlsx file to import.")
        return redirect("products:htscode_list")
    try:
        created, updated, errors = services.import_hts_xlsx(form.cleaned_data["file"])
        messages.success(request, f"HTS import: {created} created, {updated} updated.")
        for e in errors[:20]:
            messages.warning(request, e)
        if len(errors) > 20:
            messages.warning(request, f"…and {len(errors) - 20} more rows skipped.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    return redirect("products:htscode_list")


# ----------------------------------------------------------------------------- price lists

class PriceListDetail(ErpDetailView):
    model = PriceList
    template_name = "products/pricelist_detail.html"
    fields = (("Name", "name"), ("Description", "description"), ("Active", "is_active"))
