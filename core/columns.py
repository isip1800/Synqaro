"""Column chooser and saved filters shared by list screens."""
from urllib.parse import urlencode

from django.db.models import Q

from .models import SavedFilter, UserPreference

IGNORED_QUERY_KEYS = {"page", "csrfmiddlewaretoken"}


def chosen_columns(request, screen, available_keys, default_keys):
    """Visible column keys for this user/screen, in the screen's own order."""
    pref = None
    if request.user.is_authenticated:
        pref = UserPreference.objects.filter(user=request.user, key=f"columns:{screen}").first()
    keys = set(pref.value.get("visible", [])) & set(available_keys) if pref else set(default_keys)
    if not keys:
        keys = set(default_keys)
    return [k for k in available_keys if k in keys]


def save_columns(user, screen, keys):
    UserPreference.objects.update_or_create(user=user, key=f"columns:{screen}", defaults={"value": {"visible": keys}})


def reset_columns(user, screen):
    UserPreference.objects.filter(user=user, key=f"columns:{screen}").delete()


def saved_filters(request, screen):
    if not request.user.is_authenticated:
        return SavedFilter.objects.none()
    return SavedFilter.objects.filter(screen=screen).filter(Q(user=request.user) | Q(shared=True))


def clean_query(querydict):
    pairs = [(k, v) for k, vals in querydict.lists() for v in vals if k not in IGNORED_QUERY_KEYS and v != ""]
    return urlencode(pairs)


def chooser_context(request, screen, options, visible_keys):
    """Context for the Columns / Saved filters widgets. options: [(key, label)]."""
    return {
        "chooser": {
            "screen": screen,
            "options": [(k, label, k in visible_keys) for k, label in options],
            "next": request.get_full_path(),
            "filters": saved_filters(request, screen),
            "query": clean_query(request.GET),
            "path": request.path,
        }
    }
