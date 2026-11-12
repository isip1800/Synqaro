from django.urls import path

from . import views
from .crud import crud_urls
from .models import Currency, ExchangeRate

app_name = "core"

urlpatterns = [
    path("company/", views.company, name="company"),
    path("columns/", views.columns_save, name="columns_save"),
    path("filters/save/", views.filter_save, name="filter_save"),
    path("filters/<int:pk>/delete/", views.filter_delete, name="filter_delete"),
    *crud_urls(Currency, prefix="currencies/", fields=["code", "name", "symbol"],
               columns=(("Code", "code"), ("Name", "name"), ("Symbol", "symbol")), detail=False),
    *crud_urls(ExchangeRate, prefix="rates/", fields=["currency", "date", "rate"], columns=(), detail=False,
               list_view=views.ExchangeRateList),
]
