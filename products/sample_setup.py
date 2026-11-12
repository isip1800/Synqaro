"""Sample product-setup data (based on a real apparel / home-goods importer's setup)."""
from decimal import Decimal as D

from django.db import transaction

from .models import HTSCode, ItemClass, LookupValue, Product

LOOKUPS = {
    "brand": [("KENSIEGIRL", "Kensie Girl"), ("HOMESTYLE", "Home Style"), ("PCI", "Pacific Coast Imports")],
    "package_size": [("1", "1 PC"), ("3", "3 PC Set"), ("6", "6 PC Set"), ("10", "10 PC Set"), ("24", "24 PC Set")],
    "season": [("Q421", "Q4-21"), ("Q126", "Q1-26"), ("Q226", "Q2-26"), ("Q326", "Q3-26"), ("Q426", "Q4-26"),
               ("BASIC", "Basic / never out of stock")],
    "seasonal_cutoff": [("SPR26", "Spring 2026 cutoff"), ("HOL26", "Holiday 2026 cutoff")],
    "gender": [("Girl", "Girl"), ("Boy", "Boy"), ("Neutral", "Neutral"), ("Women", "Women"), ("Men", "Men"),
               ("NA", "Not applicable")],
    "size_range": [("GIRLS-4-12", "Girls - 4-6-8-10-12"), ("BOYS-4-12", "Boys - 4-6-8-10-12"),
                   ("INFANT", "Infant 0-24M"), ("OS", "One size")],
    "size_breakdown": [("1-1-2-1-1", "1-1-2-1-1 = 6 PC"), ("1-2-2-1", "1-2-2-1 = 6 PC"), ("SOLID", "Solid pack")],
    "item_spec": [("STD", "Standard"), ("PREMIUM", "Premium")],
    "material": [("INT100COT", "Interlock 100% Cotton"), ("INTLCK60COT40PLY", "Interlock 60% Cotton 40% Poly"),
                 ("LUX&SHERPA", "Lux plush & sherpa"), ("LUXPLUSH", "Lux plush"), ("Microfiber", "Microfiber"),
                 ("Metal-Iron", "Metal - iron"), ("PEVA", "PEVA"), ("BAMBOO", "Bamboo"), ("LOOFAH", "Loofah"),
                 ("PVC", "PVC"), ("SS", "Stainless steel"), ("ALUM", "Aluminum"), ("SILICONE", "Silicone"),
                 ("NA", "Not applicable")],
    "customer_code": [("WINNERS", "Winners"), ("STOCK", "Open stock (all customers)")],
    "label": [("HANGTAG", "Hang tag"), ("LAW", "Law label"), ("UPCSTK", "UPC sticker")],
}

# (tier1 code, tier1 desc, tier2 code, tier2 desc, tier3 code, tier3 desc)
CLASSES = [
    ("INT", "Basics", "UNDERWEAR", "Underwear", "10PKPANTIE", "10 Pack Panties"),
    ("BABY", "Baby", "BABYACCESS", "Baby Accessories", "MITTENS", "Mittens"),
    ("BABY", "Baby", "BABYBATH", "Baby Bath", "BABYROBES", "Baby Robes"),
    ("BABY", "Baby", "BABYBATH", "Baby Bath", "TOWELSET", "Towel Set"),
    ("BABY", "Baby", "BABYBLANKE", "Baby Blankets", "STROLBLNKT", "Stroller Blanket"),
    ("BABY", "Baby", "BABYBLANKE", "Baby Blankets", "CRIBBLNKT", "Crib Blanket"),
    ("BATHHOME", "Bath Home", "RODS", "Rods", "SHRODS", "Shower Rods"),
    ("BATHHOME", "Bath Home", "LINERS", "Liners", "PEVALINERS", "PEVA Liners"),
    ("BATHHOME", "Bath Home", "SHHOOKS", "Shower Hooks", "SHOHOOKS", "Shower Hooks"),
    ("HOME", "Home", "BATH", "Bath", "BATHMATS", "Bath Mats"),
    ("HOME", "Home", "KITCHEN", "Kitchen", "COOKWARE", "Cookware"),
    ("HOME", "Home", "KITCHEN", "Kitchen", "UTENSILS", "Kitchen Utensils"),
    ("HOME", "Home", "LIGHTING", "Lighting", "LAMPS", "Lamps & Bulbs"),
    ("HARDWARE", "Hardware", "TOOLS", "Hand Tools", "SCREWDRV", "Screwdrivers"),
]

# (item class tier-3 path, material, gender or None, hts, duty, tariff)
HTS = [
    ("INT-UNDERWEAR-10PKPANTIE", "INT100COT", "Girl", "6108.21.00200", "7.80", "7.50"),
    ("BABY-BABYACCESS-MITTENS", "INTLCK60COT40PLY", "Girl", "6116.10.0800", "2.80", "7.50"),
    ("BABY-BABYBATH-BABYROBES", "LUX&SHERPA", "Girl", "6302.93.1000", "6.20", "0"),
    ("BABY-BABYBATH-BABYROBES", "LUXPLUSH", "Boy", "6302.93.1000", "6.20", "0"),
    ("BABY-BABYBATH-BABYROBES", "LUXPLUSH", "Neutral", "6302.93.1000", "6.20", "0"),
    ("BABY-BABYBATH-TOWELSET", "Microfiber", "Girl", "6302.93.1000", "6.20", "0"),
    ("BABY-BABYBLANKE-STROLBLNKT", "LUX&SHERPA", "Boy", "6301.40.0020", "8.50", "0"),
    ("BABY-BABYBLANKE-STROLBLNKT", "LUX&SHERPA", "Neutral", "6301.40.0020", "8.50", "0"),
    ("BABY-BABYBLANKE-STROLBLNKT", "LUXPLUSH", "Girl", "6301.40.0020", "8.50", "0"),
    ("BABY-BABYBLANKE-CRIBBLNKT", "LUXPLUSH", "Boy", "6301.40.0020", "8.50", "0"),
    ("BABY-BABYBLANKE-CRIBBLNKT", "LUXPLUSH", "Neutral", "6301.40.0020", "8.50", "0"),
    ("BATHHOME-RODS-SHRODS", "Metal-Iron", "NA", "7323.92.0040", "0", "7.50"),
    ("BATHHOME-LINERS-PEVALINERS", "PEVA", "NA", "3924.90.1010", "3.30", "0"),
    ("BATHHOME-SHHOOKS-SHOHOOKS", "NA", "NA", "7323.93.0080", "2.00", "0"),
    ("HOME-BATH-BATHMATS", "BAMBOO", "NA", "4420.19.0000", "3.20", "0"),
    ("HOME-BATH-BATHMATS", "LOOFAH", "NA", "3924.90.1050", "3.40", "0"),
    ("HOME-BATH-BATHMATS", "PVC", "NA", "3924.90.1050", "3.40", "0"),
    ("HOME-KITCHEN-COOKWARE", "SS", "NA", "7323.93.0035", "2.00", "0"),
    ("HOME-KITCHEN-COOKWARE", "ALUM", "NA", "7615.10.7130", "3.10", "0"),
    ("HOME-KITCHEN-UTENSILS", "SILICONE", "NA", "3924.10.4000", "3.40", "0"),
    ("HOME-LIGHTING-LAMPS", "NA", "NA", "9405.21.6010", "3.90", "0"),
    ("HOME-BATH-BATHMATS", "Microfiber", "NA", "", "0", "0"),  # waiting on info
    ("HARDWARE-TOOLS-SCREWDRV", "SS", "NA", "8205.40.0000", "6.20", "0"),
]

# Demo SKU -> (class path, material, brand, season, package size, carton qty, L, W, H inches, carton kg)
DEMO_PRODUCTS = {
    "CK-1001": ("HOME-KITCHEN-COOKWARE", "SS", "PCI", "BASIC", "1", 12, "24", "16", "14", "15.5"),
    "CK-1002": ("HOME-KITCHEN-COOKWARE", "ALUM", "PCI", "BASIC", "3", 6, "22", "18", "16", "15.0"),
    "CK-1003": ("HOME-KITCHEN-UTENSILS", "SILICONE", "HOMESTYLE", "BASIC", "6", 48, "20", "14", "12", "14.0"),
    "LT-2001": ("HOME-LIGHTING-LAMPS", "NA", "PCI", "Q426", "1", 8, "20", "18", "16", "11.0"),
    "LT-2002": ("HOME-LIGHTING-LAMPS", "NA", "PCI", "BASIC", "1", 48, "18", "12", "10", "9.5"),
    "TL-3001": ("HARDWARE-TOOLS-SCREWDRV", "SS", "PCI", "BASIC", "24", 36, "18", "14", "12", "16.0"),
}


def _class(path):
    node = None
    for code in path.split("-"):
        node = ItemClass.objects.get(parent=node, code=code)
    return node


@transaction.atomic
def load_sample_setup(enrich_demo_products=True):
    """Idempotently create sample lookups, item classes and HTS rows; fill in demo products."""
    for kind, values in LOOKUPS.items():
        for code, desc in values:
            LookupValue.objects.get_or_create(kind=kind, code=code, defaults={"description": desc})
    for t1, d1, t2, d2, t3, d3 in CLASSES:
        n1, _ = ItemClass.objects.get_or_create(parent=None, code=t1, defaults={"description": d1})
        n2, _ = ItemClass.objects.get_or_create(parent=n1, code=t2, defaults={"description": d2})
        ItemClass.objects.get_or_create(parent=n2, code=t3, defaults={"description": d3})
    lv = lambda kind, code: LookupValue.objects.get(kind=kind, code=code)  # noqa: E731
    for path, mat, gender, hts, duty, tariff in HTS:
        HTSCode.objects.get_or_create(
            item_class=_class(path), material=lv("material", mat), gender=lv("gender", gender) if gender else None,
            defaults={"hts_code": hts, "duty_rate": D(duty), "tariff_rate": D(tariff)},
        )
    if not enrich_demo_products:
        return
    for sku, (path, mat, brand, season, pkg, cqty, ln, wd, ht, kg) in DEMO_PRODUCTS.items():
        p = Product.objects.filter(sku=sku, item_class__isnull=True).first()
        if not p:
            continue
        p.item_class = _class(path)
        p.material, p.brand = lv("material", mat), lv("brand", brand)
        p.season, p.package_size = lv("season", season), lv("package_size", pkg)
        p.gender, p.size_range = lv("gender", "NA"), lv("size_range", "OS")
        p.customer_code = lv("customer_code", "STOCK")
        p.carton_qty, p.prepack_qty = cqty, 1
        p.carton_length, p.carton_width, p.carton_height, p.dimension_unit = D(ln), D(wd), D(ht), "in"
        p.carton_weight_kg = D(kg)
        p.save()
