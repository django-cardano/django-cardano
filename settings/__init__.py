"""
This is a django-split-settings main file.
For more information read this:
https://github.com/sobolevn/django-split-settings
Default environment is `developement`.
To change settings file:
`DJANGO_ENV=production python manage.py runserver`
"""
import platform
from split_settings.tools import optional, include

hostname = platform.node().split('.')[0]

base_settings = [
    'base.py',  # standard django settings

    # You can even use glob:
    # 'components/*.py'

    # Optionally override some settings:
    optional(f'local/{hostname}.py'),
]

# Include settings:
include(*base_settings)
