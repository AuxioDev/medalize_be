import uuid


def randomize_upload_filename(file, extension):
    """Rename an uploaded file to a random name with the given extension,
    discarding the client-supplied filename/extension entirely. Content
    validation only checks file bytes — without this, a file whose bytes pass
    as a valid PDF/image could still be stored under an attacker-chosen
    extension (e.g. .html) and served back with a mismatched Content-Type."""
    file.name = f'{uuid.uuid4().hex}.{extension}'
    return file
