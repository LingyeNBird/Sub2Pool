"""Administrator-only global burst activation; GET never changes balances."""

from .base import AdminAPIView, error, ok
from ..balance_operations import auto_apply_recommendations
from ..history_state import LeaseBusyError, LeaseLostError
from ..temporary_burst import burst_payload, start_session


class TemporaryBurstView(AdminAPIView):
    def get(self, request):
        return ok(burst_payload())

    def post(self, request):
        if (
            not isinstance(request.data, dict)
            or request.data.get("confirm") is not True
        ):
            return error(
                "请确认将所有参与者建议余额临时设为 9999，并在换周期时结算借用权益"
            )
        try:
            start_session()
        except (LeaseBusyError, LeaseLostError) as exc:
            return error(str(exc), 409)
        except ValueError as exc:
            return error(str(exc), 400)
        result = burst_payload()
        result["application"] = auto_apply_recommendations(explicit=True)
        return ok(result)
