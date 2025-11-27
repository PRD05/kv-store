from rest_framework import serializers

from storage.models import KeyValueEntry


class KeyValueSerializer(serializers.ModelSerializer):
    """Serializer for key/value entries with metadata."""

    class Meta:
        model = KeyValueEntry
        fields = [
            "key",
            "value",
            "version",
            "updated_at",
            "created_at",
        ]
        read_only_fields = ["version", "updated_at", "created_at"]


class KeyValueWriteSerializer(serializers.Serializer):
    """Serializer for writing/updating key values."""

    value = serializers.CharField(
        allow_blank=True,
        help_text="The value to store for the key. Can be empty string.",
    )


class BatchItemSerializer(serializers.Serializer):
    """Serializer for a single item in a batch operation."""

    key = serializers.CharField(
        max_length=255,
        help_text="The key for this item (max 255 characters)",
    )
    value = serializers.CharField(
        allow_blank=True,
        help_text="The value to store for this key. Can be empty string.",
    )


class BatchPutSerializer(serializers.Serializer):
    """Serializer for batch put operations."""

    items = BatchItemSerializer(
        many=True,
        help_text="List of key/value pairs to insert or update in a single transaction",
    )

    def validate_items(self, items):
        if not items:
            raise serializers.ValidationError("At least one item is required")
        keys = [item["key"] for item in items]
        if len(keys) != len(set(keys)):
            raise serializers.ValidationError("Duplicate keys detected in batch request")
        return items


class KeyValueRangeResponseSerializer(serializers.Serializer):
    """Serializer for range query responses."""

    count = serializers.IntegerField(help_text="Number of items in the range")
    results = KeyValueSerializer(many=True, help_text="List of key/value pairs in the range")
