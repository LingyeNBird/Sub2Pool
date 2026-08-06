from django.contrib import admin

from .models import (
    AppSettings,
    BlockedIPAddress,
    LoginEvent,
    NotificationEvent,
    Observation,
    Participant,
    ParticipantSnapshot,
    ParticipantUsageSample,
    QuotaCycle,
)

# Django Admin 仅作为应急运维入口；日常使用由中文前端完成。
admin.site.register(AppSettings)
admin.site.register(Participant)
admin.site.register(QuotaCycle)
admin.site.register(Observation)
admin.site.register(ParticipantSnapshot)
admin.site.register(NotificationEvent)
admin.site.register(ParticipantUsageSample)
admin.site.register(LoginEvent)
admin.site.register(BlockedIPAddress)
