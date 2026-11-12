from django.urls import path

from core.crud import action_view

from . import services, views
from .models import CustomerInvoice, CustomerPayment, VendorBill, VendorPayment

app_name = "accounting"

urlpatterns = [
    path("accounts/", views.AccountList.as_view(), name="account_list"),
    path("accounts/new/", views.AccountCreate.as_view(), name="account_create"),
    path("accounts/<int:pk>/", views.account_detail, name="account_detail"),
    path("accounts/<int:pk>/edit/", views.AccountUpdate.as_view(), name="account_update"),
    path("journal/", views.JournalList.as_view(), name="journalentry_list"),
    path("journal/new/", views.JournalCreate.as_view(), name="journalentry_create"),
    path("journal/<int:pk>/", views.JournalDetail.as_view(), name="journalentry_detail"),
    path("journal/<int:pk>/reverse/", views.journal_reverse, name="journal_reverse"),
    # Receivables
    path("invoices/", views.InvoiceList.as_view(), name="customerinvoice_list"),
    path("invoices/new/", views.InvoiceCreate.as_view(), name="customerinvoice_create"),
    path("invoices/<int:pk>/", views.InvoiceDetail.as_view(), name="customerinvoice_detail"),
    path("invoices/<int:pk>/edit/", views.InvoiceUpdate.as_view(), name="customerinvoice_update"),
    path("invoices/<int:pk>/post/", action_view(CustomerInvoice, services.post_customer_invoice,
                                                "Invoice {obj} posted to the ledger."), name="invoice_post"),
    path("customer-payments/", views.CustomerPaymentList.as_view(), name="customerpayment_list"),
    path("customer-payments/new/", views.CustomerPaymentCreate.as_view(), name="customerpayment_create"),
    path("customer-payments/<int:pk>/", views.CustomerPaymentDetail.as_view(), name="customerpayment_detail"),
    path("customer-payments/<int:pk>/edit/", views.CustomerPaymentUpdate.as_view(), name="customerpayment_update"),
    path("customer-payments/<int:pk>/auto-allocate/", views.auto_allocate_customer,
         name="customer_payment_auto_allocate"),
    path("customer-payments/<int:pk>/post/", action_view(CustomerPayment, services.post_customer_payment,
                                                         "Payment {obj} posted."), name="customer_payment_post"),
    # Payables
    path("bills/", views.BillList.as_view(), name="vendorbill_list"),
    path("bills/new/", views.BillCreate.as_view(), name="vendorbill_create"),
    path("bills/<int:pk>/", views.BillDetail.as_view(), name="vendorbill_detail"),
    path("bills/<int:pk>/edit/", views.BillUpdate.as_view(), name="vendorbill_update"),
    path("bills/<int:pk>/post/", action_view(VendorBill, services.post_vendor_bill, "Bill {obj} posted."),
         name="bill_post"),
    path("vendor-payments/", views.VendorPaymentList.as_view(), name="vendorpayment_list"),
    path("vendor-payments/new/", views.VendorPaymentCreate.as_view(), name="vendorpayment_create"),
    path("vendor-payments/<int:pk>/", views.VendorPaymentDetail.as_view(), name="vendorpayment_detail"),
    path("vendor-payments/<int:pk>/edit/", views.VendorPaymentUpdate.as_view(), name="vendorpayment_update"),
    path("vendor-payments/<int:pk>/auto-allocate/", views.auto_allocate_vendor, name="vendor_payment_auto_allocate"),
    path("vendor-payments/<int:pk>/post/", action_view(VendorPayment, services.post_vendor_payment,
                                                       "Payment {obj} posted."), name="vendor_payment_post"),
    path("void/<str:kind>/<int:pk>/", views.void_draft, name="void_draft"),
    # Reports
    path("reports/", views.reports, name="reports"),
    path("reports/trial-balance/", views.trial_balance, name="trial_balance"),
    path("reports/profit-and-loss/", views.income_statement, name="income_statement"),
    path("reports/balance-sheet/", views.balance_sheet, name="balance_sheet"),
    path("reports/ar-aging/", views.ar_aging, name="ar_aging"),
    path("reports/ap-aging/", views.ap_aging, name="ap_aging"),
]
