from django.http import HttpResponse


class HealthCheckMiddleware:
    """Answer /healthz before host validation / HTTPS redirect so platform probes always succeed."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path == "/healthz":
            return HttpResponse("ok", content_type="text/plain")
        return self.get_response(request)


class CompanyMiddleware:
    """Pick the company for this request and make it current for every query (see core.tenancy).

    Staff choose among the companies they are members of (session 'company_id'); superusers can open any
    company; vendor-portal users always work in their vendor's company.
    """

    EXEMPT_PREFIXES = ("/login/", "/logout/", "/static/", "/healthz", "/settings/no-access/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from .tenancy import activate, deactivate

        request.company = request.membership = None
        request.perms = frozenset()
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return self.get_response(request)
        company_id = self.resolve(request, user)
        if company_id is None:
            if request.path.startswith(self.EXEMPT_PREFIXES):
                return self.get_response(request)
            from django.shortcuts import render

            return render(request, "core/no_access.html", {"title": "No company access"}, status=403)
        from .audit import reset_user, set_user

        token, user_token = activate(company_id), set_user(user)
        try:
            from .models import Company

            Company.clear_cache()  # company settings may have changed in another server process
            request.company = Company.get()
            return self.get_response(request)
        finally:
            reset_user(user_token)
            deactivate(token)

    def resolve(self, request, user):
        from .models import Company, Membership
        from .permissions import ALL

        contact = getattr(user, "vendor_contact", None) if hasattr(user, "vendor_contact") else None
        if contact is not None:
            from portal.models import VendorContact

            vc = VendorContact.all_companies.select_related("vendor").get(pk=contact.pk)
            return vc.company_id or vc.vendor.company_id
        memberships = list(Membership.objects.filter(user=user, is_active=True, company__is_active=True)
                           .select_related("company", "role"))
        wanted = request.session.get("company_id")
        chosen = next((m for m in memberships if m.company_id == wanted), None)
        if user.is_superuser:
            if chosen is None and wanted and Company.objects.filter(pk=wanted, is_active=True).exists():
                request.perms = frozenset(ALL)
                return wanted
            if chosen is None and not memberships:
                first = Company.objects.filter(is_active=True).order_by("pk").first()
                request.perms = frozenset(ALL)
                return first.pk if first else Company.get_default().pk
        if chosen is None and memberships:
            chosen = memberships[0]
        if chosen is None:
            return None
        request.membership = chosen
        request.perms = frozenset(ALL) if user.is_superuser else frozenset(chosen.role.permissions or [])
        request.session["company_id"] = chosen.company_id
        return chosen.company_id


class PermissionMiddleware:
    """Enforce core.permissions.required_permission for every internal page and action."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        from .permissions import required_permission

        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated or hasattr(user, "vendor_contact"):
            return None
        match = request.resolver_match
        if match is None:
            return None
        perm = required_permission(match.namespace, match.url_name or "", request.method)
        if not perm or perm in request.perms:
            return None
        from django.shortcuts import render

        from .permissions import PERMISSIONS

        label = dict((p[0], p[2]) for p in PERMISSIONS).get(perm, perm)
        return render(request, "core/no_permission.html", {"title": "Not allowed", "perm": perm, "label": label},
                      status=403)
