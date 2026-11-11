"""HTTPS helpers. Python from python.org on macOS ships without CA certificates until
'Install Certificates.command' is run, so use certifi's bundle when it is installed."""

import os
import ssl


def ssl_context():
    if os.environ.get("SSL_CERT_FILE"):      # an explicitly chosen CA bundle wins
        return ssl.create_default_context()
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()
