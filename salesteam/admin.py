from django.contrib import admin

from .models import Commission, CommissionPayout, SalesRep

admin.site.register([SalesRep, Commission, CommissionPayout])
