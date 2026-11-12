from django.contrib import admin

from .models import StockAdjustment, StockMove, StockQuant, StockTransfer, Warehouse

admin.site.register([Warehouse, StockQuant, StockMove, StockAdjustment, StockTransfer])
