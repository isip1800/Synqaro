"""Product services: images, UPC generation, Excel export (images embedded in cells), HTS import/export."""
import io
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from core.models import Company

from .models import HTSCode, ItemClass, LookupValue, Product, ProductCrossReference, ProductImage
from .upc import build

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
FULL_SIZE = 1600
THUMB_SIZE = 240


# ----------------------------------------------------------------------------- images

def _encode(img, size):
    from PIL import Image

    img = img.copy()
    img.thumbnail((size, size), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85, optimize=True)
    return out.getvalue(), img.size


def add_image(product, uploaded, make_primary=False):
    from PIL import Image, ImageOps, UnidentifiedImageError

    if uploaded.size > MAX_UPLOAD_BYTES:
        raise ValidationError(f"{uploaded.name}: image is larger than 10 MB.")
    try:
        img = Image.open(uploaded)
        img = ImageOps.exif_transpose(img)
    except (UnidentifiedImageError, OSError):
        raise ValidationError(f"{uploaded.name}: not a recognised image file.")
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    data, (w, h) = _encode(img, FULL_SIZE)
    thumb, _ = _encode(img, THUMB_SIZE)
    first = not product.images.exists()
    with transaction.atomic():
        if make_primary or first:
            product.images.update(is_primary=False)
        return ProductImage.objects.create(product=product, data=data, thumbnail=thumb, width=w, height=h,
                                           filename=uploaded.name[:200], is_primary=make_primary or first)


def set_primary(image):
    with transaction.atomic():
        image.product.images.update(is_primary=False)
        image.is_primary = True
        image.save(update_fields=["is_primary"])


def delete_image(image):
    product, was_primary = image.product, image.is_primary
    image.delete()
    if was_primary:
        nxt = product.images.first()
        if nxt:
            set_primary(nxt)


# ----------------------------------------------------------------------------- UPC

@transaction.atomic
def generate_upc(product):
    """Assign the next UPC from the company prefix; skips numbers already in use."""
    company = Company.objects.select_for_update().get(pk=Company.get().pk)
    if product.barcode and ProductCrossReference.objects.filter(
            product=product, alt_type=ProductCrossReference.BARCODE, alternate_id=product.barcode).exists():
        raise ValidationError(f"{product.sku} already has UPC {product.barcode}.")
    for _ in range(1000):
        try:
            upc = build(company.upc_company_prefix, company.upc_next_item_ref)
        except ValueError as exc:
            raise ValidationError(str(exc))
        company.upc_next_item_ref += 1
        in_use = Product.objects.filter(barcode=upc).exists() or ProductCrossReference.objects.filter(
            alt_type=ProductCrossReference.BARCODE, alternate_id=upc).exists()
        if not in_use:
            break
    else:
        raise ValidationError("Could not find a free UPC number.")
    company.save(update_fields=["upc_next_item_ref"])
    if not product.barcode:
        product.barcode = upc
        product.save(update_fields=["barcode"])
    ProductCrossReference.objects.create(product=product, alt_type=ProductCrossReference.BARCODE,
                                         alternate_id=upc, uom=product.uom, description="Generated UPC")
    return upc


# ----------------------------------------------------------------------------- Excel export

def _new_workbook(out):
    """XlsxWriter workbook writing via temp files.

    XlsxWriter's in_memory mode names one embedded-image part with a leading slash
    ('/xl/richData/_rels/...'), which Excel rejects as a corrupt file.
    """
    import xlsxwriter

    return xlsxwriter.Workbook(out, {"in_memory": False})


def _sanitize_xlsx(data):
    """Guarantee valid zip part names (no leading '/'), repacking only if needed."""
    import zipfile

    src = zipfile.ZipFile(io.BytesIO(data))
    if not any(n.startswith("/") for n in src.namelist()):
        return data
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            dst.writestr(info.filename.lstrip("/"), src.read(info.filename))
    return out.getvalue()


def _lv(v):
    return str(v) if v else ""


def export_products_xlsx(products, image_mode="cell", include_costs=True):
    """Product list as .xlsx with each item's main image in the first column.

    image_mode="cell":  picture placed *in* the cell (Excel 365 / 2021+ "Place in Cell").
    image_mode="float": picture floating over the cell, moving/sizing with it (works in every Excel version).
    """
    company = Company.get()
    out = io.BytesIO()
    wb = _new_workbook(out)
    ws = wb.add_worksheet("Products")
    head = wb.add_format({"bold": True, "bg_color": "#E8ECF2", "border": 1, "text_wrap": True, "valign": "top"})
    cell = wb.add_format({"valign": "vcenter", "text_wrap": True})
    num = wb.add_format({"valign": "vcenter", "num_format": "#,##0.00"})
    num4 = wb.add_format({"valign": "vcenter", "num_format": "#,##0.0000"})
    intf = wb.add_format({"valign": "vcenter", "num_format": "#,##0"})
    pct = wb.add_format({"valign": "vcenter", "num_format": "0.00"})
    columns = [
        ("Image", 14, None), ("SKU", 14, cell), ("Description", 32, cell), ("Item Class", 30, cell),
        ("Tier 1 Class", 14, cell), ("Tier 2 Subclass", 16, cell), ("Tier 3 Product Type", 18, cell),
        ("Brand", 18, cell), ("Package Size", 14, cell), ("Season", 10, cell), ("Gender", 10, cell),
        ("Size Range", 18, cell), ("Size Breakdown", 16, cell), ("Material", 20, cell), ("Customer Code", 14, cell),
        ("HTS Code", 14, cell), ("Duty %", 8, pct), ("Tariff %", 8, pct), ("Prepack Qty", 9, intf),
        ("Carton Qty", 9, intf), ("Carton CBM", 10, num4), ("Carton CUFT", 10, num4),
    ] + [(f"Qty per {label}", 11, intf) for label, _ in company.containers] + [
        ("UPC", 15, cell), ("On Hand", 10, intf), ("Avg Landed Cost", 12, num4), ("List Price", 10, num),
    ]
    cost_cols = {"Avg Landed Cost"}
    keep = [i for i, c in enumerate(columns) if include_costs or c[0] not in cost_cols]
    columns = [columns[i] for i in keep]
    for c, (title, width, _) in enumerate(columns):
        ws.write(0, c, title, head)
        ws.set_column(c, c, width)
    ws.set_row(0, 30)
    ws.freeze_panes(1, 2)
    for r, p in enumerate(products, start=1):
        ws.set_row(r, 75)
        imgs = getattr(p, "export_images", None)
        img = (next((i for i in imgs if i.is_primary), imgs[0] if imgs else None)
               if imgs is not None else p.primary_image)
        if img:
            data = io.BytesIO(bytes(img.thumbnail))
            if image_mode == "float":
                # Row is 75pt (100px), column 14 chars (~103px): fit the thumbnail inside with a small margin
                longest = max(img.width, img.height) or 1
                scale = 92 / min(THUMB_SIZE, longest) if longest < THUMB_SIZE else 92 / THUMB_SIZE
                ws.insert_image(r, 0, f"{p.sku}.jpg", {"image_data": data, "x_scale": scale, "y_scale": scale,
                                                       "x_offset": 4, "y_offset": 4, "object_position": 1})
            else:
                ws.embed_image(r, 0, f"{p.sku}.jpg", {"image_data": data})
        ic = p.item_class
        values = [
            p.sku, p.name, ic.full_code if ic else "",
            _lv(ic and ic.tier(1) and ic.tier(1).description), _lv(ic and ic.tier(2) and ic.tier(2).description),
            _lv(ic and ic.description), _lv(p.brand), _lv(p.package_size), _lv(p.season and p.season.code),
            _lv(p.gender and p.gender.code), _lv(p.size_range), _lv(p.size_breakdown), _lv(p.material),
            _lv(p.customer_code), p.hs_code, float(p.duty_rate), float(p.tariff_rate), p.prepack_qty, p.carton_qty,
            float(p.carton_cbm), float(p.carton_cuft),
        ] + [p.qty_per(cbm) or "" for _, cbm in company.containers] + [
            p.barcode, float(getattr(p, "export_on_hand", None) if hasattr(p, "export_on_hand") else p.qty_on_hand),
            float(p.avg_cost), float(p.list_price),
        ]
        values = [values[i - 1] for i in keep[1:]]
        for c, v in enumerate(values, start=1):
            ws.write(r, c, v, columns[c][2])
    ws.autofilter(0, 0, max(len(products), 1), len(columns) - 1)
    wb.close()
    return _sanitize_xlsx(out.getvalue())


# ----------------------------------------------------------------------------- HTS table import / export

HTS_HEADERS = ["Item Class", "Material", "Gender", "HTS Code", "Duty Rate", "Tariff", "Notes"]


def export_hts_xlsx(rows):
    out = io.BytesIO()
    wb = _new_workbook(out)
    ws = wb.add_worksheet("HTS Codes")
    head = wb.add_format({"bold": True, "bg_color": "#E8ECF2", "border": 1})
    for c, (title, width) in enumerate(zip(HTS_HEADERS, [34, 22, 10, 16, 10, 10, 30])):
        ws.write(0, c, title, head)
        ws.set_column(c, c, width)
    for r, h in enumerate(rows, start=1):
        ws.write_row(r, 0, [h.item_class.full_code, h.material.code, h.gender.code if h.gender else "", h.hts_code,
                            float(h.duty_rate), float(h.tariff_rate), h.notes])
    ws.freeze_panes(1, 0)
    wb.close()
    return _sanitize_xlsx(out.getvalue())


def _item_class_by_code(full_code):
    parts = [p.strip() for p in str(full_code).split("-") if p.strip()]
    if len(parts) != 3:
        return None
    node = None
    for part in parts:
        node = ItemClass.objects.filter(parent=node, code__iexact=part).first()
        if node is None:
            return None
    return node


def _lookup(kind, code):
    code = str(code or "").strip()
    if not code:
        return None
    obj = LookupValue.objects.filter(kind=kind, code__iexact=code).first()
    return obj or LookupValue.objects.create(kind=kind, code=code)


def _dec(value):
    try:
        return Decimal(str(value).strip() or "0")
    except InvalidOperation:
        raise ValidationError(f"'{value}' is not a number")


@transaction.atomic
def import_hts_xlsx(fileobj):
    """Create/update HTS rows from an .xlsx with the export's columns. Returns (created, updated, errors)."""
    from openpyxl import load_workbook

    try:
        wb = load_workbook(fileobj, read_only=True, data_only=True)
    except Exception:
        raise ValidationError("Could not read the file — upload an .xlsx exported from this screen.")
    ws = wb.active
    rows = ws.iter_rows(values_only=True)
    header = [str(h or "").strip().lower() for h in next(rows, [])]
    try:
        idx = {name: header.index(name.lower()) for name in HTS_HEADERS[:6]}
    except ValueError:
        raise ValidationError(f"First row must contain the columns: {', '.join(HTS_HEADERS[:6])}.")
    notes_idx = header.index("notes") if "notes" in header else None
    created = updated = 0
    errors = []
    for n, row in enumerate(rows, start=2):
        if not row or not any(row):
            continue
        ic = _item_class_by_code(row[idx["Item Class"]] or "")
        if ic is None:
            errors.append(f"Row {n}: item class '{row[idx['Item Class']]}' not found (use TIER1 -TIER2 -TIER3).")
            continue
        material = _lookup("material", row[idx["Material"]])
        if material is None:
            errors.append(f"Row {n}: material is required.")
            continue
        gender = _lookup("gender", row[idx["Gender"]])
        try:
            duty, tariff = _dec(row[idx["Duty Rate"]] or 0), _dec(row[idx["Tariff"]] or 0)
        except ValidationError as exc:
            errors.append(f"Row {n}: {exc.messages[0]}")
            continue
        obj = HTSCode.objects.filter(item_class=ic, material=material, gender=gender).first()
        if obj is None:
            obj = HTSCode(item_class=ic, material=material, gender=gender)
            created += 1
        else:
            updated += 1
        obj.hts_code = str(row[idx["HTS Code"]] or "").strip()
        obj.duty_rate, obj.tariff_rate = duty, tariff
        if notes_idx is not None:
            obj.notes = str(row[notes_idx] or "")[:255]
        obj.save()
    return created, updated, errors
