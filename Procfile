release: python manage.py migrate && python manage.py sync_home_updates
web: gunicorn novel_creator.wsgi --log-file -
