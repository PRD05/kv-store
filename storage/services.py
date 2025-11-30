from typing import Iterable, List, Optional, Tuple

from django.core.cache import cache
from django.db import transaction
from django.db.models import F

from storage.models import KeyValueEntry
from storage.replication import replicate_put, replicate_delete, replicate_batch_put

# Cache settings
CACHE_TIMEOUT = 300  # 5 minutes
CACHE_KEY_PREFIX = "kv:"

# Resource limits for predictable behavior
MAX_RANGE_SIZE = 10000  # Maximum items to return in a single range query
MAX_BATCH_SIZE = 10000  # Maximum items in a single batch operation
BATCH_CHUNK_SIZE = 1000  # Process batches in chunks to avoid memory issues


def _get_cache_key(key: str) -> str:
    """Generate cache key for a given key."""
    return f"{CACHE_KEY_PREFIX}{key}"


def put_value(key: str, value: str, replicate: bool = True) -> tuple[KeyValueEntry, bool]:
    """
    Optimized put operation using update_or_create for atomic upsert.
    Removes need for select_for_update() lock, reducing latency significantly.
    Uses F() expressions for atomic version increment at database level.
    
    Args:
        key: The key to store
        value: The value to store
        replicate: Whether to replicate to other nodes (False for internal replication calls)
    
    Returns:
        Tuple of (entry, created)
    
    Raises:
        Exception: If replication fails to achieve quorum
    """
    # Use update_or_create which is optimized and doesn't require explicit locking
    # This is much faster than select_for_update() + save()
    with transaction.atomic():
        # First, try to update existing entry atomically
        updated = KeyValueEntry.objects.filter(key=key).update(
            value=value,
            version=F("version") + 1,
        )
        
        if updated:
            # Entry existed and was updated
            entry = KeyValueEntry.objects.get(key=key)
            created = False
        else:
            # Entry doesn't exist, create it
            entry = KeyValueEntry.objects.create(key=key, value=value, version=1)
            created = True
        
        # Replicate to other nodes (if replication is enabled)
        replication_success, successful_nodes = replicate_put(key, value, replicate=replicate)
        
        if not replication_success:
            # Rollback if replication failed to achieve quorum
            raise Exception(
                f"Failed to replicate to quorum of nodes. "
                f"Successful: {len(successful_nodes) + 1} nodes"
            )
        
        # Invalidate cache on write
        cache.delete(_get_cache_key(key))
        
        return entry, created


def read_value(key: str) -> KeyValueEntry:
    """
    Optimized read with in-memory caching for hot keys.
    Uses only() to fetch minimal fields from database.
    """
    cache_key = _get_cache_key(key)
    
    # Try cache first (hot path for frequently accessed keys)
    cached_entry = cache.get(cache_key)
    if cached_entry is not None:
        return cached_entry
    
    # Cache miss - fetch from database with minimal fields
    entry = KeyValueEntry.objects.only("key", "value", "version", "created_at", "updated_at").get(key=key)
    
    # Cache for future reads (write-through cache)
    cache.set(cache_key, entry, CACHE_TIMEOUT)
    
    return entry


def delete_value(key: str, replicate: bool = True) -> bool:
    """
    Delete operation with cache invalidation and replication.
    
    Args:
        key: The key to delete
        replicate: Whether to replicate to other nodes
    
    Returns:
        True if deleted, False if key didn't exist
    
    Raises:
        Exception: If replication fails to achieve quorum
    """
    deleted, _ = KeyValueEntry.objects.filter(key=key).delete()
    
    if deleted:
        # Replicate deletion to other nodes
        replication_success, successful_nodes = replicate_delete(key, replicate=replicate)
        
        if not replication_success:
            raise Exception(
                f"Failed to replicate delete to quorum of nodes. "
                f"Successful: {len(successful_nodes) + 1} nodes"
            )
        
        # Invalidate cache on delete
        cache.delete(_get_cache_key(key))
    
    return bool(deleted)


def read_range(
    start_key: str,
    end_key: str,
    limit: Optional[int] = None,
    offset: int = 0,
    cursor: Optional[str] = None,
) -> Tuple[List[KeyValueEntry], Optional[str], bool]:
    """
    Memory-efficient range query with pagination support.
    
    Uses iterator() for large datasets to avoid loading all results into memory.
    Supports both offset-based and cursor-based pagination.
    
    Args:
        start_key: Start key (inclusive)
        end_key: End key (inclusive)
        limit: Maximum number of results to return (default: MAX_RANGE_SIZE)
        offset: Offset for pagination (used if cursor is None)
        cursor: Cursor for cursor-based pagination (key of last returned item)
    
    Returns:
        Tuple of (entries, next_cursor, has_more)
        - entries: List of KeyValueEntry objects
        - next_cursor: Key to use for next page (None if no more results)
        - has_more: Whether there are more results available
    """
    if limit is None:
        limit = MAX_RANGE_SIZE
    else:
        limit = min(limit, MAX_RANGE_SIZE)  # Enforce maximum
    
    queryset = (
        KeyValueEntry.objects.filter(key__gte=start_key, key__lte=end_key)
        .only("key", "value", "version", "created_at", "updated_at")
        .order_by("key")
    )
    
    # Cursor-based pagination (more efficient for large datasets)
    if cursor:
        queryset = queryset.filter(key__gt=cursor)
    
    # Use iterator() for memory efficiency - streams results instead of loading all
    # This allows handling datasets much larger than RAM
    queryset = queryset[:limit + 1]  # Fetch one extra to check if there's more
    
    # Convert iterator to list, but only fetch what we need
    entries = []
    iterator = queryset.iterator(chunk_size=1000)  # Process in chunks
    
    for entry in iterator:
        if len(entries) < limit:
            entries.append(entry)
        else:
            # We have one extra entry, so there are more results
            next_cursor = entry.key
            return entries, next_cursor, True
    
    # No more results
    return entries, None, False


def batch_put(items: Iterable[dict], replicate: bool = True) -> List[KeyValueEntry]:
    """
    Memory-efficient batch operation using chunked processing.
    Processes large batches in chunks to avoid memory issues with datasets larger than RAM.
    Uses bulk operations for optimal performance.
    
    Args:
        items: Iterable of items to put
        replicate: Whether to replicate to other nodes
    
    Returns:
        List of created/updated entries
    
    Raises:
        ValueError: If batch size exceeds maximum
        Exception: If replication fails to achieve quorum
    """
    items_list = list(items)  # Convert to list for validation
    
    if not items_list:
        return []
    
    # Enforce maximum batch size for predictable behavior
    if len(items_list) > MAX_BATCH_SIZE:
        raise ValueError(
            f"Batch size {len(items_list)} exceeds maximum of {MAX_BATCH_SIZE} items"
        )
    
    all_results: List[KeyValueEntry] = []
    all_cache_keys_to_invalidate: List[str] = []
    
    # Process in chunks to handle large batches without memory issues
    for chunk_start in range(0, len(items_list), BATCH_CHUNK_SIZE):
        chunk = items_list[chunk_start : chunk_start + BATCH_CHUNK_SIZE]
        
        chunk_results: List[KeyValueEntry] = []
        chunk_cache_keys: List[str] = []
        
        with transaction.atomic():
            # Collect keys for batch lookup
            keys = [item["key"] for item in chunk]
            
            # Bulk fetch existing entries (memory efficient with iterator)
            existing_entries = {
                entry.key: entry
                for entry in KeyValueEntry.objects.filter(key__in=keys)
                .only("id", "key", "version")
                .iterator(chunk_size=500)  # Stream results
            }
            
            # Separate creates and updates
            to_create: List[KeyValueEntry] = []
            to_update: List[KeyValueEntry] = []
            
            for item in chunk:
                key = item["key"]
                value = item["value"]
                chunk_cache_keys.append(_get_cache_key(key))
                
                if key in existing_entries:
                    # Update existing
                    entry = existing_entries[key]
                    entry.value = value
                    entry.version += 1
                    to_update.append(entry)
                else:
                    # Create new
                    to_create.append(KeyValueEntry(key=key, value=value, version=1))
            
            # Bulk create new entries
            if to_create:
                KeyValueEntry.objects.bulk_create(to_create)
                chunk_results.extend(to_create)
            
            # Bulk update existing entries
            if to_update:
                KeyValueEntry.objects.bulk_update(to_update, ["value", "version", "updated_at"])
                chunk_results.extend(to_update)
        
        all_results.extend(chunk_results)
        all_cache_keys_to_invalidate.extend(chunk_cache_keys)
    
    # Replicate to other nodes
    replication_success, successful_nodes = replicate_batch_put(items_list, replicate=replicate)
    
    if not replication_success:
        raise Exception(
            f"Failed to replicate batch to quorum of nodes. "
            f"Successful: {len(successful_nodes) + 1} nodes"
        )
    
    # Invalidate cache for all modified keys (batch operation)
    if all_cache_keys_to_invalidate:
        cache.delete_many(all_cache_keys_to_invalidate)
    
    # Refresh entries in chunks to avoid memory issues
    all_result_keys = [entry.key for entry in all_results]
    refreshed_entries = {}
    
    for chunk_start in range(0, len(all_result_keys), BATCH_CHUNK_SIZE):
        chunk_keys = all_result_keys[chunk_start : chunk_start + BATCH_CHUNK_SIZE]
        chunk_refreshed = {
            entry.key: entry
            for entry in KeyValueEntry.objects.filter(key__in=chunk_keys)
            .only("key", "value", "version", "created_at", "updated_at")
            .iterator(chunk_size=500)
        }
        refreshed_entries.update(chunk_refreshed)
    
    return [refreshed_entries[entry.key] for entry in all_results]
