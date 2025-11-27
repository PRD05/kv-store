from django.db import models


class KeyValueEntry(models.Model):
    """Represents a persisted key/value pair."""

    key = models.CharField(max_length=255, unique=True)
    value = models.TextField()
    version = models.PositiveIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]

    def __str__(self) -> str:
        return f"{self.key} (v{self.version})"
