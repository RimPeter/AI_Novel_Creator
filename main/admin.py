from django.contrib import admin

from .models import HomeUpdate


@admin.register(HomeUpdate)
class HomeUpdateAdmin(admin.ModelAdmin):
    list_display = ("date", "title", "updated_at")
    search_fields = ("title", "body")
    ordering = ("-date", "-updated_at")
