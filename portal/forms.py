from django import forms
from django.contrib.auth import get_user_model

from core.forms import StyledModelForm, line_formset
from purchasing.forms import ShipmentLineForm
from purchasing.models import Shipment, ShipmentLine

VENDOR_SHIPMENT_STATUSES = [(Shipment.PLANNED, "Planned"), (Shipment.BOOKED, "Booked"),
                            (Shipment.IN_TRANSIT, "In transit / departed")]


class VendorShipmentForm(StyledModelForm):
    status = forms.ChoiceField(choices=VENDOR_SHIPMENT_STATUSES)

    class Meta:
        model = Shipment
        fields = ["status", "mode", "carrier", "vessel_voyage", "origin_port", "destination_port", "bill_of_lading",
                  "container_numbers", "etd", "eta", "notes"]


class VendorShipmentLineForm(ShipmentLineForm):
    def __init__(self, *args, vendor=None, **kwargs):
        super().__init__(*args, vendor=vendor, **kwargs)


def vendor_line_formset(vendor):
    base = line_formset(Shipment, ShipmentLine, ["po_line", "quantity", "cartons"], extra=4,
                        form=VendorShipmentLineForm)

    class FormSet(base):
        def get_form_kwargs(self, index):
            kwargs = super().get_form_kwargs(index)
            kwargs["vendor"] = vendor
            return kwargs

    return FormSet


class PortalContactForm(forms.Form):
    vendor = forms.ModelChoiceField(queryset=None)
    username = forms.CharField(max_length=150)
    first_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150, required=False)
    email = forms.EmailField(required=False)
    title = forms.CharField(max_length=100, required=False)
    password = forms.CharField(widget=forms.PasswordInput, min_length=8)
    can_submit_invoices = forms.BooleanField(required=False, initial=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from purchasing.models import Vendor

        self.fields["vendor"].queryset = Vendor.objects.filter(is_active=True)

    def clean_username(self):
        username = self.cleaned_data["username"]
        if get_user_model().objects.filter(username=username).exists():
            raise forms.ValidationError("That username is taken.")
        return username
