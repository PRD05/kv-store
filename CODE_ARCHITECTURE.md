# Code Architecture and Flow Documentation

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Project Structure](#project-structure)
3. [Request Flow](#request-flow)
4. [Core Components](#core-components)
5. [Data Flow Diagrams](#data-flow-diagrams)
6. [Key Design Patterns](#key-design-patterns)
7. [Performance Optimizations](#performance-optimizations)

---

## Architecture Overview

The key-value store is built as a **3-tier architecture**:

```
┌─────────────────┐
│   Client/API    │  ← REST API Layer (Django REST Framework)
├─────────────────┤
│  Business Logic │  ← Service Layer (storage/services.py)
├─────────────────┤
│   Data Layer    │  ← Database (PostgreSQL) + Cache (Redis)
└─────────────────┘
```

### Technology Stack

- **Framework**: Django 5.2.8 with Django REST Framework
- **Database**: PostgreSQL (persistent storage)
- **Cache**: Redis (distributed caching)
- **API**: RESTful JSON API with OpenAPI/Swagger documentation

---

## Project Structure

```
kv-store/
├── kvstore/                    # Django project settings
│   ├── settings.py            # Configuration (DB, cache, replication)
│   ├── urls.py                # Root URL routing
│   └── wsgi.py                # WSGI application entry point
│
├── storage/                    # Main application
│   ├── models.py              # Database models (KeyValueEntry)
│   ├── views.py                # API endpoints (REST views)
│   ├── services.py             # Business logic layer
│   ├── serializers.py         # Request/response serialization
│   ├── urls.py                # Application URL routing
│   ├── admin.py               # Django admin configuration
│   └── migrations/            # Database migrations
│
├── examples/                   # Configuration examples
│   ├── nginx-failover.conf    # nginx load balancer config
│   └── haproxy-failover.cfg   # HAProxy load balancer config
│
├── requirements.txt            # Python dependencies
├── manage.py                   # Django management script
└── README.md                   # User-facing documentation
```

---

## Request Flow

### 1. PUT Request Flow (Create/Update Key)

```
Client Request
    ↓
[1] HTTP PUT /kv-store/v1/kv/<key>/
    ↓
[2] Django URL Router (storage/urls.py)
    → Routes to KeyValueView.put()
    ↓
[3] KeyValueView.put() (storage/views.py)
    → Validates request with KeyValueWriteSerializer
    → Extracts 'value' from request body
    → Checks X-Replication header (prevents infinite loops)
    ↓
[4] put_value(key, value) (storage/services.py)
    → Starts database transaction (atomic)
    → Attempts atomic update with F() expression
    → If no existing entry, creates new one
    → Invalidates cache entry
    → Returns (entry, created) tuple
    ↓
[5] KeyValueView.put() (storage/views.py)
    → Serializes entry with KeyValueSerializer
    → Returns HTTP 201 (created) or 200 (updated)
    ↓
[6] Client receives response
```

**Key Points:**
- **Atomicity**: All operations wrapped in `transaction.atomic()`
- **Optimistic locking**: Uses `F()` expressions for version increment
- **Cache invalidation**: Deletes cache entry on write
- **No pessimistic locks**: Removed `select_for_update()` for better concurrency

### 2. GET Request Flow (Read Key)

```
Client Request
    ↓
[1] HTTP GET /kv-store/v1/kv/<key>/
    ↓
[2] Django URL Router
    → Routes to KeyValueView.get()
    ↓
[3] KeyValueView.get() (storage/views.py)
    → Calls read_value(key)
    ↓
[4] read_value(key) (storage/services.py)
    → Checks Redis cache first (hot path)
    → If cache hit: returns immediately (~0.1ms)
    → If cache miss:
        → Queries database with .only() optimization
        → Stores result in cache (TTL: 5 minutes)
        → Returns entry
    ↓
[5] KeyValueView.get()
    → Serializes entry
    → Returns HTTP 200 with JSON response
    ↓
[6] Client receives response
```

**Key Points:**
- **Cache-first**: Redis cache checked before database
- **Query optimization**: Uses `.only()` to fetch minimal fields
- **Write-through cache**: Cache updated on database read

### 3. Range Query Flow (ReadKeyRange)

```
Client Request
    ↓
[1] HTTP GET /kv-store/v1/kv/?start=<start>&end=<end>&limit=<limit>&cursor=<cursor>
    ↓
[2] KeyValueRangeView.get() (storage/views.py)
    → Validates start/end parameters
    → Parses pagination (limit, cursor)
    ↓
[3] read_range(start, end, limit, cursor) (storage/services.py)
    → Builds queryset with filters
    → Uses .only() for minimal field selection
    → Applies cursor-based pagination (if provided)
    → Uses .iterator(chunk_size=1000) for memory efficiency
    → Fetches limit+1 items to check for more results
    → Returns (entries, next_cursor, has_more)
    ↓
[4] KeyValueRangeView.get()
    → Serializes entries
    → Returns paginated response with metadata
    ↓
[5] Client receives response
```

**Key Points:**
- **Memory efficient**: Uses iterator() to stream results
- **Pagination**: Cursor-based for large datasets
- **Chunked processing**: Processes 1000 items at a time

### 4. Batch Put Flow

```
Client Request
    ↓
[1] HTTP POST /kv-store/v1/kv/batch/
    → Body: {"items": [{"key": "k1", "value": "v1"}, ...]}
    ↓
[2] BatchPutView.post() (storage/views.py)
    → Validates with BatchPutSerializer
    → Checks for duplicates, empty items, size limits
    ↓
[3] batch_put(items) (storage/services.py)
    → Validates batch size (max 10,000 items)
    → Processes in chunks (1,000 items per chunk)
    → For each chunk:
        → Bulk fetches existing entries
        → Separates creates vs updates
        → Uses bulk_create() for new entries
        → Uses bulk_update() for existing entries
        → Invalidates cache for all modified keys
    → Returns all created/updated entries
    ↓
[4] BatchPutView.post()
    → Serializes all entries
    → Returns HTTP 200 with array of results
    ↓
[5] Client receives response
```

**Key Points:**
- **Bulk operations**: Uses bulk_create/bulk_update (10-100x faster)
- **Chunked processing**: Prevents memory issues
- **Single transaction**: All or nothing atomicity

### 5. DELETE Request Flow

```
Client Request
    ↓
[1] HTTP DELETE /kv-store/v1/kv/<key>/
    ↓
[2] KeyValueView.delete() (storage/views.py)
    → Calls delete_value(key)
    ↓
[3] delete_value(key) (storage/services.py)
    → Deletes from database
    → Invalidates cache entry
    → Returns True if deleted, False if not found
    ↓
[4] KeyValueView.delete()
    → Returns HTTP 204 (No Content) or 404 (Not Found)
    ↓
[5] Client receives response
```

---

## Core Components

### 1. Models (`storage/models.py`)

**KeyValueEntry Model:**
```python
class KeyValueEntry(models.Model):
    key = models.CharField(max_length=255, unique=True)  # Indexed for fast lookups
    value = models.TextField()                           # Can store large values
    version = models.PositiveIntegerField(default=1)     # Optimistic locking
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
```

**Key Features:**
- **Unique key constraint**: Ensures no duplicates, creates index
- **Version tracking**: For optimistic concurrency control
- **Timestamps**: For auditing and debugging

### 2. Services (`storage/services.py`)

**Core Functions:**

#### `put_value(key, value, replicate=True)`
- **Purpose**: Atomic upsert operation
- **Flow**:
  1. Start transaction
  2. Try atomic update with `F()` expression
  3. If no existing entry, create new one
  4. Invalidate cache
  5. Return (entry, created)

#### `read_value(key)`
- **Purpose**: Read with caching
- **Flow**:
  1. Check Redis cache
  2. If miss, query database with `.only()`
  3. Cache result
  4. Return entry

#### `read_range(start, end, limit, cursor)`
- **Purpose**: Memory-efficient range queries
- **Flow**:
  1. Build filtered queryset
  2. Apply cursor pagination
  3. Use iterator() for streaming
  4. Return paginated results

#### `batch_put(items, replicate=True)`
- **Purpose**: Bulk upsert operations
- **Flow**:
  1. Validate batch size
  2. Process in chunks
  3. Bulk fetch existing entries
  4. Bulk create/update
  5. Invalidate cache

#### `delete_value(key, replicate=True)`
- **Purpose**: Delete with cache invalidation
- **Flow**:
  1. Delete from database
  2. Invalidate cache
  3. Return success status

### 3. Views (`storage/views.py`)

**APIView Classes:**

#### `KeyValueView`
- **Endpoints**: `/kv/<key>/`
- **Methods**: GET, PUT, DELETE
- **Responsibilities**:
  - Request validation
  - Error handling
  - Response serialization
  - HTTP status codes

#### `KeyValueRangeView`
- **Endpoints**: `/kv/?start=&end=&limit=&cursor=`
- **Methods**: GET
- **Responsibilities**:
  - Range validation
  - Pagination handling
  - Response formatting

#### `BatchPutView`
- **Endpoints**: `/kv/batch/`
- **Methods**: POST
- **Responsibilities**:
  - Batch validation
  - Error handling
  - Bulk operation coordination

#### `HealthCheckView`
- **Endpoints**: `/health/`
- **Methods**: GET
- **Responsibilities**:
  - Cluster status reporting
  - Health monitoring

### 4. Serializers (`storage/serializers.py`)

**Purpose**: Request/response validation and transformation

#### `KeyValueSerializer`
- Serializes KeyValueEntry model
- Fields: key, value, version, created_at, updated_at

#### `KeyValueWriteSerializer`
- Validates write requests
- Fields: value (required)

#### `BatchPutSerializer`
- Validates batch requests
- Checks for duplicates
- Enforces size limits

#### `KeyValueRangeResponseSerializer`
- Formats range query responses
- Includes pagination metadata

### 5. Settings (`kvstore/settings.py`)

**Key Configurations:**

#### Database
```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'CONN_MAX_AGE': 600,  # Connection pooling
        'OPTIONS': {
            'options': '-c statement_timeout=30000',  # Query timeout
        },
    }
}
```

#### Cache
```python
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': 'redis://localhost:6379/0',
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
            'IGNORE_EXCEPTIONS': True,
        },
        'TIMEOUT': 300,  # 5 minutes
    }
}
```

#### Replication
```python
REPLICA_NODES = [
    {'url': 'http://node2:8000/kv-store/v1'},
    {'url': 'http://node3:8000/kv-store/v1'},
]
```

---

## Data Flow Diagrams

### Write Operation (PUT)

```
┌─────────┐
│ Client  │
└────┬────┘
     │ PUT /kv/<key>/
     ↓
┌─────────────────┐
│  Django Views   │
│  (validation)   │
└────┬────────────┘
     │
     ↓
┌─────────────────┐
│  Services Layer │
│  (put_value)    │
└────┬────────────┘
     │
     ├──→ [Transaction Start]
     │
     ├──→ [Atomic Update/Create]
     │         │
     │         ↓
     │    ┌─────────────┐
     │    │ PostgreSQL │
     │    │  (WAL)     │
     │    └─────────────┘
     │
     ├──→ [Cache Invalidate]
     │         │
     │         ↓
     │    ┌─────────────┐
     │    │   Redis     │
     │    │  (delete)   │
     │    └─────────────┘
     │
     └──→ [Transaction Commit]
              │
              ↓
         ┌─────────┐
         │ Client  │
         └─────────┘
```

### Read Operation (GET)

```
┌─────────┐
│ Client  │
└────┬────┘
     │ GET /kv/<key>/
     ↓
┌─────────────────┐
│  Django Views   │
└────┬────────────┘
     │
     ↓
┌─────────────────┐
│  Services Layer │
│  (read_value)   │
└────┬────────────┘
     │
     ├──→ [Check Cache]
     │         │
     │         ├──→ [Cache Hit] ──→ Return (~0.1ms)
     │         │
     │         └──→ [Cache Miss]
     │                   │
     │                   ↓
     │              ┌─────────────┐
     │              │ PostgreSQL │
     │              │  (query)   │
     │              └──────┬──────┘
     │                     │
     │                     ↓
     │              [Store in Cache]
     │                     │
     │                     ↓
     │              ┌─────────────┐
     │              │   Redis    │
     │              │  (set)     │
     │              └─────────────┘
     │
     └──→ [Return Entry]
              │
              ↓
         ┌─────────┐
         │ Client  │
         └─────────┘
```

### Batch Operation Flow

```
┌─────────┐
│ Client  │
└────┬────┘
     │ POST /kv/batch/
     ↓
┌─────────────────┐
│  BatchPutView   │
│  (validation)   │
└────┬────────────┘
     │
     ↓
┌─────────────────┐
│  batch_put()    │
└────┬────────────┘
     │
     ├──→ [Validate Size]
     │
     ├──→ [Split into Chunks]
     │         │
     │         ├──→ Chunk 1 (1000 items)
     │         ├──→ Chunk 2 (1000 items)
     │         └──→ Chunk N
     │
     ├──→ [For Each Chunk]
     │         │
     │         ├──→ [Bulk Fetch Existing]
     │         │         │
     │         │         ↓
     │         │    ┌─────────────┐
     │         │    │ PostgreSQL │
     │         │    └─────────────┘
     │         │
     │         ├──→ [Separate Creates/Updates]
     │         │
     │         ├──→ [Bulk Create]
     │         │         │
     │         │         ↓
     │         │    ┌─────────────┐
     │         │    │ PostgreSQL │
     │         │    └─────────────┘
     │         │
     │         ├──→ [Bulk Update]
     │         │         │
     │         │         ↓
     │         │    ┌─────────────┐
     │         │    │ PostgreSQL │
     │         │    └─────────────┘
     │         │
     │         └──→ [Invalidate Cache]
     │                   │
     │                   ↓
     │              ┌─────────────┐
     │              │   Redis     │
     │              │ (delete_many)│
     │              └─────────────┘
     │
     └──→ [Return All Entries]
              │
              ↓
         ┌─────────┐
         │ Client  │
         └─────────┘
```

---

## Key Design Patterns

### 1. Service Layer Pattern

**Purpose**: Separate business logic from API layer

**Benefits**:
- Reusable logic (can be called from views, management commands, etc.)
- Easier testing
- Clear separation of concerns

**Example**:
```python
# views.py
def put(self, request, key: str):
    entry, created = put_value(key, value)  # Service layer
    return Response(serializer.data)

# services.py
def put_value(key: str, value: str):
    # Business logic here
    with transaction.atomic():
        # ...
```

### 2. Repository Pattern (via Django ORM)

**Purpose**: Abstract database access

**Implementation**: Django ORM acts as repository
- `KeyValueEntry.objects.get()` - Find by key
- `KeyValueEntry.objects.filter()` - Query with conditions
- `KeyValueEntry.objects.create()` - Create new
- `KeyValueEntry.objects.update()` - Update existing

### 3. Optimistic Locking

**Purpose**: Avoid pessimistic locks (better concurrency)

**Implementation**:
```python
# Instead of select_for_update() (pessimistic)
KeyValueEntry.objects.filter(key=key).update(
    value=value,
    version=F("version") + 1,  # Atomic at DB level
)
```

**Benefits**:
- No lock contention
- Better throughput
- Atomic operations at database level

### 4. Caching Strategy (Write-Through)

**Purpose**: Fast reads for hot keys

**Implementation**:
- **Read**: Check cache first, fallback to DB, then cache
- **Write**: Invalidate cache immediately
- **Delete**: Invalidate cache immediately

**Cache Key Format**: `kv:<key>`

### 5. Iterator Pattern (for Large Datasets)

**Purpose**: Memory-efficient processing

**Implementation**:
```python
queryset = KeyValueEntry.objects.filter(...)
for entry in queryset.iterator(chunk_size=1000):
    # Process entry
    # Memory usage: ~1KB per entry, not entire queryset
```

**Benefits**:
- Handles datasets larger than RAM
- Constant memory usage
- Streaming results

### 6. Bulk Operations Pattern

**Purpose**: High throughput for batch operations

**Implementation**:
```python
# Instead of: for item in items: put_value(item)
# Use:
KeyValueEntry.objects.bulk_create(to_create)
KeyValueEntry.objects.bulk_update(to_update, fields)
```

**Benefits**:
- 10-100x faster than sequential operations
- Single database round-trip
- Atomic transaction

---

## Performance Optimizations

### 1. Query Optimization

**`.only()` Usage**:
```python
# Fetch only needed fields
entry = KeyValueEntry.objects.only("key", "value", "version").get(key=key)
```

**Benefits**:
- Reduces data transfer
- Faster query execution
- Lower memory usage

### 2. Connection Pooling

**Configuration**:
```python
'CONN_MAX_AGE': 600  # Reuse connections for 10 minutes
```

**Benefits**:
- Eliminates connection overhead
- Better resource utilization
- Reduced latency

### 3. Indexing

**Automatic Index**:
```python
key = models.CharField(max_length=255, unique=True)
# Creates index automatically for O(log n) lookups
```

### 4. Chunked Processing

**Implementation**:
```python
for chunk_start in range(0, len(items), BATCH_CHUNK_SIZE):
    chunk = items[chunk_start:chunk_start + BATCH_CHUNK_SIZE]
    # Process chunk
```

**Benefits**:
- Bounded memory usage
- Prevents OOM errors
- Predictable performance

### 5. Resource Limits

**Enforcement**:
```python
MAX_RANGE_SIZE = 10000  # Maximum items per range query
MAX_BATCH_SIZE = 10000  # Maximum items per batch
```

**Benefits**:
- Prevents unbounded operations
- Predictable resource usage
- Clear error messages

---

## Error Handling

### Validation Errors

**Location**: Serializers (`storage/serializers.py`)

**Examples**:
- Empty batch items
- Duplicate keys in batch
- Batch size exceeds limit

**Response**: HTTP 400 with error details

### Database Errors

**Location**: Services (`storage/services.py`)

**Examples**:
- Key not found (DoesNotExist)
- Database connection errors
- Transaction failures

**Response**: HTTP 404 or 500 with error message

### Cache Errors

**Handling**: `IGNORE_EXCEPTIONS: True`

**Behavior**: Cache failures don't break the application
- Falls back to database
- Logs warning
- Continues operation

---

## Testing Flow

### Unit Tests

**Location**: `storage/tests.py`

**Coverage**:
- API endpoints
- Serializer validation
- Error cases

**Run**:
```bash
python manage.py test storage.tests
```

### Integration Testing

**Manual Testing**:
```bash
# Test PUT
curl -X PUT http://localhost:8000/kv-store/v1/kv/test/ \
  -H 'Content-Type: application/json' \
  -d '{"value": "test"}'

# Test GET
curl http://localhost:8000/kv-store/v1/kv/test/

# Test Range
curl 'http://localhost:8000/kv-store/v1/kv/?start=a&end=z&limit=10'

# Test Batch
curl -X POST http://localhost:8000/kv-store/v1/kv/batch/ \
  -H 'Content-Type: application/json' \
  -d '{"items": [{"key": "k1", "value": "v1"}]}'
```

---

## Summary

### Key Architectural Decisions

1. **Service Layer**: Separates business logic from API
2. **Optimistic Locking**: Better concurrency than pessimistic locks
3. **Caching**: Redis for distributed, fast reads
4. **Bulk Operations**: High throughput for batch writes
5. **Iterator Pattern**: Memory-efficient for large datasets
6. **Resource Limits**: Predictable behavior under load

### Performance Characteristics

| Operation | Latency | Throughput | Memory |
|-----------|---------|------------|--------|
| Read (cached) | ~0.1ms | ~10K ops/sec | ~1KB |
| Read (uncached) | ~1-5ms | ~200-1K ops/sec | ~1KB |
| Write | ~2-8ms | ~125-500 ops/sec | ~2KB |
| Batch (100 items) | ~50-200ms | ~500-2K items/sec | ~5MB |
| Range (paginated) | ~50-200ms/page | Unlimited | ~10MB/page |

### Scalability Features

- ✅ Handles datasets larger than RAM (pagination)
- ✅ High throughput (bulk operations)
- ✅ Low latency (caching)
- ✅ Fault tolerance (transactions, WAL)
- ✅ Predictable behavior (resource limits)

---

This architecture provides a production-ready, scalable key-value store that meets all performance and reliability requirements.

