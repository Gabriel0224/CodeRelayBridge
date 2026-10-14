"""Use the macOS system CA bundle when the build machine's store is absent."""
import os
from pathlib import Path
import ssl
import sys

def configure_native_tls():
    if sys.platform != 'darwin':
        return
    if os.environ.get('SSL_CERT_FILE') or os.environ.get('SSL_CERT_DIR'):
        return
    default = ssl.get_default_verify_paths().cafile
    if default and Path(default).is_file():
        return
    system_bundle = Path('/etc/ssl/cert.pem')
    if system_bundle.is_file():
        os.environ['SSL_CERT_FILE'] = str(system_bundle)
