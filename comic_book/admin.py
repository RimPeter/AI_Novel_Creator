from django.contrib import admin

from .models import ComicBible, ComicCharacter, ComicIssue, ComicLocation, ComicPage, ComicPanel, ComicProject


@admin.register(ComicProject)
class ComicProjectAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "genre", "tone", "updated_at")
    search_fields = ("title", "slug", "owner__username", "owner__email", "logline")


@admin.register(ComicBible)
class ComicBibleAdmin(admin.ModelAdmin):
    list_display = ("project", "updated_at")
    search_fields = ("project__title", "premise", "world_rules", "visual_rules")


@admin.register(ComicCharacter)
class ComicCharacterAdmin(admin.ModelAdmin):
    list_display = ("name", "project", "role", "updated_at")
    search_fields = ("name", "role", "project__title", "description")


@admin.register(ComicLocation)
class ComicLocationAdmin(admin.ModelAdmin):
    list_display = ("name", "project", "updated_at")
    search_fields = ("name", "project__title", "description")


@admin.register(ComicIssue)
class ComicIssueAdmin(admin.ModelAdmin):
    list_display = ("project", "number", "title", "status", "planned_page_count", "updated_at")
    list_filter = ("status",)
    search_fields = ("project__title", "title", "summary", "theme")


@admin.register(ComicPage)
class ComicPageAdmin(admin.ModelAdmin):
    list_display = ("issue", "page_number", "title", "page_role", "layout_type")
    list_filter = ("page_role", "layout_type")
    search_fields = ("issue__title", "issue__project__title", "title", "summary")


@admin.register(ComicPanel)
class ComicPanelAdmin(admin.ModelAdmin):
    list_display = ("page", "panel_number", "title", "shot_type", "location")
    list_filter = ("shot_type",)
    search_fields = ("page__issue__title", "page__issue__project__title", "title", "focus", "action", "dialogue")
