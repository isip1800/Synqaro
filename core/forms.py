from django import forms
from django.db import models
from django.forms import inlineformset_factory, modelform_factory


def formfield_callback(field, **kwargs):
    formfield = field.formfield(**kwargs)
    if formfield is None:
        return None
    if isinstance(field, models.DateTimeField):
        formfield.widget = forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M")
    elif isinstance(field, models.DateField):
        formfield.widget = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    elif isinstance(field, models.TextField):
        formfield.widget = forms.Textarea(attrs={"rows": 3})
    return formfield


class StyledModelForm(forms.ModelForm):
    formfield_callback = formfield_callback


class LineWarehouseForm(StyledModelForm):
    """Order line with a required warehouse, pre-filled with the default warehouse on new lines."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "warehouse" in self.fields:
            from inventory.models import Warehouse

            from .models import default_warehouse

            field = self.fields["warehouse"]
            field.required = True
            field.queryset = Warehouse.objects.filter(is_active=True) | Warehouse.objects.filter(
                pk=getattr(self.instance, "warehouse_id", None))
            if not self.instance.pk and not self.initial.get("warehouse"):
                wh = default_warehouse()
                if wh:
                    self.initial["warehouse"] = wh.pk


def model_form(model, fields, form=StyledModelForm):
    return modelform_factory(model, form=form, fields=fields, formfield_callback=formfield_callback)


def line_formset(parent, model, fields, extra=3, form=StyledModelForm, fk_name=None):
    return inlineformset_factory(
        parent,
        model,
        form=form,
        fields=fields,
        extra=extra,
        can_delete=True,
        fk_name=fk_name,
        formfield_callback=formfield_callback,
    )
