from .text_models import get_user_text_model


def navbar_text_model(request):
    if not getattr(request.user, "is_authenticated", False):
        return {}
    return {"navbar_text_model": get_user_text_model(request.user)}


def optional_apps(request):
    from django.conf import settings

    return {
        "comic_book_app_enabled": "comic_book" in getattr(settings, "INSTALLED_APPS", []),
        "youtube_app_enabled": bool(getattr(settings, "YOUTUBE_APP_ENABLED", False)),
    }
