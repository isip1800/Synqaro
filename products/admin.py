from django.contrib import admin

from .models import Category, PriceList, PriceListItem, Product, UnitOfMeasure, VendorProduct


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("sku", "name", "category", "list_price", "avg_cost", "is_active")
    search_fields = ("sku", "name")
    list_filter = ("category", "is_active")


admin.site.register([Category, UnitOfMeasure, VendorProduct, PriceList, PriceListItem])
