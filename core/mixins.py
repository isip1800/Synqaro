from functools import wraps

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect


def is_vendor_user(user):
    return user.is_authenticated and hasattr(user, "vendor_contact")


class InternalRequiredMixin(LoginRequiredMixin):
    """Staff-side views: authenticated users who are not vendor-portal users."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and is_vendor_user(request.user):
            return redirect("portal:dashboard")
        return super().dispatch(request, *args, **kwargs)


def internal_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if is_vendor_user(request.user):
            return redirect("portal:dashboard")
        return view(request, *args, **kwargs)

    return wrapper


def vendor_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not is_vendor_user(request.user):
            raise PermissionDenied("Vendor portal access only.")
        request.vendor = request.user.vendor_contact.vendor
        return view(request, *args, **kwargs)

    return wrapper
