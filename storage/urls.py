from django.urls import path

from storage.views import BatchPutView, KeyValueRangeView, KeyValueView, HealthCheckView

app_name = "storage"

urlpatterns = [
    path("kv/batch/", BatchPutView.as_view(), name="kv-batch"),
    path("kv/<str:key>/", KeyValueView.as_view(), name="kv-detail"),
    path("kv/", KeyValueRangeView.as_view(), name="kv-range"),
    path("health/", HealthCheckView.as_view(), name="health"),
]
