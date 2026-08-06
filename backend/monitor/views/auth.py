"""管理员 JWT 登录、刷新、登出和密码管理。"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from rest_framework import serializers as drf_serializers
from rest_framework.exceptions import ParseError
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from .base import AdminAPIView, PublicAPIView, error, ok
from ..login_audit import record_login_attempt, request_addresses
from ..serializers import LoginSerializer, PasswordChangeSerializer


def _cookie_name() -> str:
    return settings.JWT_REFRESH_COOKIE_NAME


def _set_refresh_cookie(response, refresh_token: str) -> None:
    """Refresh Token 只进入 HttpOnly Cookie，前端 JavaScript 无法读取。"""
    max_age = int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds())
    response.set_cookie(
        key=_cookie_name(),
        value=refresh_token,
        max_age=max_age,
        httponly=True,
        secure=settings.JWT_REFRESH_COOKIE_SECURE,
        samesite=settings.JWT_REFRESH_COOKIE_SAMESITE,
        path=settings.JWT_REFRESH_COOKIE_PATH,
    )


def _delete_refresh_cookie(response) -> None:
    response.delete_cookie(
        _cookie_name(),
        path=settings.JWT_REFRESH_COOKIE_PATH,
        samesite=settings.JWT_REFRESH_COOKIE_SAMESITE,
    )


def _issue_tokens(user) -> tuple[str, str]:
    refresh = RefreshToken.for_user(user)
    return str(refresh.access_token), str(refresh)


def _blacklist_cookie(request) -> None:
    encoded = request.COOKIES.get(_cookie_name())
    if not encoded:
        return
    try:
        RefreshToken(encoded).blacklist()
    except TokenError:
        # 过期或已轮换的 Token 不能再次使用；登出和改密仍继续清理 Cookie。
        pass


class LoginView(PublicAPIView):
    def post(self, request):
        try:
            raw_payload = request.data
        except ParseError:
            record_login_attempt(
                request._request,
                {},
                username="",
                success=False,
                failure_reason="请求格式错误",
            )
            return error("请求体必须是有效的 JSON 对象")

        payload = raw_payload if isinstance(raw_payload, dict) else {}
        serializer = LoginSerializer(data=raw_payload)
        if not serializer.is_valid():
            record_login_attempt(
                request._request,
                payload,
                username=str(payload.get("username", "")),
                success=False,
                failure_reason="请求格式错误",
            )
            return error("登录参数无效", details=serializer.errors)

        username = serializer.validated_data["username"]
        password = serializer.validated_data["password"]
        request_ip, _ = request_addresses(request._request)
        throttle_key = f"login-fail:{request_ip or 'unknown'}:{username[:100]}"
        failures = int(cache.get(throttle_key, 0))
        if failures >= settings.LOGIN_FAILURE_LIMIT:
            record_login_attempt(
                request._request,
                payload,
                username=username,
                success=False,
                failure_reason="登录失败次数过多",
            )
            return error("登录失败次数过多，请稍后再试", 429)

        user = authenticate(
            request=request._request,
            username=username,
            password=password,
        )
        if user is None or not user.is_staff:
            cache.set(
                throttle_key,
                failures + 1,
                settings.LOGIN_FAILURE_WINDOW_SECONDS,
            )
            record_login_attempt(
                request._request,
                payload,
                username=username,
                success=False,
                failure_reason="用户名、密码或权限错误",
            )
            return error("用户名或密码错误", 401)

        cache.delete(throttle_key)
        access, refresh = _issue_tokens(user)
        record_login_attempt(
            request._request,
            payload,
            username=user.get_username(),
            success=True,
        )
        response = ok({"username": user.get_username(), "access": access})
        _set_refresh_cookie(response, refresh)
        return response


class RefreshView(PublicAPIView):
    def post(self, request):
        encoded = request.COOKIES.get(_cookie_name())
        if not encoded:
            return error("登录已过期，请重新登录", 401)

        serializer = TokenRefreshSerializer(data={"refresh": encoded})
        try:
            serializer.is_valid(raise_exception=True)
        except (TokenError, drf_serializers.ValidationError):
            response = error("登录已过期，请重新登录", 401)
            _delete_refresh_cookie(response)
            return response

        access = serializer.validated_data["access"]
        rotated_refresh = serializer.validated_data.get("refresh")
        response = ok({"access": access})
        if rotated_refresh:
            _set_refresh_cookie(response, rotated_refresh)
        return response


class LogoutView(AdminAPIView):
    def post(self, request):
        _blacklist_cookie(request)
        response = ok({"logged_out": True})
        _delete_refresh_cookie(response)
        return response


class MeView(AdminAPIView):
    def get(self, request):
        return ok(
            {
                "username": request.user.get_username(),
                "is_staff": request.user.is_staff,
            }
        )


class PasswordView(AdminAPIView):
    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data)
        if not serializer.is_valid():
            return error("密码参数无效", details=serializer.errors)

        old_password = serializer.validated_data["old_password"]
        new_password = serializer.validated_data["new_password"]
        if not request.user.check_password(old_password):
            return error("当前密码不正确")
        try:
            validate_password(new_password, request.user)
        except ValidationError as exc:
            return error("新密码不符合安全要求", details=list(exc.messages))

        # 先吊销当前 Refresh Token，再用新密码哈希签发新的一对 Token。
        _blacklist_cookie(request)
        request.user.set_password(new_password)
        request.user.save(update_fields=["password"])
        access, refresh = _issue_tokens(request.user)
        response = ok({"changed": True, "access": access})
        _set_refresh_cookie(response, refresh)
        return response
