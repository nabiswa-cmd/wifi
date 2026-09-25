release: python manage.py migrate --noinput
web: gunicorn config.wsgi --bind 0.0.0.0:$PORT --workers 2 --threads 4 --worker-class gthread --log-file -
worker: python worker.py
