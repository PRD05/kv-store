# PostgreSQL Configuration for Crash Friendliness

## Overview

For optimal crash friendliness and durability, PostgreSQL server-level settings need to be configured. These cannot be set via Django's connection options.

## Required PostgreSQL Configuration

Edit `postgresql.conf` (typically located at `/etc/postgresql/<version>/main/postgresql.conf` or `/usr/local/pgsql/data/postgresql.conf`):

### WAL and Durability Settings

```conf
# Ensure writes are durable (default, but explicit)
synchronous_commit = on

# Enable WAL for replication and durability
wal_level = replica  # or 'logical' for logical replication

# Checkpoint configuration for crash recovery
checkpoint_timeout = 300  # Checkpoint every 5 minutes (default: 5min)
max_wal_size = 1GB  # Allow WAL to grow for better write performance
min_wal_size = 80MB  # Minimum WAL size

# Connection limits for predictable behavior
max_connections = 100  # Adjust based on available resources
```

### Performance Tuning

```conf
# Shared buffers (typically 25% of RAM)
shared_buffers = 2GB

# Effective cache size (typically 50-75% of RAM)
effective_cache_size = 6GB

# Maintenance work memory
maintenance_work_mem = 512MB

# Work memory for queries
work_mem = 16MB
```

## Verification

After updating `postgresql.conf`, restart PostgreSQL:

```bash
# Ubuntu/Debian
sudo systemctl restart postgresql

# macOS (Homebrew)
brew services restart postgresql

# Or manually
pg_ctl restart -D /path/to/data
```

Verify settings:

```sql
-- Check WAL level
SHOW wal_level;

-- Check synchronous commit
SHOW synchronous_commit;

-- Check checkpoint settings
SHOW checkpoint_timeout;
SHOW max_wal_size;
SHOW min_wal_size;
```

## Docker Configuration

If using Docker, set these in your `docker-compose.yml` or Docker command:

```yaml
services:
  postgres:
    image: postgres:15
    environment:
      POSTGRES_DB: kvstore
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    command: >
      postgres
      -c synchronous_commit=on
      -c wal_level=replica
      -c checkpoint_timeout=300
      -c max_wal_size=1GB
      -c min_wal_size=80MB
      -c max_connections=100
    volumes:
      - postgres_data:/var/lib/postgresql/data
```

## Backup and Recovery

### Automated Backups

Set up automated backups using `pg_dump`:

```bash
# Daily backup script
#!/bin/bash
DATE=$(date +%Y%m%d)
pg_dump -U postgres kvstore > /backups/kvstore_$DATE.sql
```

### Point-in-Time Recovery (PITR)

For point-in-time recovery, enable WAL archiving:

```conf
# In postgresql.conf
wal_level = replica
archive_mode = on
archive_command = 'cp %p /backups/wal_archive/%f'
```

## Monitoring

Monitor WAL size and checkpoint activity:

```sql
-- Check WAL size
SELECT pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), '0/0'));

-- Check checkpoint activity
SELECT * FROM pg_stat_bgwriter;
```

## References

- [PostgreSQL WAL Documentation](https://www.postgresql.org/docs/current/wal.html)
- [PostgreSQL Durability Settings](https://www.postgresql.org/docs/current/runtime-config-wal.html)
- [PostgreSQL Backup and Recovery](https://www.postgresql.org/docs/current/backup.html)

