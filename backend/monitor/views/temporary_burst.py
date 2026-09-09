"""Administrator-only burst mode controls; GET never changes balances."""

from .base import AdminAPIView, error, ok
from ..balance_operations import auto_apply_recommendations
from ..history_state import LeaseBusyError, LeaseLostError
from ..temporary_burst import burst_payload, start_session, stop_session, set_exhaustion_reminder


class TemporaryBurstView(AdminAPIView):
    def get(self, request):
        return ok(burst_payload())

    def patch(self, request):
        if not isinstance(request.data, dict) or type(request.data.get("reminder_enabled")) is not bool:
            return error("请明确指定是否启用本轮用满提醒")
        try:
            set_exhaustion_reminder(
                request.data["reminder_enabled"], request.data.get("session_id"),
            )
        except (LeaseBusyError, LeaseLostError) as exc:
            return error(str(exc), 409)
        except ValueError as exc:
            return error(str(exc), 400)
        return ok(burst_payload())

    def post(self, request):
        if not isinstance(request.data, dict) or request.data.get("confirm") is not True:
            return error("请确认开启临时爽蹬")
        carryover = request.data.get("carryover_enabled")
        if type(carryover) is not bool:
            return error("请选择结转或不结转模式")
        if not carryover and request.data.get("riders_notified") is not True:
            return error("不结转模式需先告知所有车友：本轮多用不追账、少用不补偿")
        try:
            start_session(carryover)
        except (LeaseBusyError, LeaseLostError) as exc:
            return error(str(exc), 409)
        except ValueError as exc:
            return error(str(exc), 400)
        result = burst_payload()
        result["application"] = auto_apply_recommendations(explicit=True)
        return ok(result)

    def delete(self, request):
        if not isinstance(request.data, dict) or request.data.get("confirm") is not True:
            return error("请确认提前终止爽蹬")
        try:
            stop_session(request.data.get("session_id"))
        except (LeaseBusyError, LeaseLostError) as exc:
            return error(str(exc), 409)
        except ValueError as exc:
            return error(str(exc), 400)
        result = burst_payload()
        result["application"] = auto_apply_recommendations(explicit=True)
        return ok(result)
