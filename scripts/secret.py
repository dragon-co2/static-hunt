"""Encrypts account passwords with Windows DPAPI before they're written to the settings file.

A protected value only decrypts on the same PC under the same Windows user — copying
settings_override.json to another machine (or reading it as another user) gives nothing usable.
Stored form: 'dpapi:<base64>'."""
import base64
import ctypes
import ctypes.wintypes

PREFIX = 'dpapi:'


class _BLOB(ctypes.Structure):
    _fields_ = [('cbData', ctypes.wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]


def _blob(data):
    buf = ctypes.create_string_buffer(data, len(data))
    return _BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _out(blob):
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob.pbData)


def protect(text):
    """Plain text -> 'dpapi:...' ('' stays '')."""
    if not text:
        return ''
    src, _keep = _blob(text.encode('utf-8'))
    dst = _BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(src), None, None, None, None, 0,
                                                   ctypes.byref(dst)):
        raise OSError('CryptProtectData failed')
    return PREFIX + base64.b64encode(_out(dst)).decode('ascii')


def unprotect(value):
    """'dpapi:...' -> plain text. Returns '' for empty or undecryptable values (e.g. a settings
    file copied from another PC)."""
    if not value or not value.startswith(PREFIX):
        return ''
    src, _keep = _blob(base64.b64decode(value[len(PREFIX):]))
    dst = _BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0,
                                                     ctypes.byref(dst)):
        return ''
    return _out(dst).decode('utf-8')


def is_protected(value):
    return isinstance(value, str) and value.startswith(PREFIX)
