from setuptools import setup, find_packages

setup(
    name='pushstream-python',
    version='1.0.0',
    description='PushStream Python SDK for real-time messaging',
    author='PushStream',
    license='MIT',
    py_modules=['pushstream'],
    install_requires=[
        'websocket-client>=1.6.0',
        'requests>=2.31.0',
    ],
    python_requires='>=3.7',
    keywords=['websocket', 'realtime', 'pusher', 'messaging'],
)
