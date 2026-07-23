import os

import cloudinary.uploader
import cloudinary.utils
from cloudinary_storage.storage import MediaCloudinaryStorage, RawMediaCloudinaryStorage
from django.conf import settings

# How long a generated diploma/avatar URL stays usable once handed out in an
# API response or an admin page. Only enforced as a real, checkable expiry
# when CLOUDINARY_AUTH_TOKEN_KEY is configured (Cloudinary's "token-based
# authentication" add-on — a separate key generated in the Cloudinary
# console under Settings > Security, distinct from the API secret). Without
# that key we still sign every URL with the API secret so it can never be
# guessed or served without our backend generating it, but that signature
# doesn't expire on its own — see apps/core/storage.py's PrivateMediaMixin.
SIGNED_URL_TTL_SECONDS = 300


class PrivateMediaMixin:
    """Uploads to Cloudinary's `authenticated` delivery type instead of the
    default public `upload` type, and always serves a signed URL rather than
    a plain public one. Used for doctor diplomas and avatars: identity/PII
    documents that must never be reachable by guessing or enumerating a
    URL (the pre-launch audit found the previous public delivery type +
    non-randomized filenames made diplomas directly fetchable by anyone on
    the internet with no authentication at all)."""

    def _upload(self, name, content):
        options = {
            'use_filename': True,
            'resource_type': self._get_resource_type(name),
            'tags': self.TAG,
            'type': 'authenticated',
        }
        folder = os.path.dirname(name)
        if folder:
            options['folder'] = folder
        return cloudinary.uploader.upload(content, **options)

    def delete(self, name):
        response = cloudinary.uploader.destroy(
            name, invalidate=True, resource_type=self._get_resource_type(name),
            type='authenticated',
        )
        return response['result'] == 'ok'

    def _get_url(self, name):
        name = self._prepend_prefix(name)
        auth_token_key = getattr(settings, 'CLOUDINARY_AUTH_TOKEN_KEY', '')
        auth_token = (
            {'key': auth_token_key, 'duration': SIGNED_URL_TTL_SECONDS}
            if auth_token_key else None
        )
        url, _options = cloudinary.utils.cloudinary_url(
            name,
            resource_type=self._get_resource_type(name),
            type='authenticated',
            sign_url=True,
            secure=True,
            auth_token=auth_token,
        )
        return url


class PrivateImageCloudinaryStorage(PrivateMediaMixin, MediaCloudinaryStorage):
    """Avatars: signed authenticated delivery. Still fine to hand the
    resulting URL to any patient via a public-facing serializer (that's the
    product intent — patients see doctor avatars), but the URL can no
    longer be guessed or crawled without ever calling our API."""


class PrivateRawCloudinaryStorage(PrivateMediaMixin, RawMediaCloudinaryStorage):
    """Diplomas and other non-image verification documents: signed
    authenticated delivery. Only ever handed back to the uploading doctor
    or shown to Django Admin staff reviewing verification."""
