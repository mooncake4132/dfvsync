#!/usr/bin/env python
from distutils.core import setup


setup(
    name='dfvsync',
    version='0.1',
    description='Dockerfile version sync',
    author='mooncake4132',
    url='https://github.com/mooncake4132/dfvsync',
    py_modules=['dfvsync'],
    entry_points={'console_scripts': ['dfvsync = dfvsync:main']},
)
