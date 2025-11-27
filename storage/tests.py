from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from storage.models import KeyValueEntry


class KeyValueApiTests(APITestCase):
    def test_put_and_read_key(self):
        url = reverse("storage:kv-detail", args=["alpha"])
        response = self.client.put(url, {"value": "first"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["value"], "first")

        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["key"], "alpha")
        self.assertEqual(response.data["value"], "first")

    def test_missing_key_returns_404(self):
        url = reverse("storage:kv-detail", args=["missing"])
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_range_query_returns_sorted_results(self):
        KeyValueEntry.objects.create(key="a", value="1")
        KeyValueEntry.objects.create(key="b", value="2")
        KeyValueEntry.objects.create(key="c", value="3")

        url = reverse("storage:kv-range")
        response = self.client.get(url, {"start": "a", "end": "b"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual([item["key"] for item in response.data["results"]], ["a", "b"])

    def test_batch_put_upserts_all_items(self):
        url = reverse("storage:kv-batch")
        payload = {
            "items": [
                {"key": "alpha", "value": "1"},
                {"key": "beta", "value": "2"},
            ]
        }
        response = self.client.post(url, payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)
        self.assertTrue(KeyValueEntry.objects.filter(key="alpha").exists())

    def test_delete_removes_key(self):
        KeyValueEntry.objects.create(key="temp", value="old")
        url = reverse("storage:kv-detail", args=["temp"])
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
