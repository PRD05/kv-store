# Moniepoint Persistent Key/Value Store

A take-home implementation of a crash-friendly, network-available persistent key/value store built with Python 3.12, Django 5, and Django REST Framework.

## Requirements Coverage
- **Put(Key, Value)** – `PUT /kv-store/v1/kv/<key>/`
- **Read(Key)** – `GET /kv-store/v1/kv/<key>/`
- **ReadKeyRange(StartKey, EndKey)** – `GET /kv-store/v1/kv/?start=<start>&end=<end>`
- **BatchPut(..keys, ..values)** – `POST /kv-store/v1/kv/batch/`
- **Delete(Key)** – `DELETE /kv-store/v1/kv/<key>/`

## Architecture Notes
- Built with Django's ORM on PostgreSQL for durable storage and ordered range scans.
- Each `KeyValueEntry` tracks metadata (version, timestamps) to aid auditing and reconciliation.
- All mutating operations use `transaction.atomic()` to guarantee crash-friendly updates and predictable behavior during concurrent writes.
- Batch operations reuse the single-key primitive to keep latency low while achieving high throughput.
- REST responses are JSON-only to minimize serialization overhead.

### References Consulted
Key design choices (ordered storage, log-structured writes, durability, replication trade-offs) were inspired by:
- [Google Bigtable](https://static.googleusercontent.com/media/research.google.com/en//archive/bigtable-osdi06.pdf)
- [Riak Bitcask](https://riak.com/assets/bitcask-intro.pdf)
- [Log-Structured Merge Trees](https://www.cs.umb.edu/~poneil/lstree.pdf)
- [Raft: In Search of an Understandable Consensus Algorithm](https://web.stanford.edu/~ouster/cgi-bin/papers/raft-atc14.pdf)
- [Lamport Paxos Papers](https://lamport.azurewebsites.net/pubs/lamport-paxos.pdf)

While this submission runs as a single node, the codebase is structured to extend toward multi-node replication/failover strategies discussed in the references.

## Getting Started
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
createdb kvstore    # or your preferred database name
export POSTGRES_DB=kvstore
export POSTGRES_USER=postgres
export POSTGRES_PASSWORD=postgres
export POSTGRES_HOST=localhost
export POSTGRES_PORT=5432
python manage.py migrate
python manage.py runserver
```

### API Documentation (Swagger/OpenAPI)

Interactive API documentation is available via Swagger UI:

- **Swagger UI**: http://localhost:8000/kv-store/docs/
- **ReDoc**: http://localhost:8000/kv-store/redoc/
- **OpenAPI Schema (JSON)**: http://localhost:8000/kv-store/schema/

The documentation is serializer-based and automatically generated from your Django REST Framework serializers and views.

### API Examples
```bash
# Upsert
curl -X PUT localhost:8000/kv-store/v1/kv/user123/ -H 'Content-Type: application/json' -d '{"value": "42"}'

# Range scan
curl 'localhost:8000/kv-store/v1/kv/?start=user100&end=user200'

# Batch put
cat <<'JSON' | curl -X POST localhost:8000/kv-store/v1/kv/batch/ -H 'Content-Type: application/json' -d @-
{"items": [{"key": "foo", "value": "bar"}, {"key": "baz", "value": "qux"}]}
JSON
```

## Testing
```bash
python manage.py test
```

## Extensibility
- Add replication/failover by integrating Raft/Paxos coordinators or leveraging Django Channels for streaming change feeds.
- Introduce TTL indices or compression per requirements.
