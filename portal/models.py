from django.conf import settings
from django.db import models
from django.urls import reverse


class VendorContact(models.Model):
    """Links a login to a vendor so the person can use the vendor portal."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="vendor_contact")
    vendor = models.ForeignKey("purchasing.Vendor", on_delete=models.CASCADE, related_name="portal_contacts")
    title = models.CharField(max_length=100, blank=True)
    can_submit_invoices = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.user.get_username()} ({self.vendor})"

    def get_absolute_url(self):
        return reverse("portal:contact_list")
