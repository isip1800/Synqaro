from django.contrib import admin

from .models import (
    HTSCode,
    ItemClass,
    LookupValue,
    PriceList,
    PriceListItem,
    Product,
    ProductCrossReference,
    ProductWarehouse,
    UnitOfMeasure,
    VendorProduct,
)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("sku", "name", "item_class", "brand", "list_price", "avg_cost", "is_active")
    search_fields = ("sku", "name", "barcode")
    list_filter = ("is_active", "brand", "season")


@admin.register(LookupValue)
class LookupValueAdmin(admin.ModelAdmin):
    list_display = ("kind", "code", "description", "is_active")
    list_filter = ("kind",)


admin.site.register([ItemClass, HTSCode, UnitOfMeasure, VendorProduct, PriceList, PriceListItem,
                     ProductCrossReference, ProductWarehouse])
