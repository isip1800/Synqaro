from django.urls import path

from . import views

app_name = "returns"

urlpatterns = [
    path("customer-returns/", views.CustomerReturnList.as_view(), name="customerreturn_list"),
    path("customer-returns/new/", views.CustomerReturnCreate.as_view(), name="customerreturn_create"),
    path("customer-returns/<int:pk>/", views.CustomerReturnDetail.as_view(), name="customerreturn_detail"),
    path("customer-returns/<int:pk>/edit/", views.CustomerReturnUpdate.as_view(), name="customerreturn_update"),
    path("customer-returns/<int:pk>/receive/", views.customerreturn_receive, name="customerreturn_receive"),
    path("customer-returns/<int:pk>/credit/", views.customerreturn_credit, name="customerreturn_credit"),
    path("customer-returns/<int:pk>/cancel/", views.customerreturn_cancel, name="customerreturn_cancel"),
    path("credit-memos/", views.CreditMemoList.as_view(), name="creditmemo_list"),
    path("credit-memos/new/", views.CreditMemoCreate.as_view(), name="creditmemo_create"),
    path("credit-memos/<int:pk>/", views.CreditMemoDetail.as_view(), name="creditmemo_detail"),
    path("credit-memos/<int:pk>/edit/", views.CreditMemoUpdate.as_view(), name="creditmemo_update"),
    path("credit-memos/<int:pk>/post/", views.creditmemo_post, name="creditmemo_post"),
    path("credit-memos/<int:pk>/apply/", views.creditmemo_apply, name="creditmemo_apply"),
    path("credit-memos/<int:pk>/void/", views.creditmemo_void, name="creditmemo_void"),
    path("chargebacks/", views.ChargebackList.as_view(), name="chargeback_list"),
    path("chargebacks/new/", views.ChargebackCreate.as_view(), name="chargeback_create"),
    path("chargebacks/report/", views.chargeback_report, name="chargeback_report"),
    path("chargebacks/<int:pk>/", views.ChargebackDetail.as_view(), name="chargeback_detail"),
    path("chargebacks/<int:pk>/edit/", views.ChargebackUpdate.as_view(), name="chargeback_update"),
    path("chargebacks/<int:pk>/<str:action>/", views.chargeback_action, name="chargeback_action"),
    path("vendor-returns/", views.VendorReturnList.as_view(), name="vendorreturn_list"),
    path("vendor-returns/new/", views.VendorReturnCreate.as_view(), name="vendorreturn_create"),
    path("vendor-returns/<int:pk>/", views.VendorReturnDetail.as_view(), name="vendorreturn_detail"),
    path("vendor-returns/<int:pk>/edit/", views.VendorReturnUpdate.as_view(), name="vendorreturn_update"),
    path("vendor-returns/<int:pk>/ship/", views.vendorreturn_ship, name="vendorreturn_ship"),
    path("vendor-returns/<int:pk>/credit/", views.vendorreturn_credit, name="vendorreturn_credit"),
    path("vendor-returns/<int:pk>/cancel/", views.vendorreturn_cancel, name="vendorreturn_cancel"),
    path("vendor-credits/", views.VendorCreditList.as_view(), name="vendorcredit_list"),
    path("vendor-credits/new/", views.VendorCreditCreate.as_view(), name="vendorcredit_create"),
    path("vendor-credits/<int:pk>/", views.VendorCreditDetail.as_view(), name="vendorcredit_detail"),
    path("vendor-credits/<int:pk>/edit/", views.VendorCreditUpdate.as_view(), name="vendorcredit_update"),
    path("vendor-credits/<int:pk>/post/", views.vendorcredit_post, name="vendorcredit_post"),
    path("vendor-credits/<int:pk>/apply/", views.vendorcredit_apply, name="vendorcredit_apply"),
    path("vendor-credits/<int:pk>/void/", views.vendorcredit_void, name="vendorcredit_void"),
]
