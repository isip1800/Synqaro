from django.contrib import admin

from .models import Account, CustomerInvoice, CustomerPayment, JournalEntry, JournalLine, VendorBill, VendorPayment


class JournalLineInline(admin.TabularInline):
    model = JournalLine
    extra = 0


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = ("number", "date", "source", "reference", "memo")
    inlines = [JournalLineInline]


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "account_type", "is_bank", "system_key", "is_active")


admin.site.register([CustomerInvoice, CustomerPayment, VendorBill, VendorPayment])
