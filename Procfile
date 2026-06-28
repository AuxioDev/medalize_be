release: python manage.py migrate --noinput
web: gunicorn config.wsgi --bind 0.0.0.0:$PORT --workers 3
worker: celery -A config worker -l info
beat: celery -A config beat -l info
