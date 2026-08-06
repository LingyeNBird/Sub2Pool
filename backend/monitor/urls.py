from django.urls import path

from .views.auth import LoginView, LogoutView, MeView, PasswordView, RefreshView
from .views.dashboard import DashboardView
from .views.monitoring import RunMonitorView
from .views.participants import ParticipantDetailView, ParticipantListView
from .views.public import AuthClientConfigView, HealthView
from .views.records import (
    LoginEventListView,
    NotificationListView,
    ObservationListView,
)
from .views.settings import (
    OpenAIAccountListView,
    SettingsView,
    TestEmailView,
    TestSub2APIView,
)
from .views.statistics import StatisticsView

urlpatterns = [
    path("health", HealthView.as_view()),
    path("auth/client-config", AuthClientConfigView.as_view()),
    path("auth/login", LoginView.as_view()),
    path("auth/refresh", RefreshView.as_view()),
    path("auth/logout", LogoutView.as_view()),
    path("auth/me", MeView.as_view()),
    path("auth/password", PasswordView.as_view()),
    path("login-events", LoginEventListView.as_view()),
    path("dashboard", DashboardView.as_view()),
    path("statistics", StatisticsView.as_view()),
    path("participants", ParticipantListView.as_view()),
    path("participants/<int:participant_id>", ParticipantDetailView.as_view()),
    path("observations", ObservationListView.as_view()),
    path("notifications", NotificationListView.as_view()),
    path("settings", SettingsView.as_view()),
    path("settings/openai-accounts", OpenAIAccountListView.as_view()),
    path("settings/test-sub2api", TestSub2APIView.as_view()),
    path("settings/test-email", TestEmailView.as_view()),
    path("monitor/run", RunMonitorView.as_view()),
]
