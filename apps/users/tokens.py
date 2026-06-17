from datetime import timedelta
from rest_framework_simplejwt.tokens import RefreshToken

REMEMBER_ME_LIFETIME = timedelta(days=30)
DEFAULT_REFRESH_LIFETIME = timedelta(days=1)


class MedalizeRefreshToken(RefreshToken):
    """
    Custom refresh token that adjusts its lifetime based on the remember_me flag
    embedded in the payload at creation time.
    """

    @classmethod
    def for_user(cls, user, remember_me=False):
        token = super().for_user(user)
        if remember_me:
            token['remember_me'] = True
            token.set_exp(lifetime=REMEMBER_ME_LIFETIME)
        else:
            token['remember_me'] = False
        return token
