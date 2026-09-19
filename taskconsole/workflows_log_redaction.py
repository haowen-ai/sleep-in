"""Incremental literal credential masking before worker output is persisted."""
import re


class StreamRedactor:
    def __init__(self, values=()):
        secrets = sorted({value.encode('utf-8') for value in values if value}, key=lambda value: (-len(value), value))
        self._pattern = re.compile(b'|'.join(re.escape(value) for value in secrets)) if secrets else None
        self._length = max(map(len, secrets), default=1)
        self._pending = b''
        self._replacement = b'[redacted]'
        self._suppress = False
        used = set(b''.join(secrets))
        if ord('[') in used or ord(']') in used or any(value in self._replacement for value in secrets):
            # Neither the marker itself nor its join with adjacent output may
            # synthesize a known credential, including one-character values.
            safe = next((byte for byte in b'*#~^|_' + bytes(range(33, 127)) if byte not in used), None)
            if safe is None:
                # With every printable byte a credential, no printable marker
                # can be safe. Suppression is preferable to writing a secret.
                self._suppress = True
            else:
                self._replacement = bytes([safe]) * 3

    def feed(self, chunk):
        return self._consume(chunk, False)

    def finish(self):
        return self._consume(b'', True)

    def _consume(self, chunk, final):
        if self._suppress:
            return b''
        if self._pattern is None:
            return chunk
        data = self._pending + chunk
        # A match beginning after this boundary might still acquire more bytes
        # in the next pipe read. Keep that suffix exclusively in memory.
        limit = len(data) if final else max(0, len(data) - self._length + 1)
        result = bytearray(); position = 0
        for match in self._pattern.finditer(data):
            if match.start() >= limit:
                break
            result.extend(data[position:match.start()]); result.extend(self._replacement)
            position = match.end()
            if position >= limit:
                break
        end = max(position, limit)
        result.extend(data[position:end])
        self._pending = data[end:]
        return bytes(result)
