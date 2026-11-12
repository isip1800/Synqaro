from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from core.views import dashboard

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", dashboard, name="dashboard"),
    path("settings/", include("core.urls")),
    path("products/", include("products.urls")),
    path("inventory/", include("inventory.urls")),
    path("purchasing/", include("purchasing.urls")),
    path("sales/", include("sales.urls")),
    path("accounting/", include("accounting.urls")),
    path("sales-team/", include("salesteam.urls")),
    path("portal/", include("portal.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
else:
    from django.contrib.auth.decorators import login_required
    from django.urls import re_path
    from django.views.static import serve

    urlpatterns += [re_path(r"^media/(?P<path>.*)$", login_required(serve),
                            {"document_root": settings.MEDIA_ROOT})]
