import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from monitor.models import AnnouncementRead
from monitor.tests.helpers import jwt_login


@pytest.mark.django_db
def test_admin_announcements_have_persistent_per_user_read_state():
    User = get_user_model()
    admin = User.objects.create_superuser(
        username="owner",
        password="very-strong-password",
        email="owner@example.com",
    )
    regular = User.objects.create_user(
        username="viewer",
        password="Viewer-Access-2026!secure",
        email="viewer@example.com",
    )
    admin_client = Client()
    admin_headers, _ = jwt_login(admin_client)
    regular_client = Client()
    regular_headers, _ = jwt_login(
        regular_client,
        username="viewer",
        password="Viewer-Access-2026!secure",
    )

    response = admin_client.get("/api/announcements", **admin_headers)

    assert response.status_code == 200
    data = response.json()["data"]
    unread_codes = {item["code"] for item in data["items"] if not item["read"]}
    assert data["unread_count"] == len(unread_codes)
    announcement = data["items"][0]
    assert announcement["read"] is False
    assert announcement["read_at"] is None
    assert regular_client.get(
        "/api/announcements",
        **regular_headers,
    ).status_code == 403
    assert regular_client.post(
        f"/api/announcements/{announcement['code']}/read",
        **regular_headers,
    ).status_code == 403

    marked = admin_client.post(
        f"/api/announcements/{announcement['code']}/read",
        **admin_headers,
    )

    assert marked.status_code == 200
    assert marked.json()["data"]["read"] is True
    assert marked.json()["data"]["read_at"] is not None
    assert AnnouncementRead.objects.filter(
        user=admin,
        announcement_code=announcement["code"],
    ).count() == 1

    repeated = admin_client.post(
        f"/api/announcements/{announcement['code']}/read",
        **admin_headers,
    )
    assert repeated.status_code == 200
    assert AnnouncementRead.objects.filter(user=admin).count() == 1
    refreshed = admin_client.get("/api/announcements", **admin_headers).json()[
        "data"
    ]
    assert refreshed["unread_count"] == len(unread_codes) - 1
    assert next(item for item in refreshed["items"] if item["code"] == announcement["code"])["read"] is True
    assert admin_client.post(
        "/api/announcements/not-a-real-announcement/read",
        **admin_headers,
    ).status_code == 404
    assert AnnouncementRead.objects.filter(user=regular).count() == 0
