from django.contrib import admin

from .models import GoodsReceipt, LandedCost, PurchaseOrder, Shipment, ShipmentDocument, Vendor


@admin.register(Vendor)
class VendorAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "vendor_type", "country", "currency", "is_active")
    search_fields = ("code", "name")


admin.site.register([PurchaseOrder, Shipment, LandedCost, ShipmentDocument, GoodsReceipt])
