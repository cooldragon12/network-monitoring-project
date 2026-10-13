from django.conf import settings
from django.db import models
from django.utils import timezone

from config.tokens import TOKEN_PREFIX_LEN, generate_token, hash_token, token_prefix


class ApiToken(models.Model):
    """Personal API token for scripts/integrations. Only the hash is stored."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="api_tokens")
    name = models.CharField(max_length=100)
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    token_prefix = models.CharField(max_length=TOKEN_PREFIX_LEN, editable=False)
    created_at = models.DateTimeField(default=timezone.now)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["user", "name"], name="api_token_unique_name_per_user")]

    def __str__(self) -> str:
        return f"{self.name} ({self.token_prefix}…)"

    @classmethod
    def issue(cls, user, name: str) -> tuple["ApiToken", str]:
        """Create a token for ``user``; returns (instance, plaintext shown once)."""
        token = generate_token()
        instance = cls.objects.create(
            user=user, name=name, token_hash=hash_token(token), token_prefix=token_prefix(token)
        )
        return instance, token
