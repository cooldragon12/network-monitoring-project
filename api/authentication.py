from datetime import timedelta

from django.utils import timezone
from rest_framework import authentication, exceptions

from api.models import ApiToken
from config.tokens import hash_token, parse_bearer

# Avoid a DB write on every request just to bump last_used_at.
_LAST_USED_RESOLUTION = timedelta(minutes=5)


class ApiTokenAuthentication(authentication.BaseAuthentication):
    """``Authorization: Bearer <token>`` using hashed personal API tokens."""

    def authenticate(self, request):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header:
            return None
        token = parse_bearer(header)
        if token is None:
            return None
        api_token = ApiToken.objects.select_related("user").filter(token_hash=hash_token(token)).first()
        if api_token is None or not api_token.user.is_active:
            raise exceptions.AuthenticationFailed("Invalid or revoked token.")
        now = timezone.now()
        if api_token.last_used_at is None or now - api_token.last_used_at > _LAST_USED_RESOLUTION:
            ApiToken.objects.filter(pk=api_token.pk).update(last_used_at=now)
        return api_token.user, api_token

    def authenticate_header(self, request):
        return "Bearer"
