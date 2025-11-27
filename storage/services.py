from typing import Iterable, List

from django.db import transaction

from storage.models import KeyValueEntry


def put_value(key: str, value: str) -> tuple[KeyValueEntry, bool]:
    with transaction.atomic():
        entry, created = KeyValueEntry.objects.select_for_update().get_or_create(
            key=key,
            defaults={"value": value},
        )
        if not created:
            entry.value = value
            entry.version += 1
            entry.save(update_fields=["value", "version", "updated_at"])
        return entry, created


def read_value(key: str) -> KeyValueEntry:
    return KeyValueEntry.objects.get(key=key)


def delete_value(key: str) -> bool:
    deleted, _ = KeyValueEntry.objects.filter(key=key).delete()
    return bool(deleted)


def read_range(start_key: str, end_key: str) -> List[KeyValueEntry]:
    queryset = (
        KeyValueEntry.objects.filter(key__gte=start_key, key__lte=end_key)
        .order_by("key")
    )
    return list(queryset)


def batch_put(items: Iterable[dict]) -> List[KeyValueEntry]:
    results: List[KeyValueEntry] = []
    with transaction.atomic():
        for item in items:
            entry, _ = put_value(item["key"], item["value"])
            results.append(entry)
    return results
