from django.urls import path

from . import api

urlpatterns = [
    path("health", api.health),
    path("auth/csrf", api.csrf),
    path("auth/client-config", api.auth_client_config),
    path("auth/login", api.login_view),
    path("auth/logout", api.logout_view),
    path("auth/me", api.me),
    path("auth/password", api.change_password),
    path("login-events", api.login_events),
    path("dashboard", api.dashboard),
    path("statistics", api.statistics_view),
    path("participants", api.participants_view),
    path("participants/<int:participant_id>", api.participant_detail),
    path("observations", api.observations),
    path("notifications", api.notifications),
    path("settings", api.settings_view),
    path("settings/test-sub2api", api.test_sub2api),
    path("settings/test-email", api.test_email),
    path("monitor/run", api.run_monitor_view),
]
