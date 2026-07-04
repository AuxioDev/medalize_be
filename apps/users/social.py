"""Verification of Google / Apple id_tokens for social login.

Each verifier checks the token signature against the provider's public keys
and validates issuer + audience (our app's client id / bundle id, configured
via GOOGLE_OAUTH_CLIENT_IDS / APPLE_BUNDLE_IDS env vars), then returns
normalized claims: provider_uid, email, email_verified, first/last name.
"""
import logging

from django.conf import settings

logger = logging.getLogger(__name__)

APPLE_JWKS_URL = 'https://appleid.apple.com/auth/keys'
APPLE_ISSUER = 'https://appleid.apple.com'

_apple_jwk_client = None


class SocialTokenError(Exception):
    """The id_token failed verification or is missing required claims."""


def _normalize(claims, verified_default=False):
    # Apple sends email_verified as the string 'true'/'false'; Google as bool.
    verified = claims.get('email_verified', verified_default)
    return {
        'provider_uid': claims.get('sub', ''),
        'email': (claims.get('email') or '').lower(),
        'email_verified': str(verified).lower() == 'true',
        'first_name': claims.get('given_name', ''),
        'last_name': claims.get('family_name', ''),
    }


def _verify_google(raw_token):
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token as google_id_token

    audiences = settings.GOOGLE_OAUTH_CLIENT_IDS
    if not audiences:
        raise SocialTokenError('Google sign-in is not configured on the server.')
    try:
        # audience=None skips the built-in single-audience check; we accept any
        # of the configured client ids (iOS / Android / web) below.
        claims = google_id_token.verify_oauth2_token(
            raw_token, google_requests.Request(), audience=None
        )
    except ValueError:
        raise SocialTokenError('Invalid or expired Google token.')
    except Exception:
        logger.exception('Google token verification failed unexpectedly')
        raise SocialTokenError('Could not verify Google token.')
    if claims.get('aud') not in audiences:
        raise SocialTokenError('Google token was issued for a different application.')
    return _normalize(claims)


def _get_apple_jwk_client():
    global _apple_jwk_client
    if _apple_jwk_client is None:
        import jwt

        _apple_jwk_client = jwt.PyJWKClient(APPLE_JWKS_URL)
    return _apple_jwk_client


def _verify_apple(raw_token):
    import jwt

    audiences = settings.APPLE_BUNDLE_IDS
    if not audiences:
        raise SocialTokenError('Apple sign-in is not configured on the server.')
    try:
        signing_key = _get_apple_jwk_client().get_signing_key_from_jwt(raw_token)
        claims = jwt.decode(
            raw_token,
            signing_key.key,
            algorithms=['RS256'],
            audience=audiences,
            issuer=APPLE_ISSUER,
        )
    except jwt.PyJWTError:
        raise SocialTokenError('Invalid or expired Apple token.')
    except Exception:
        logger.exception('Apple token verification failed unexpectedly')
        raise SocialTokenError('Could not verify Apple token.')
    return _normalize(claims)


_VERIFIERS = {
    'google': _verify_google,
    'apple': _verify_apple,
}


def verify_id_token(provider, raw_token):
    verifier = _VERIFIERS.get(provider)
    if verifier is None:
        raise SocialTokenError('Unsupported provider.')
    claims = verifier(raw_token)
    if not claims['provider_uid']:
        raise SocialTokenError('Token is missing the subject claim.')
    return claims
