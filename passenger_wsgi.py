"""cPanel entry point. Setup Python App (Passenger) looks for ``application`` in this file."""

from config.wsgi import application  # noqa: F401
