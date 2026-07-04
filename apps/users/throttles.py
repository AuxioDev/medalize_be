from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class LoginRateThrottle(AnonRateThrottle):
    scope = 'login'


class RegisterRateThrottle(AnonRateThrottle):
    scope = 'register'


class PasswordResetRateThrottle(AnonRateThrottle):
    scope = 'password_reset'


class SocialLoginRateThrottle(AnonRateThrottle):
    scope = 'social_login'


class EmailChangeRateThrottle(UserRateThrottle):
    scope = 'email_change'
