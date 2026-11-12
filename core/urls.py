from django.urls import path

from . import views
from .crud import crud_urls
from .models import Currency, ExchangeRate

app_name = "core"

urlpatterns = [
    path("company/", views.company, name="company"),
    *crud_urls(Currency, prefix="currencies/", fields=["code", "name", "symbol"],
               columns=(("Code", "code"), ("Name", "name"), ("Symbol", "symbol")), detail=False),
    *crud_urls(ExchangeRate, prefix="rates/", fields=["currency", "date", "rate"], columns=(), detail=False,
               list_view=views.ExchangeRateList),
]
