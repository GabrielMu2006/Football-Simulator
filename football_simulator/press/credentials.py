"""Local-only credentials: session memory, optionally macOS Keychain.

Security.framework receives the secret in memory, never in command arguments.
No key is written to a game save, report, environment variable or log.
"""
from __future__ import annotations

import ctypes as C
import sys

from football_simulator.press import PressError
from football_simulator.press.provider import validate_key

SERVICE = "Football Simulator Development / DeepSeek"


class Keychain:
    def __init__(self, service=SERVICE):
        if sys.platform != 'darwin':
            raise PressError("钥匙串仅在 macOS 可用。")
        self.service = service
        self.cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.sec = C.CDLL('/System/Library/Frameworks/Security.framework/Security')
        ptr, index = C.c_void_p, C.c_long
        for name, args, result in (
            ('CFStringCreateWithCString', [ptr, C.c_char_p, C.c_uint32], ptr),
            ('CFDictionaryCreateMutable', [ptr, index, ptr, ptr], ptr),
            ('CFDictionarySetValue', [ptr, ptr, ptr], None), ('CFRelease', [ptr], None),
            ('CFDataCreate', [ptr, C.c_char_p, index], ptr),
            ('CFDataGetLength', [ptr], index), ('CFDataGetBytePtr', [ptr], ptr),
        ):
            fn = getattr(self.cf, name); fn.argtypes = args; fn.restype = result
        for name, args in (('SecItemCopyMatching', [ptr, C.POINTER(ptr)]),
                           ('SecItemAdd', [ptr, ptr]), ('SecItemUpdate', [ptr, ptr]),
                           ('SecItemDelete', [ptr])):
            fn = getattr(self.sec, name); fn.argtypes = args; fn.restype = C.c_int32

    def _constant(self, name, *, cf=False):
        return C.c_void_p.in_dll(self.cf if cf else self.sec, name).value

    def _query(self):
        # NULL callbacks: referenced objects stay alive until the operation ends.
        query = self.cf.CFDictionaryCreateMutable(None, 0, None, None)
        values = [self.cf.CFStringCreateWithCString(None, value.encode('utf-8'), 0x08000100)
                  for value in (self.service, 'deepseek-api')]
        for key, value in (('kSecClass', self._constant('kSecClassGenericPassword')),
                           ('kSecAttrService', values[0]), ('kSecAttrAccount', values[1])):
            self.cf.CFDictionarySetValue(query, self._constant(key), value)
        return query, values

    def _release(self, query, values):
        self.cf.CFRelease(query)
        for value in values:
            self.cf.CFRelease(value)

    def get(self):
        query, values = self._query()
        result = C.c_void_p()
        try:
            self.cf.CFDictionarySetValue(query, self._constant('kSecReturnData'), self._constant('kCFBooleanTrue', cf=True))
            code = self.sec.SecItemCopyMatching(query, C.byref(result))
            if code == -25300:
                return None
            if code:
                raise PressError("无法读取本机钥匙串，可改用仅本次使用。")
            return C.string_at(self.cf.CFDataGetBytePtr(result), self.cf.CFDataGetLength(result)).decode('utf-8')
        finally:
            if result.value:
                self.cf.CFRelease(result)
            self._release(query, values)

    def set(self, key):
        validate_key(key)
        query, values = self._query()
        attributes = self.cf.CFDictionaryCreateMutable(None, 0, None, None)
        data = self.cf.CFDataCreate(None, key.encode('utf-8'), len(key.encode('utf-8')))
        try:
            self.cf.CFDictionarySetValue(attributes, self._constant('kSecValueData'), data)
            code = self.sec.SecItemUpdate(query, attributes)
            if code == -25300:
                self.cf.CFDictionarySetValue(query, self._constant('kSecValueData'), data)
                code = self.sec.SecItemAdd(query, None)
            if code:
                raise PressError("无法保存到钥匙串，请选择仅本次使用。")
        finally:
            self.cf.CFRelease(attributes); self.cf.CFRelease(data)
            self._release(query, values)

    def delete(self):
        query, values = self._query()
        try:
            if self.sec.SecItemDelete(query) not in (0, -25300):
                raise PressError("无法移除钥匙串中的密钥。")
        finally:
            self._release(query, values)


class Credentials:
    def __init__(self, keychain=None):
        self._session = None
        self.keychain = keychain if keychain is not None else Keychain()

    def get(self):
        return self._session or self.keychain.get()

    def set(self, key, *, remember=False):
        validate_key(key)
        if remember:
            self.keychain.set(key)
        else:
            # Replacing a remembered key with a session key must not resurrect
            # the old key on the next launch.
            self.keychain.delete()
        self._session = key

    def clear(self):
        self.keychain.delete()
        self._session = None
