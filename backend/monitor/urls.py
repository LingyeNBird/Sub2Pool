from django.urls import path

from .views.auth import (
    LoginView,
    LogoutView,
    MeView,
    NetworkCheckView,
    PasswordView,
    RefreshView,
)
from .views.account_status import AccountStatusView
from .views.dashboard import (
    ApplyParticipantRecommendationView,
    DashboardView,
)
from .views.database import DatabaseExportView, DatabaseImportView
from .views.maintenance import (
    HistoricalRebuildApplyView,
    HistoricalRebuildPlanDetailView,
    HistoricalRebuildPlanListView,
)
from .views.monitoring import RunMonitorView
from .views.participants import (
    ParticipantDetailView,
    ParticipantListView,
    ReadOnlyParticipantListView,
    Sub2APIUserListView,
)
from .views.particle_trajectory import ParticleTrajectoryView
from .views.public import AuthClientConfigView, HealthView
from .views.readonly_api import ReadOnlyAPIRootView, ReadOnlyOpenAPIView
from .views.notifications import NotificationListView
from .views.observations import (
    ObservationExclusionView,
    ObservationFastCorrectionDetailView,
    ObservationListView,
    ObservationManualStartView,
    ObservationRebuildView,
    ObservationRestoreView,
)
from .views.security import (
    BlockedIPAddressDetailView,
    BlockedIPAddressListView,
    LoginEventListView,
)
from .views.settings import (
    MonitoredAccountDetailView,
    MonitoredAccountListView,
    OpenAIAccountListView,
    SettingsView,
    ReadOnlyAPIKeyView,
    TestEmailView,
    TestSub2APIView,
)
from .views.statistics import (
    ParticipantAPIUsageView,
    ReadOnlyParticipantAPIUsageView,
    ReadOnlyStatisticsView,
    StatisticsView,
)
from .views.users import SystemUserDetailView, SystemUserListView

urlpatterns = [
    path("health", HealthView.as_view()),
    path("auth/client-config", AuthClientConfigView.as_view()),
    path("auth/network-check", NetworkCheckView.as_view()),
    path("auth/login", LoginView.as_view()),
    path("auth/refresh", RefreshView.as_view()),
    path("auth/logout", LogoutView.as_view()),
    path("auth/me", MeView.as_view()),
    path("auth/password", PasswordView.as_view()),
    path("login-events", LoginEventListView.as_view()),
    path("ip-blocks", BlockedIPAddressListView.as_view()),
    path("ip-blocks/<int:block_id>", BlockedIPAddressDetailView.as_view()),
    path("dashboard", DashboardView.as_view()),
    path("account-status", AccountStatusView.as_view()),
    path(
        "dashboard/participants/<int:participant_id>/apply-recommendation",
        ApplyParticipantRecommendationView.as_view(),
    ),
    path("statistics", StatisticsView.as_view()),
    path(
        "statistics/participants/<int:participant_id>/api-usage",
        ParticipantAPIUsageView.as_view(),
    ),
    path("v1", ReadOnlyAPIRootView.as_view()),
    path("v1/openapi.json", ReadOnlyOpenAPIView.as_view()),
    path("v1/participants", ReadOnlyParticipantListView.as_view()),
    path("v1/statistics", ReadOnlyStatisticsView.as_view()),
    path(
        "v1/statistics/participants/<int:participant_id>/api-usage",
        ReadOnlyParticipantAPIUsageView.as_view(),
    ),
    path("database/export", DatabaseExportView.as_view()),
    path("database/import", DatabaseImportView.as_view()),
    path("participants/sub2api-users", Sub2APIUserListView.as_view()),
    path("participants", ParticipantListView.as_view()),
    path("participants/<int:participant_id>", ParticipantDetailView.as_view()),
    path("system-users", SystemUserListView.as_view()),
    path("system-users/<int:user_id>", SystemUserDetailView.as_view()),
    path("observations", ObservationListView.as_view()),
    path(
        "observations/<int:observation_id>/fast-correction",
        ObservationFastCorrectionDetailView.as_view(),
    ),
    path("observations/rebuild", ObservationRebuildView.as_view()),
    path(
        "observations/<int:observation_id>/exclude",
        ObservationExclusionView.as_view(),
    ),
    path(
        "observations/<int:observation_id>/restore",
        ObservationRestoreView.as_view(),
    ),
    path(
        "observations/<int:observation_id>/manual-start",
        ObservationManualStartView.as_view(),
    ),
    path("particle-trajectory", ParticleTrajectoryView.as_view()),
    path("notifications", NotificationListView.as_view()),
    path("settings", SettingsView.as_view()),
    path("settings/openai-accounts", OpenAIAccountListView.as_view()),
    path("settings/monitored-accounts", MonitoredAccountListView.as_view()),
    path(
        "settings/monitored-accounts/<int:account_id>",
        MonitoredAccountDetailView.as_view(),
    ),
    path("settings/test-sub2api", TestSub2APIView.as_view()),
    path("settings/test-email", TestEmailView.as_view()),
    path("settings/readonly-api-key", ReadOnlyAPIKeyView.as_view()),
    path(
        "settings/data-maintenance/history-rebuild-plans",
        HistoricalRebuildPlanListView.as_view(),
    ),
    path(
        "settings/data-maintenance/history-rebuild-plans/<uuid:plan_id>",
        HistoricalRebuildPlanDetailView.as_view(),
    ),
    path(
        "settings/data-maintenance/history-rebuild-plans/<uuid:plan_id>/apply",
        HistoricalRebuildApplyView.as_view(),
    ),
    path("monitor/run", RunMonitorView.as_view()),
]
