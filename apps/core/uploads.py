import uuid

from PIL import Image

# Guards every Image.open()/img.load() in the codebase (diploma + avatar
# uploads both import this module) against decompression-bomb uploads: a
# small, highly-compressible image (e.g. a huge solid-color PNG) can stay
# under the 5-10 MB size cap while decoding to gigabytes of RAM. 40 MP is far
# beyond any legitimate avatar/scanned-diploma photo (a 24 MP DSLR photo is
# well under this) while still well below PIL's ~179 MP default warning
# threshold. PIL raises DecompressionBombError past this limit, which the
# callers' existing `except Exception` already turns into a validation error.
Image.MAX_IMAGE_PIXELS = 40_000_000


def randomize_upload_filename(file, extension):
    """Rename an uploaded file to a random name with the given extension,
    discarding the client-supplied filename/extension entirely. Content
    validation only checks file bytes — without this, a file whose bytes pass
    as a valid PDF/image could still be stored under an attacker-chosen
    extension (e.g. .html) and served back with a mismatched Content-Type."""
    file.name = f'{uuid.uuid4().hex}.{extension}'
    return file
