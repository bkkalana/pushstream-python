from setuptools import setup, find_packages

setup(
    name='pushstream-python',
    version='1.0.1',
    description='Python SDK for PushStream real-time messaging',
    author='Ceylon IT Solutions',
    author_email='support@pushstream.online',
    url='https://github.com/bkkalana/pushstream-python',
    packages=find_packages(),
    py_modules=['pushstream'],
    test_suite='tests',
    install_requires=[
        'websocket-client>=1.6.0',
        'requests>=2.31.0',
    ],
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: 3.7',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
    ],
)
