from django.contrib import admin

from storage.models import KeyValueEntry


@admin.register(KeyValueEntry)
class KeyValueEntryAdmin(admin.ModelAdmin):
    list_display = ("key", "version", "updated_at")
    search_fields = ("key",)
    ordering = ("key",)
