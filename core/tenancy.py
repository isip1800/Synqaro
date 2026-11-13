"""Multi-company support.

Every business record belongs to a company (``company`` column). The company being worked in is held in a
context variable for the duration of a request (set by ``core.middleware.CompanyMiddleware``) or a
``use_company()`` block. ``TenantModel.objects`` only ever returns rows of the current company, so screens,
forms, reports and services are isolated without each query having to remember to filter.

Outside a request (migrations, management commands, tests) there is no current company: queries are
unfiltered and new rows go to the first company.
"""
import contextlib
import contextvars

from django.db import models

_current = contextvars.ContextVar("synqaro_company_id", default=None)
_fallback = {"id": None}


def current_company_id():
    """Company of the running request / block, else None."""
    return _current.get()


def default_company_id():
    """Company new rows are assigned to: the current one, or (outside a request) the first company."""
    cid = _current.get()
    if cid is not None:
        return cid
    if _fallback["id"] is None:
        from .models import Company

        _fallback["id"] = Company.get_default().pk
    return _fallback["id"]


def reset_fallback():
    _fallback["id"] = None


def activate(company_id):
    return _current.set(company_id)


def deactivate(token):
    _current.reset(token)


@contextlib.contextmanager
def use_company(company):
    """Run code as `company` (a Company or id), e.g. in scripts and tests."""
    token = _current.set(getattr(company, "pk", company))
    try:
        yield
    finally:
        _current.reset(token)


class CurrentCompany(models.Expression):
    """WHERE condition `company_id = <current company>` decided when the SQL is compiled, not when the queryset
    is built. Querysets created once and reused — form dropdowns, class attributes, module-level constants —
    therefore always show the company of the request that runs them. No company active: no restriction."""

    conditional = True
    output_field = models.BooleanField()

    def __init__(self, col=None):
        super().__init__()
        self.col = col

    def get_source_expressions(self):
        return [self.col] if self.col is not None else []

    def set_source_expressions(self, exprs):
        self.col = exprs[0] if exprs else None

    def resolve_expression(self, query=None, allow_joins=True, reuse=None, summarize=False, for_save=False):
        c = self.copy()
        c.col = models.F("company_id").resolve_expression(query, allow_joins, reuse, summarize, for_save)
        return c

    def as_sql(self, compiler, connection):
        cid = _current.get()
        if cid is None:
            return "1=1", []
        sql, params = compiler.compile(self.col)
        return f"{sql} = %s", [*params, cid]


class TenantQuerySet(models.QuerySet):
    pass


class TenantManager(models.Manager.from_queryset(TenantQuerySet)):
    """Only rows of the current company (all rows when no company is active), decided at query time."""

    def get_queryset(self):
        return super().get_queryset().filter(CurrentCompany())


class TenantModel(models.Model):
    company = models.ForeignKey("core.Company", null=True, blank=True, on_delete=models.CASCADE,
                                related_name="+", editable=False, db_index=True)

    objects = TenantManager()
    all_companies = models.Manager()

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self.company_id:
            self.company_id = default_company_id()
        super().save(*args, **kwargs)


def stamp(objs):
    """Set the company on objects about to be bulk_create()d (bulk_create skips save())."""
    cid = default_company_id()
    for o in objs:
        if not o.company_id:
            o.company_id = cid
    return objs
