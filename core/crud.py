"""Small generic CRUD layer used by every module for master data and documents."""
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import path, reverse
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from .forms import model_form
from .mixins import InternalRequiredMixin


class ErpListView(InternalRequiredMixin, ListView):
    template_name = "core/list.html"
    paginate_by = 50
    columns = ()  # default visible columns: sequence of (label, accessor)
    available_columns = None  # optional larger set the user can choose from (column chooser)
    cost_columns = ()  # accessors only shown to users with the costs.view permission
    screen_key = None
    search_fields = ()
    filters = ()  # sequence of (param, label, choices) applied as exact filters
    title = None
    create_url = None
    select_related = ()

    def get_queryset(self):
        qs = super().get_queryset()
        if self.select_related:
            qs = qs.select_related(*self.select_related)
        q = self.request.GET.get("q", "").strip()
        if q and self.search_fields:
            cond = Q()
            for f in self.search_fields:
                cond |= Q(**{f"{f}__icontains": q})
            qs = qs.filter(cond)
        for param, _label, _choices in self.get_filters():
            val = self.request.GET.get(param)
            if val:
                qs = qs.filter(**{param: val})
        return qs

    def get_filters(self):
        return self.filters

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        meta = self.model._meta
        from .columns import chooser_context, chosen_columns

        available = list(self.available_columns or self.columns)
        if self.cost_columns and "costs.view" not in getattr(self.request, "perms", ()):
            available = [c for c in available if c[1] not in self.cost_columns]
            self.columns = [c for c in self.columns if c[1] not in self.cost_columns]
        if available:
            screen = self.screen_key or f"list:{meta.app_label}.{meta.model_name}"
            keys = chosen_columns(self.request, screen, [a for _, a in available], [a for _, a in self.columns])
            by_key = dict((a, label) for label, a in available)
            columns = [(by_key[k], k) for k in keys]
            ctx.update(chooser_context(self.request, screen, [(a, label) for label, a in available], keys))
        else:
            columns = self.columns
        create_url = str(self.create_url) if self.create_url else None
        if create_url:
            from django.urls import resolve

            from .permissions import required_permission

            match = resolve(create_url)
            need = required_permission(match.namespace, match.url_name or "", "POST")
            if need and need not in getattr(self.request, "perms", ()):
                create_url = None
        ctx.update(
            title=self.title or meta.verbose_name_plural.title(),
            columns=columns,
            create_url=create_url,
            search_enabled=bool(self.search_fields),
            q=self.request.GET.get("q", ""),
            filters=[
                (param, label, choices, self.request.GET.get(param, ""))
                for param, label, choices in self.get_filters()
            ],
        )
        return ctx


class ErpFormMixin(InternalRequiredMixin):
    template_name = "core/form.html"
    title = None
    formset_class = None
    locked_message = "This document is no longer a draft and cannot be edited."

    def is_locked(self, obj):
        status = getattr(obj, "status", None)
        return status is not None and status not in getattr(obj, "EDITABLE_STATUSES", (status,))

    def get_formset(self):
        if not self.formset_class:
            return None
        kwargs = {"instance": self.object if self.object and self.object.pk else None, "prefix": "lines"}
        if self.request.method == "POST":
            kwargs.update(data=self.request.POST, files=self.request.FILES)
        return self.formset_class(**kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if "formset" not in ctx:
            ctx["formset"] = self.get_formset()
        if ctx["formset"] is not None:
            meta = ctx["formset"].model._meta
            ctx["formset_title"] = "Lines" if meta.model_name.endswith("line") else meta.verbose_name_plural.title()
        verb = "Edit" if self.object and self.object.pk else "New"
        ctx["title"] = self.title or f"{verb} {self.model._meta.verbose_name.title()}"
        ctx["cancel_url"] = (
            self.object.get_absolute_url() if self.object and self.object.pk else self.request.GET.get("next", "")
        )
        return ctx

    def get_initial(self):
        initial = super().get_initial()
        for key, value in self.request.GET.items():
            if key in self.get_form_class().base_fields:
                initial[key] = value
        return initial

    def form_valid(self, form):
        formset = self.get_formset()
        if formset is not None and not formset.is_valid():
            return self.render_to_response(self.get_context_data(form=form, formset=formset))
        try:
            with transaction.atomic():
                self.before_save(form)
                self.object = form.save()
                if formset is not None:
                    formset.instance = self.object
                    formset.save()
                self.after_save(form)
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.render_to_response(self.get_context_data(form=form, formset=formset))
        except IntegrityError:
            form.add_error(None, "That code / number is already used in this company.")
            return self.render_to_response(self.get_context_data(form=form, formset=formset))
        messages.success(self.request, f"{self.model._meta.verbose_name.title()} {self.object} saved.")
        return redirect(self.get_success_url())

    def before_save(self, form):
        if hasattr(form.instance, "created_by_id") and not form.instance.pk and self.request.user.is_authenticated:
            form.instance.created_by = self.request.user

    def after_save(self, form):
        pass

    def get_success_url(self):
        return self.object.get_absolute_url()


class ErpCreateView(ErpFormMixin, CreateView):
    pass


class ErpUpdateView(ErpFormMixin, UpdateView):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            obj = self.get_object()
            if self.is_locked(obj):
                messages.error(request, self.locked_message)
                return redirect(obj.get_absolute_url())
        return super().dispatch(request, *args, **kwargs)


class ErpDetailView(InternalRequiredMixin, DetailView):
    template_name = "core/detail.html"
    fields = ()  # (label, accessor)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["fields"] = self.fields
        ctx["title"] = f"{self.model._meta.verbose_name.title()} {self.object}"
        return ctx


def crud_urls(model, *, fields, columns, prefix="", search_fields=(), filters=(), detail=True, list_view=None,
              detail_view=None, create_view=None, update_view=None, detail_fields=None, ordering=None,
              select_related=(), formset_class=None):
    """Return url patterns for a model's list/create/detail/update views."""
    name = model._meta.model_name
    app = model._meta.app_label
    from django.db.models import AutoField, BigAutoField

    conv = "int" if isinstance(model._meta.pk, (AutoField, BigAutoField)) else "str"
    form_class = model_form(model, fields)

    list_cls = list_view or type(
        f"{model.__name__}List",
        (ErpListView,),
        {
            "model": model,
            "columns": columns,
            "search_fields": search_fields,
            "filters": filters,
            "select_related": select_related,
            "ordering": ordering,
        },
    )

    def _create_url(self):
        return reverse(f"{app}:{name}_create")

    list_cls.create_url = property(_create_url) if create_view is not False else None

    patterns = [path(prefix, list_cls.as_view(), name=f"{name}_list")]
    if create_view is not False:
        create_cls = create_view or type(
            f"{model.__name__}Create",
            (ErpCreateView,),
            {"model": model, "form_class": form_class, "formset_class": formset_class},
        )
        patterns.append(path(prefix + "new/", create_cls.as_view(), name=f"{name}_create"))
    if update_view is not False:
        update_cls = update_view or type(
            f"{model.__name__}Update",
            (ErpUpdateView,),
            {"model": model, "form_class": form_class, "formset_class": formset_class},
        )
        patterns.append(path(prefix + f"<{conv}:pk>/edit/", update_cls.as_view(), name=f"{name}_update"))
    if detail:
        detail_cls = detail_view or type(
            f"{model.__name__}Detail",
            (ErpDetailView,),
            {"model": model, "fields": detail_fields or [(model._meta.get_field(f).verbose_name.title(), f)
                                                          for f in fields]},
        )
        patterns.append(path(prefix + f"<{conv}:pk>/", detail_cls.as_view(), name=f"{name}_detail"))
    return patterns


def run_action(request, obj, func, success, *args, **kwargs):
    """Run a service function on POST, surfacing ValidationErrors as flash messages."""
    try:
        with transaction.atomic():
            result = func(*args, **kwargs)
        messages.success(request, success)
        return result
    except ValidationError as exc:
        for msg in exc.messages:
            messages.error(request, msg)
    return None


def get_or_404(model, pk):
    return get_object_or_404(model, pk=pk)


def action_view(model, func, success, pass_user=True):
    """POST-only view running `func(obj[, user=])` and redirecting back to the object."""
    from django.views.decorators.http import require_POST

    from .mixins import internal_required

    @internal_required
    @require_POST
    def view(request, pk):
        obj = get_object_or_404(model, pk=pk)
        kwargs = {"user": request.user} if pass_user else {}
        result = run_action(request, obj, func, success.format(obj=obj), obj, **kwargs)
        target = result if hasattr(result, "get_absolute_url") else obj
        return redirect(target.get_absolute_url())

    return view
