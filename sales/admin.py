from django.contrib import admin

from .models import Customer, Delivery, SalesOrder


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "sales_rep", "credit_limit", "is_active")
    search_fields = ("code", "name")


admin.site.register([SalesOrder, Delivery])
