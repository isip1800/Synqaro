from django.contrib import admin

from .models import Company, Currency, ExchangeRate, Sequence

admin.site.site_header = "Synqaro ERP administration"
admin.site.register([Company, Currency, ExchangeRate, Sequence])
