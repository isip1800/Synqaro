from django.urls import path

from . import documents, views
from .crud import crud_urls
from .models import Currency, EmailTemplate, ExchangeRate

app_name = "core"

urlpatterns = [
    path("company/", views.company, name="company"),
    path("columns/", views.columns_save, name="columns_save"),
    path("docs/<int:ct_id>/<int:obj_id>/files/upload/", documents.file_upload, name="file_upload"),
    path("docs/files/<int:pk>/", documents.file_download, name="file_download"),
    path("docs/files/<int:pk>/delete/", documents.file_delete, name="file_delete"),
    path("docs/<int:ct_id>/<int:obj_id>/notes/add/", documents.note_add, name="note_add"),
    path("docs/notes/<int:pk>/delete/", documents.note_delete, name="note_delete"),
    path("print/<str:key>/<int:pk>/", documents.print_document, name="print_document"),
    path("email/<str:key>/<int:pk>/", documents.email_compose, name="email_compose"),
    path("email/", views.email_settings, name="email_settings"),
    path("email/log/", views.EmailLogList.as_view(), name="email_log"),
    *crud_urls(EmailTemplate, prefix="email/templates/", fields=["kind", "subject", "body", "cc", "attach_pdf"],
               columns=(("Document", "kind"), ("Subject", "subject"), ("Attach PDF", "attach_pdf"),
                        ("Always CC", "cc")), detail=False),
    path("filters/save/", views.filter_save, name="filter_save"),
    path("filters/<int:pk>/delete/", views.filter_delete, name="filter_delete"),
    *crud_urls(Currency, prefix="currencies/", fields=["code", "name", "symbol"],
               columns=(("Code", "code"), ("Name", "name"), ("Symbol", "symbol")), detail=False),
    *crud_urls(ExchangeRate, prefix="rates/", fields=["currency", "date", "rate"], columns=(), detail=False,
               list_view=views.ExchangeRateList),
]
