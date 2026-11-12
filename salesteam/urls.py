from django.urls import path

from core.crud import crud_urls

from . import views
from .models import SalesRep

app_name = "salesteam"

urlpatterns = [
    *crud_urls(SalesRep, prefix="reps/",
               fields=["code", "name", "user", "email", "phone", "territory", "manager", "commission_rate",
                       "commission_basis", "commission_trigger", "monthly_target", "hire_date", "is_active"],
               columns=(), list_view=views.RepList, detail_view=views.RepDetail),
    path("reps/<int:pk>/pay-commissions/", views.pay_commissions, name="pay_commissions"),
    path("reps/<int:pk>/approve-commissions/", views.approve_commissions, name="approve_commissions"),
    path("performance/", views.performance, name="performance"),
    path("commissions/", views.CommissionList.as_view(), name="commission_list"),
]
