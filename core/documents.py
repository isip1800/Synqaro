"""Files, notes, printable documents (HTML / PDF) and email for any document."""
import io
import mimetypes

from django.conf import settings
from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST

from .mixins import internal_required
from .models import Attachment, Company, EmailLog, EmailTemplate, Note

MAX_FILE_BYTES = 20 * 1024 * 1024

# Documents that support printing / email: key -> (model path, print template, email kind)
DOCUMENTS = {
    "sales_order": ("sales.SalesOrder", "print/sales_order.html", "sales_order"),
    "purchase_order": ("purchasing.PurchaseOrder", "print/purchase_order.html", "purchase_order"),
    "invoice": ("accounting.CustomerInvoice", "print/invoice.html", "invoice"),
    "statement": ("sales.Customer", "print/statement.html", "statement"),
}

DEFAULT_TEMPLATES = {
    "sales_order": ("Order confirmation {number} — {company}",
                    "Hello {buyer},\n\nThank you for your order. Please find attached our confirmation {number} for "
                    "your PO {customer_po}.\nShip window: {ship_start} to {cancel_date}.\nOrder total: {currency} "
                    "{total}\n\nBest regards,\n{user}\n{company}"),
    "purchase_order": ("Purchase order {number} — {company}",
                       "Dear {vendor},\n\nPlease find attached purchase order {number} dated {date}.\nKindly confirm "
                       "the order and ready date through our vendor portal or by reply.\n\nBest regards,\n{user}\n"
                       "{company}"),
    "invoice": ("Invoice {number} from {company}",
                "Hello {buyer},\n\nPlease find attached invoice {number} for your PO {customer_po}.\nAmount due: "
                "{currency} {total}, due {due_date}.\n\nThank you for your business,\n{user}\n{company}"),
    "statement": ("Statement of account — {company}",
                  "Hello,\n\nPlease find attached your statement of account. Balance due: {currency} {total}.\n\n"
                  "Best regards,\n{user}\n{company}"),
}


def get_template(kind):
    tpl = EmailTemplate.objects.filter(kind=kind).first()
    if tpl is None:
        subject, body = DEFAULT_TEMPLATES[kind]
        tpl = EmailTemplate.objects.create(kind=kind, subject=subject, body=body)
    return tpl


def _model(key):
    from django.apps import apps

    if key not in DOCUMENTS:
        raise Http404
    return apps.get_model(DOCUMENTS[key][0])


def document_context(key, obj):
    """Context used by the print templates and email placeholders."""
    company = Company.get()
    ctx = {"company": company, "doc": obj, "key": key}
    if key == "statement":
        from accounting.models import JournalLine
        from accounting.services import acct

        lines = JournalLine.objects.filter(customer=obj, account=acct("AR")).select_related("entry") \
            .order_by("entry__date", "entry_id")
        running, rows = 0, []
        for ln in lines:
            running += ln.debit - ln.credit
            rows.append({"line": ln, "balance": running})
        ctx.update(rows=rows, balance=running)
    return ctx


def placeholders(key, obj, user):
    company = Company.get()
    vals = {"company": company.name, "number": getattr(obj, "number", ""), "currency": company.base_currency_id,
            "user": (user.get_full_name() or user.get_username()) if user else "", "customer": "", "vendor": "",
            "buyer": "", "customer_po": "", "date": "", "due_date": "", "ship_start": "", "cancel_date": "",
            "total": ""}
    if key == "sales_order":
        vals.update(customer=obj.customer.name, buyer=obj.buyer.name if obj.buyer else obj.customer.contact_name or "",
                    customer_po=obj.customer_po, date=obj.order_date, total=f"{obj.total:,.2f}",
                    ship_start=obj.ship_start_date or "", cancel_date=obj.cancel_date or "")
    elif key == "purchase_order":
        vals.update(vendor=obj.vendor.name, date=obj.order_date, total=f"{obj.total:,.2f}",
                    currency=obj.currency_id)
    elif key == "invoice":
        so = obj.sales_order
        vals.update(customer=obj.customer.name, date=obj.invoice_date, due_date=obj.due_date or "",
                    total=f"{obj.balance_due:,.2f}", customer_po=so.customer_po if so else "",
                    buyer=(so.buyer.name if so and so.buyer else obj.customer.contact_name or ""))
    elif key == "statement":
        vals.update(customer=obj.name, total=f"{obj.balance:,.2f}", buyer=obj.contact_name or "")
    return {k: ("" if v is None else str(v)) for k, v in vals.items()}


def fill(text, values):
    class Safe(dict):
        def __missing__(self, k):
            return "{" + k + "}"

    return text.format_map(Safe(values))


def default_recipients(key, obj):
    if key == "purchase_order":
        emails = [obj.vendor.email] + [c.user.email for c in obj.vendor.portal_contacts.select_related("user")]
    elif key == "sales_order":
        emails = [obj.buyer.email if obj.buyer else "", obj.customer.email]
    elif key == "invoice":
        so = obj.sales_order
        emails = [so.buyer.email if so and so.buyer else "", obj.customer.email]
    else:
        emails = [obj.email]
    seen, out = set(), []
    for e in emails:
        if e and e.lower() not in seen:
            seen.add(e.lower())
            out.append(e)
    return out[:3]


def render_html(key, obj):
    return render_to_string(DOCUMENTS[key][1], document_context(key, obj))


def render_pdf(key, obj):
    from xhtml2pdf import pisa

    out = io.BytesIO()
    html = render_to_string(DOCUMENTS[key][1], {**document_context(key, obj), "pdf": True})
    result = pisa.CreatePDF(html, dest=out, encoding="utf-8")
    if result.err:
        raise ValidationError("Could not create the PDF.")
    return out.getvalue()


def pdf_filename(key, obj):
    label = {"sales_order": "Order", "purchase_order": "PO", "invoice": "Invoice", "statement": "Statement"}[key]
    ident = getattr(obj, "number", None) or getattr(obj, "code", "")
    return f"{label}-{ident}.pdf"


def send_document_email(key, obj, to, cc, subject, body, attach_pdf=True, attachment_ids=(), user=None):
    """Send (or log as not configured). Returns the EmailLog."""
    to = [e.strip() for e in to if e.strip()]
    cc = [e.strip() for e in cc if e.strip()]
    if not to:
        raise ValidationError("Enter at least one recipient.")
    msg = EmailMessage(subject=subject, body=body, from_email=settings.DEFAULT_FROM_EMAIL, to=to, cc=cc)
    names = []
    if attach_pdf:
        name = pdf_filename(key, obj)
        msg.attach(name, render_pdf(key, obj), "application/pdf")
        names.append(name)
    ct = ContentType.objects.get_for_model(obj)
    for att in Attachment.objects.filter(pk__in=attachment_ids, content_type=ct, object_id=obj.pk):
        msg.attach(att.filename, bytes(att.data), att.mime_type)
        names.append(att.filename)
    log = EmailLog(content_type=ct, object_id=obj.pk, document=str(getattr(obj, "number", obj)), to=", ".join(to),
                   cc=", ".join(cc), subject=subject, body=body, attachments="\n".join(names), sent_by=user)
    if not settings.EMAIL_CONFIGURED:
        log.status = EmailLog.NOT_CONFIGURED
        log.error = "Outgoing email is not set up (EMAIL_HOST is missing)."
    else:
        try:
            msg.send(fail_silently=False)
            log.status = EmailLog.SENT
        except Exception as exc:  # network / auth errors are recorded, not raised
            log.status, log.error = EmailLog.FAILED, str(exc)[:2000]
    log.save()
    return log


# ----------------------------------------------------------------------------- views

def _target(ct_id, obj_id):
    ct = get_object_or_404(ContentType, pk=ct_id)
    model = ct.model_class()
    if model is None:
        raise Http404
    obj = get_object_or_404(model._default_manager, pk=obj_id)  # company-scoped
    return ct, obj


def _back(obj, anchor="#notes-files"):
    return redirect(obj.get_absolute_url() + anchor)


@internal_required
@require_POST
def file_upload(request, ct_id, obj_id):
    ct, obj = _target(ct_id, obj_id)
    files = request.FILES.getlist("files")
    if not files:
        messages.error(request, "Choose a file to upload.")
    for f in files:
        if f.size > MAX_FILE_BYTES:
            messages.error(request, f"{f.name} is larger than 20 MB.")
            continue
        Attachment.objects.create(content_type=ct, object_id=obj.pk, filename=f.name[:255],
                                  mime_type=f.content_type or mimetypes.guess_type(f.name)[0] or
                                  "application/octet-stream", size=f.size, data=f.read(),
                                  description=request.POST.get("description", "")[:255], uploaded_by=request.user)
        messages.success(request, f"Uploaded {f.name}.")
    return _back(obj)


@internal_required
def file_download(request, pk):
    att = get_object_or_404(Attachment, pk=pk)
    resp = HttpResponse(bytes(att.data), content_type=att.mime_type)
    disposition = "inline" if att.mime_type.startswith(("image/", "application/pdf")) else "attachment"
    resp["Content-Disposition"] = f'{disposition}; filename="{att.filename}"'
    return resp


@internal_required
@require_POST
def file_delete(request, pk):
    att = get_object_or_404(Attachment, pk=pk)
    _, obj = _target(att.content_type_id, att.object_id)
    att.delete()
    messages.success(request, f"Deleted {att.filename}.")
    return _back(obj)


@internal_required
@require_POST
def note_add(request, ct_id, obj_id):
    ct, obj = _target(ct_id, obj_id)
    text = request.POST.get("text", "").strip()
    if text:
        Note.objects.create(content_type=ct, object_id=obj.pk, text=text[:5000], author=request.user)
    return _back(obj)


@internal_required
@require_POST
def note_delete(request, pk):
    note = get_object_or_404(Note, pk=pk)
    _, obj = _target(note.content_type_id, note.object_id)
    if note.author_id == request.user.id or request.user.is_superuser:
        note.delete()
    return _back(obj)


DOCUMENT_PERMS = {"sales_order": "sales.view", "purchase_order": "costs.view", "invoice": "accounting.view",
                  "statement": "accounting.view"}


def _check_doc_perm(request, key):
    from django.core.exceptions import PermissionDenied

    need = DOCUMENT_PERMS.get(key)
    if need and need not in getattr(request, "perms", ()):
        raise PermissionDenied(f"Needs permission {need}")


@internal_required
def print_document(request, key, pk):
    _check_doc_perm(request, key)
    obj = get_object_or_404(_model(key), pk=pk)
    if request.GET.get("format") == "pdf":
        resp = HttpResponse(render_pdf(key, obj), content_type="application/pdf")
        resp["Content-Disposition"] = f'inline; filename="{pdf_filename(key, obj)}"'
        return resp
    return HttpResponse(render_html(key, obj))  # browser view (PDF via ?format=pdf)


@internal_required
def email_compose(request, key, pk):
    _check_doc_perm(request, key)
    obj = get_object_or_404(_model(key), pk=pk)
    kind = DOCUMENTS[key][2]
    tpl = get_template(kind)
    values = placeholders(key, obj, request.user)
    ct = ContentType.objects.get_for_model(obj)
    files = Attachment.objects.filter(content_type=ct, object_id=obj.pk).defer("data")
    if request.method == "POST":
        split = lambda v: [x for x in v.replace(";", ",").split(",")]  # noqa: E731
        try:
            log = send_document_email(
                key, obj, split(request.POST.get("to", "")), split(request.POST.get("cc", "")),
                request.POST.get("subject", ""), request.POST.get("body", ""),
                attach_pdf=bool(request.POST.get("attach_pdf")),
                attachment_ids=[int(x) for x in request.POST.getlist("files") if x.isdigit()], user=request.user)
            if log.status == EmailLog.SENT:
                messages.success(request, f"Email sent to {log.to}.")
            elif log.status == EmailLog.NOT_CONFIGURED:
                messages.warning(request, "Email is not set up yet, so nothing was sent. The message was saved in "
                                          "the email log. See Settings → Email for how to connect your mailbox.")
            else:
                messages.error(request, f"Sending failed: {log.error}")
            return redirect(obj.get_absolute_url())
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
    cc = [c.strip() for c in tpl.cc.split(",") if c.strip()]
    if request.user.email:
        cc.append(request.user.email)
    return render(request, "core/email_compose.html", {
        "title": f"Email {getattr(obj, 'number', obj)}", "obj": obj, "key": key,
        "to": ", ".join(default_recipients(key, obj)), "cc": ", ".join(cc),
        "subject": fill(tpl.subject, values), "body": fill(tpl.body, values), "attach_pdf": tpl.attach_pdf,
        "files": files, "pdf_name": pdf_filename(key, obj), "configured": settings.EMAIL_CONFIGURED,
        "history": EmailLog.objects.filter(content_type=ct, object_id=obj.pk)[:10],
    })
