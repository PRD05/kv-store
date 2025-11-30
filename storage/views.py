from django.http import Http404
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from storage.models import KeyValueEntry
from storage.serializers import (
    BatchPutSerializer,
    KeyValueRangeResponseSerializer,
    KeyValueSerializer,
    KeyValueWriteSerializer,
)
from storage.services import batch_put, delete_value, put_value, read_range, read_value
from storage.replication import get_cluster_status


class KeyValueView(APIView):
    """Handle single key/value operations."""

    def _get_entry(self, key: str):
        try:
            return read_value(key)
        except KeyValueEntry.DoesNotExist as exc:
            raise Http404 from exc

    @extend_schema(
        operation_id="read_key",
        summary="Read a key/value pair",
        description="Retrieve the value associated with a given key.",
        parameters=[
            OpenApiParameter(
                name="key",
                type=str,
                location=OpenApiParameter.PATH,
                description="The key to retrieve",
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=KeyValueSerializer,
                description="Successfully retrieved the key/value pair",
            ),
            404: OpenApiResponse(description="Key not found"),
        },
        tags=["Key-Value Operations"],
    )
    def get(self, request, key: str):
        entry = self._get_entry(key)
        return Response(KeyValueSerializer(entry).data)

    @extend_schema(
        operation_id="put_key",
        summary="Create or update a key/value pair",
        description="Insert or update a key/value pair. Creates a new entry if the key doesn't exist, otherwise updates the existing value and increments the version.",
        parameters=[
            OpenApiParameter(
                name="key",
                type=str,
                location=OpenApiParameter.PATH,
                description="The key to create or update",
            ),
        ],
        request=KeyValueWriteSerializer,
        responses={
            200: OpenApiResponse(
                response=KeyValueSerializer,
                description="Successfully updated the existing key/value pair",
            ),
            201: OpenApiResponse(
                response=KeyValueSerializer,
                description="Successfully created a new key/value pair",
            ),
        },
        tags=["Key-Value Operations"],
    )
    def put(self, request, key: str):
        serializer = KeyValueWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        # Check if this is a replication request (to prevent infinite loops)
        is_replication = request.headers.get('X-Replication') == 'true'
        
        try:
            entry, created = put_value(
                key, 
                serializer.validated_data["value"],
                replicate=not is_replication  # Don't replicate if this IS a replication
            )
        except Exception as e:
            # Replication failure
            return Response(
                {"detail": f"Replication failed: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
        status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(KeyValueSerializer(entry).data, status=status_code)

    @extend_schema(
        operation_id="delete_key",
        summary="Delete a key/value pair",
        description="Remove a key/value pair from the store.",
        parameters=[
            OpenApiParameter(
                name="key",
                type=str,
                location=OpenApiParameter.PATH,
                description="The key to delete",
            ),
        ],
        responses={
            204: OpenApiResponse(description="Successfully deleted the key/value pair"),
            404: OpenApiResponse(description="Key not found"),
        },
        tags=["Key-Value Operations"],
    )
    def delete(self, request, key: str):
        # Check if this is a replication request
        is_replication = request.headers.get('X-Replication') == 'true'
        
        try:
            deleted = delete_value(key, replicate=not is_replication)
        except Exception as e:
            return Response(
                {"detail": f"Replication failed: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
        if not deleted:
            raise Http404
        return Response(status=status.HTTP_204_NO_CONTENT)


class KeyValueRangeView(APIView):
    """Return values whose keys fall within the provided inclusive range."""

    @extend_schema(
        operation_id="read_key_range",
        summary="Read key/value pairs in a range",
        description="Retrieve key/value pairs whose keys fall within the specified inclusive range [start, end]. Results are sorted by key. Supports pagination for large datasets.",
        parameters=[
            OpenApiParameter(
                name="start",
                type=str,
                location=OpenApiParameter.QUERY,
                description="The start key (inclusive)",
                required=True,
            ),
            OpenApiParameter(
                name="end",
                type=str,
                location=OpenApiParameter.QUERY,
                description="The end key (inclusive)",
                required=True,
            ),
            OpenApiParameter(
                name="limit",
                type=int,
                location=OpenApiParameter.QUERY,
                description="Maximum number of results to return (default: 10000, max: 10000)",
                required=False,
            ),
            OpenApiParameter(
                name="cursor",
                type=str,
                location=OpenApiParameter.QUERY,
                description="Cursor for pagination (key of last item from previous page)",
                required=False,
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=KeyValueRangeResponseSerializer,
                description="Successfully retrieved key/value pairs in the range",
            ),
            400: OpenApiResponse(description="Invalid range parameters"),
        },
        tags=["Key-Value Operations"],
    )
    def get(self, request):
        start_key = request.query_params.get("start")
        end_key = request.query_params.get("end")
        if not start_key or not end_key:
            return Response(
                {"detail": "start and end query parameters are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if start_key > end_key:
            return Response(
                {"detail": "start key must be lexicographically <= end key"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        
        # Parse pagination parameters
        limit = request.query_params.get("limit")
        limit = int(limit) if limit else None
        
        cursor = request.query_params.get("cursor")
        
        # Use memory-efficient range query with pagination
        entries, next_cursor, has_more = read_range(
            start_key, end_key, limit=limit, cursor=cursor
        )
        
        serializer = KeyValueSerializer(entries, many=True)
        response_data = {
            "count": len(serializer.data),
            "results": serializer.data,
            "has_more": has_more,
        }
        
        if next_cursor:
            response_data["next_cursor"] = next_cursor
        
        return Response(response_data)


class BatchPutView(APIView):
    """Upsert multiple key/value pairs in a single request."""

    @extend_schema(
        operation_id="batch_put",
        summary="Batch create or update key/value pairs",
        description="Insert or update multiple key/value pairs in a single atomic transaction. All operations succeed or fail together. Duplicate keys within the batch are not allowed.",
        request=BatchPutSerializer,
        responses={
            200: OpenApiResponse(
                response=KeyValueSerializer(many=True),
                description="Successfully processed all key/value pairs in the batch",
            ),
            400: OpenApiResponse(description="Invalid batch request (e.g., duplicate keys, empty items)"),
        },
        tags=["Key-Value Operations"],
    )
    def post(self, request):
        serializer = BatchPutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        # Check if this is a replication request
        is_replication = request.headers.get('X-Replication') == 'true'
        
        try:
            entries = batch_put(
                serializer.validated_data["items"],
                replicate=not is_replication
            )
        except ValueError as e:
            # Handle batch size limit errors
            raise ValidationError({"detail": str(e)})
        except Exception as e:
            # Handle replication errors
            return Response(
                {"detail": f"Replication failed: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        
        return Response(
            KeyValueSerializer(entries, many=True).data, status=status.HTTP_200_OK
        )


class HealthCheckView(APIView):
    """Health check and cluster status endpoint."""
    
    @extend_schema(
        operation_id="health_check",
        summary="Health check and cluster status",
        description="Returns health status of this node and cluster replication status.",
        responses={
            200: OpenApiResponse(
                description="Node is healthy and cluster status information",
            ),
        },
        tags=["Health & Monitoring"],
    )
    def get(self, request):
        cluster_status = get_cluster_status()
        
        return Response({
            "status": "healthy",
            "cluster": cluster_status,
        }, status=status.HTTP_200_OK)
