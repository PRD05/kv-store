"""
Replication module for multi-node data synchronization.

Implements synchronous replication to multiple nodes for high availability
with automatic failover capabilities.
Uses a simple majority consensus approach (write must succeed on N/2+1 nodes).
"""

import logging
import time
from typing import List, Dict, Tuple, Optional
import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# Cache keys for node health status
HEALTH_CACHE_PREFIX = "node_health:"
HEALTH_CACHE_TIMEOUT = 10  # seconds
RETRY_ATTEMPTS = 3
RETRY_DELAY = 0.5  # seconds


def get_replica_nodes() -> List[Dict[str, str]]:
    """
    Get list of replica nodes from settings.
    
    Returns:
        List of node configurations with 'url' key
    """
    return getattr(settings, 'REPLICA_NODES', [])


def is_replication_enabled() -> bool:
    """Check if replication is enabled."""
    return len(get_replica_nodes()) > 0


def get_required_replicas() -> int:
    """
    Get number of replicas required for successful write (majority).
    
    Returns:
        Number of replicas needed (N/2 + 1 for majority consensus)
    """
    total_nodes = len(get_replica_nodes()) + 1  # +1 for current node
    return (total_nodes // 2) + 1


def replicate_put(key: str, value: str, replicate: bool = True) -> Tuple[bool, List[str]]:
    """
    Replicate a put operation to all configured replica nodes with automatic failover.
    
    Args:
        key: The key to replicate
        value: The value to replicate
        replicate: Whether to actually perform replication (False for internal calls)
    
    Returns:
        Tuple of (success, list of successful nodes)
    """
    return replicate_with_failover('put', key, value, replicate=replicate)


def replicate_delete(key: str, replicate: bool = True) -> Tuple[bool, List[str]]:
    """
    Replicate a delete operation to all configured replica nodes with automatic failover.
    
    Args:
        key: The key to delete
        replicate: Whether to actually perform replication
    
    Returns:
        Tuple of (success, list of successful nodes)
    """
    return replicate_with_failover('delete', key, replicate=replicate)


def replicate_batch_put(items: List[Dict], replicate: bool = True) -> Tuple[bool, List[str]]:
    """
    Replicate a batch put operation to all configured replica nodes with automatic failover.
    
    Args:
        items: List of items to replicate
        replicate: Whether to actually perform replication
    
    Returns:
        Tuple of (success, list of successful nodes)
    """
    if not replicate or not is_replication_enabled():
        return True, []
    
    replica_nodes = get_replica_nodes()
    required_replicas = get_required_replicas()
    successful_nodes = []
    failed_nodes = []
    
    logger.info(f"Replicating BATCH PUT ({len(items)} items) to {len(replica_nodes)} nodes with failover")
    
    for node in replica_nodes:
        success = False
        
        # Retry logic for automatic failover
        for attempt in range(RETRY_ATTEMPTS):
            try:
                url = f"{node['url']}/batch/"
                response = requests.post(
                    url,
                    json={"items": items},
                    headers={"X-Replication": "true"},
                    timeout=30,  # Longer timeout for batch operations
                )
                
                if response.status_code == 200:
                    successful_nodes.append(node['url'])
                    success = True
                    logger.debug(f"Successfully replicated batch to {node['url']} on attempt {attempt + 1}")
                    break
                else:
                    logger.warning(
                        f"Failed to replicate batch to {node['url']} on attempt {attempt + 1}: "
                        f"{response.status_code}"
                    )
                    
            except requests.RequestException as e:
                logger.warning(
                    f"Error replicating batch to {node['url']} on attempt {attempt + 1}: {e}"
                )
                if attempt < RETRY_ATTEMPTS - 1:
                    time.sleep(RETRY_DELAY)
        
        if not success:
            failed_nodes.append(node['url'])
            # Mark node as unhealthy
            cache_key = f"{HEALTH_CACHE_PREFIX}{node['url']}"
            cache.set(cache_key, False, HEALTH_CACHE_TIMEOUT)
    
    # Check if we have majority
    total_successful = len(successful_nodes) + 1
    has_quorum = total_successful >= required_replicas
    
    if has_quorum:
        logger.info(
            f"Batch replication successful with failover: {total_successful}/"
            f"{len(replica_nodes) + 1} nodes"
        )
    else:
        logger.error(
            f"Batch replication failed even with failover: only {total_successful}/"
            f"{len(replica_nodes) + 1} nodes, needed {required_replicas}"
        )
    
    return has_quorum, successful_nodes


def check_node_health(node_url: str, use_cache: bool = True) -> bool:
    """
    Check if a replica node is healthy.
    
    Args:
        node_url: Base URL of the node to check
        use_cache: Whether to use cached health status
    
    Returns:
        True if node is healthy, False otherwise
    """
    if use_cache:
        # Check cached health status first
        cache_key = f"{HEALTH_CACHE_PREFIX}{node_url}"
        cached_health = cache.get(cache_key)
        if cached_health is not None:
            return cached_health
    
    try:
        url = f"{node_url}/health/"
        response = requests.get(url, timeout=3)
        is_healthy = response.status_code == 200
        
        # Cache the health status
        cache_key = f"{HEALTH_CACHE_PREFIX}{node_url}"
        cache.set(cache_key, is_healthy, HEALTH_CACHE_TIMEOUT)
        
        return is_healthy
    except requests.RequestException:
        # Cache the failure
        cache_key = f"{HEALTH_CACHE_PREFIX}{node_url}"
        cache.set(cache_key, False, HEALTH_CACHE_TIMEOUT)
        return False


def get_healthy_nodes() -> List[Dict[str, str]]:
    """
    Get list of currently healthy replica nodes.
    
    Returns:
        List of healthy node configurations
    """
    replica_nodes = get_replica_nodes()
    healthy = []
    
    for node in replica_nodes:
        if check_node_health(node['url']):
            healthy.append(node)
    
    return healthy


def replicate_with_failover(
    operation: str,
    key: str,
    value: Optional[str] = None,
    timeout: int = 5,
    replicate: bool = True
) -> Tuple[bool, List[str]]:
    """
    Replicate operation with automatic failover and retry.
    
    Args:
        operation: Operation type ('put' or 'delete')
        key: The key to replicate
        value: The value (for put operations)
        timeout: Request timeout in seconds
        replicate: Whether to actually perform replication
    
    Returns:
        Tuple of (success, list of successful nodes)
    """
    if not replicate or not is_replication_enabled():
        return True, []
    
    replica_nodes = get_replica_nodes()
    required_replicas = get_required_replicas()
    successful_nodes = []
    failed_nodes = []
    
    logger.info(f"Replicating {operation.upper()} {key} to {len(replica_nodes)} nodes with failover")
    
    for node in replica_nodes:
        success = False
        
        # Retry logic for automatic failover
        for attempt in range(RETRY_ATTEMPTS):
            try:
                if operation == 'put':
                    url = f"{node['url']}/kv/{key}/"
                    response = requests.put(
                        url,
                        json={"value": value},
                        headers={"X-Replication": "true"},
                        timeout=timeout,
                    )
                elif operation == 'delete':
                    url = f"{node['url']}/kv/{key}/"
                    response = requests.delete(
                        url,
                        headers={"X-Replication": "true"},
                        timeout=timeout,
                    )
                else:
                    raise ValueError(f"Unknown operation: {operation}")
                
                if response.status_code in [200, 201, 204, 404]:
                    successful_nodes.append(node['url'])
                    success = True
                    logger.debug(f"Successfully replicated to {node['url']} on attempt {attempt + 1}")
                    break
                else:
                    logger.warning(
                        f"Failed to replicate to {node['url']} on attempt {attempt + 1}: "
                        f"{response.status_code}"
                    )
                    
            except requests.RequestException as e:
                logger.warning(
                    f"Error replicating to {node['url']} on attempt {attempt + 1}: {e}"
                )
                if attempt < RETRY_ATTEMPTS - 1:
                    time.sleep(RETRY_DELAY)
        
        if not success:
            failed_nodes.append(node['url'])
            # Mark node as unhealthy in cache
            cache_key = f"{HEALTH_CACHE_PREFIX}{node['url']}"
            cache.set(cache_key, False, HEALTH_CACHE_TIMEOUT)
    
    # Check if we have majority (including current node)
    total_successful = len(successful_nodes) + 1  # +1 for current node
    has_quorum = total_successful >= required_replicas
    
    if has_quorum:
        logger.info(
            f"Replication successful with failover: {total_successful}/{len(replica_nodes) + 1} nodes"
        )
    else:
        logger.error(
            f"Replication failed even with failover: only {total_successful}/"
            f"{len(replica_nodes) + 1} nodes, needed {required_replicas}"
        )
    
    return has_quorum, successful_nodes


def get_cluster_status() -> Dict:
    """
    Get status of all nodes in the cluster with health monitoring.
    
    Returns:
        Dictionary with cluster status information including health
    """
    replica_nodes = get_replica_nodes()
    node_statuses = []
    
    for node in replica_nodes:
        is_healthy = check_node_health(node['url'], use_cache=False)  # Fresh check
        node_statuses.append({
            'url': node['url'],
            'healthy': is_healthy,
        })
    
    healthy_count = sum(1 for node in node_statuses if node['healthy']) + 1  # +1 for current
    total_count = len(replica_nodes) + 1
    required_for_quorum = get_required_replicas()
    has_quorum = healthy_count >= required_for_quorum
    
    return {
        'replication_enabled': is_replication_enabled(),
        'total_nodes': total_count,
        'healthy_nodes': healthy_count,
        'unhealthy_nodes': total_count - healthy_count,
        'required_for_quorum': required_for_quorum,
        'has_quorum': has_quorum,
        'can_accept_writes': has_quorum,  # Can accept writes if we have quorum
        'automatic_failover_enabled': True,  # Failover is always enabled
        'retry_attempts': RETRY_ATTEMPTS,
        'nodes': node_statuses,
    }

