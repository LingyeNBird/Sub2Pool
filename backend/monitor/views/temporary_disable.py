"""Administrator-only temporary disable controls; reads never change upstream state."""

from rest_framework import status

from .base import AdminAPIView, error, ok
from ..history_state import LeaseBusyError, LeaseLostError
from ..integrations.sub2api import Sub2APIError
from ..temporary_disable import (
    DisableNotFound,
    create_disable,
    disable_payload,
    disablable_models,
    restore_disable,
    update_disable,
)


class _DisableViewMixin:
    @staticmethod
    def _failure(exc: Exception):
        if isinstance(exc, DisableNotFound):
            return error(str(exc), status.HTTP_404_NOT_FOUND)
        if isinstance(exc, (LeaseBusyError, LeaseLostError)):
            return error(str(exc), status.HTTP_409_CONFLICT)
        if isinstance(exc, Sub2APIError):
            return error(str(exc), status.HTTP_502_BAD_GATEWAY)
        return error(str(exc), status.HTTP_400_BAD_REQUEST)


class TemporaryDisableModelListView(_DisableViewMixin, AdminAPIView):
    """Models the disable dialog may offer for one monitored account."""

    def get(self, _request, account_id: int):
        try:
            models = disablable_models(account_id)
        except (Sub2APIError, ValueError) as exc:
            return self._failure(exc)
        return ok({"models": models})


class TemporaryDisableView(_DisableViewMixin, AdminAPIView):
    """Create one temporary disable and return its journal row."""

    def post(self, request, account_id: int):
        data = request.data if isinstance(request.data, dict) else {}
        try:
            disable = create_disable(
                account_id=account_id,
                scope=data.get("scope"),
                model=data.get("model") or "",
                minutes=data.get("minutes"),
                user=request.user,
            )
        except (
            DisableNotFound,
            LeaseBusyError,
            LeaseLostError,
            Sub2APIError,
            ValueError,
        ) as exc:
            return self._failure(exc)
        return ok(disable_payload(disable), status.HTTP_201_CREATED)


class TemporaryDisableDetailView(_DisableViewMixin, AdminAPIView):
    """Move one disable's restore deadline, or restore it early."""

    def patch(self, request, disable_id: int):
        data = request.data if isinstance(request.data, dict) else {}
        try:
            disable = update_disable(
                disable_id=disable_id,
                minutes=data.get("minutes"),
            )
        except (
            DisableNotFound,
            LeaseBusyError,
            LeaseLostError,
            Sub2APIError,
            ValueError,
        ) as exc:
            return self._failure(exc)
        return ok(disable_payload(disable))

    def delete(self, _request, disable_id: int):
        try:
            disable = restore_disable(disable_id=disable_id)
        except (
            DisableNotFound,
            LeaseBusyError,
            LeaseLostError,
            Sub2APIError,
            ValueError,
        ) as exc:
            return self._failure(exc)
        return ok(disable_payload(disable))
