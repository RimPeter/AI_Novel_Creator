from .text_models import get_user_text_model


def navbar_text_model(request):
    if not getattr(request.user, "is_authenticated", False):
        return {}
    return {"navbar_text_model": get_user_text_model(request.user)}
