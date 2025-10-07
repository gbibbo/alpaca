from setuptools import setup, find_packages

setup(
    name="trading_platform",
    version="1.0.0",
    packages=find_packages(),
    install_requires=[
        'python-jose[cryptography]',
        'passlib[bcrypt]',
        'python-multipart',
        'websockets',
    ],
)
