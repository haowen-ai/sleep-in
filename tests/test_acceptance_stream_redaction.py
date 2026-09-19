"""Known credentials must be masked before native worker log bytes reach disk."""
from concurrent.futures import ThreadPoolExecutor
import copy
import os
from pathlib import Path
import signal
import threading
import time

import pytest

from taskconsole.workflows_runtime import run_script


@pytest.mark.parametrize('secrets,payload,expected', [
    (['abcdef', 'abc', 'cdef'], b'abcdef|abc|cdef', b'[redacted]|[redacted]|[redacted]'),
    (['秘密🚀'], '前秘密🚀后'.encode(), '前[redacted]后'.encode()),
    (['a', 'ab'], b'ab-a-zzz', b'***-***-zzz'),
    (['end-secret'], b'prefix-end-secret', b'prefix-[redacted]'),
    (['', 'same', 'same'], b'same same', b'[redacted] [redacted]'),
    (['secret', 'xx[redacted]'], b'xxsecret', b'xx***'),
])
def test_stream_masks_every_chunk_partition_utf8_overlap_and_short_values(secrets, payload, expected):
    from taskconsole.workflows_log_redaction import StreamRedactor
    for cut in range(len(payload) + 1):
        redactor = StreamRedactor(secrets)
        actual = redactor.feed(payload[:cut]) + redactor.feed(payload[cut:]) + redactor.finish()
        assert actual == expected
        assert redactor.finish() == b''
    redactor = StreamRedactor(secrets)
    assert b''.join(redactor.feed(bytes([byte])) for byte in payload) + redactor.finish() == expected


def test_stream_never_releases_an_unresolved_secret_prefix():
    from taskconsole.workflows_log_redaction import StreamRedactor
    redactor = StreamRedactor(['abcdef'])
    assert redactor.feed(b'xxabc') == b''
    assert redactor.feed(b'defyy') == b'xx[redacted]'
    assert redactor.finish() == b'yy'


def until(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('Owned log capture did not reach barrier')


@pytest.mark.parametrize('ending', ['success', 'failure', 'cancel', 'kill'])
def test_real_ongoing_disk_logs_are_masked_before_worker_exit(tmp_path, ending):
    secret = 'Known秘密🚀Credential'
    directory = tmp_path / 'attempt'
    first = tmp_path / 'first'; release = tmp_path / 'release'; ready = tmp_path / 'ready'; finish = tmp_path / 'finish'
    source = ('import os,time\nfrom pathlib import Path\ndef main(inputs):\n'
        ' value=inputs["secret"].encode();split=len(value)//2\n'
        ' os.write(1,b"PUBLIC BEFORE\\n"+value[:split])\n'
        f' Path({str(first)!r}).touch()\n'
        f' while not Path({str(release)!r}).exists():time.sleep(.01)\n'
        ' os.write(1,value[split:]+b"\\n"+b"x"*8192)\n'
        ' os.write(2,b"STDERR "+value+b" END\\n")\n'
        f' Path({str(ready)!r}).touch()\n'
        f' while not Path({str(finish)!r}).exists():time.sleep(.01)\n'
        + (' os._exit(7)\n' if ending == 'failure' else ' return {"ok":True}\n'))
    pids = []; cancel = threading.Event()
    node = {'id': 'stream', 'kind': 'python', 'source': source, 'config': {}, '_on_process': pids.append}
    original = copy.copy(node)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_script, node, {'secret': secret}, directory, tmp_path,
                             cancel.is_set, redaction_values=[secret])
        try:
            until(lambda: first.exists() or future.done())
            if future.done():
                future.result()
            assert first.exists()
            release.touch()
            until(lambda: ready.exists() and (directory / 'stdout.txt').exists()
                  and (directory / 'stdout.txt').stat().st_size > 1000)
            assert not future.done() and pids
            for name in ['stdout', 'stderr']:
                content = (directory / (name + '.txt')).read_bytes()
                assert secret.encode() not in content
                assert b'[redacted]' in content
            if ending == 'cancel':
                cancel.set()
            elif ending == 'kill':
                os.killpg(pids[0], signal.SIGKILL)
            else:
                finish.touch()
            result = future.result(timeout=8)
        finally:
            cancel.set(); release.touch(); finish.touch()
    assert result['status'] == {'success': 'succeeded', 'failure': 'failed', 'cancel': 'cancelled', 'kill': 'failed'}[ending]
    assert node == original
    expected = {'stdout': len(b'PUBLIC BEFORE\n' + secret.encode() + b'\n' + b'x' * 8192),
                'stderr': len(b'STDERR ' + secret.encode() + b' END\n')}
    for name in ['stdout', 'stderr']:
        content = (directory / (name + '.txt')).read_bytes()
        assert secret.encode() not in content and '[redacted]' in result[name]
        assert result['log_capture'][name]['bytes_seen'] == expected[name]
        assert result['log_capture'][name]['bytes_stored'] == len(content)


@pytest.mark.parametrize('short', [False, True])
def test_redacted_truncation_tail_caps_file_and_preserves_raw_count(tmp_path, short):
    secret = 'Q' if short else 'SecretAcrossQuotaBoundary'
    raw = (b'Q' * 200000 if short else b'x' * (1048576 - 3) + secret.encode() + b'x' * 20000) + b' END ' + secret.encode()
    source = 'import os\ndef main(inputs):\n os.write(1,inputs["payload"].encode())\n return {}'
    directory = tmp_path / 'attempt'
    result = run_script({'id': 'quota', 'kind': 'python', 'source': source, 'config': {}},
        {'payload': raw.decode()}, directory, tmp_path, redaction_values=[secret])
    content = (directory / 'stdout.txt').read_bytes()
    assert result['status'] == 'succeeded'
    assert len(content) <= 1048576 and secret.encode() not in content
    assert content.endswith(b' END [redacted]')
    assert result['stdout'].endswith(' END [redacted]')
    assert result['log_capture']['stdout']['bytes_seen'] == len(raw)
    assert result['log_capture']['stdout']['truncated'] is True


@pytest.mark.parametrize('secret,raw_size', [('X\n[', 1100000), ('Log', 1100000), ('Log', 150000)])
def test_truncation_composition_and_returned_marker_cannot_synthesize_secret(tmp_path, secret, raw_size):
    raw = bytearray(b'a' * raw_size)
    header = f'\n[Log truncated: {len(raw)} bytes produced; final output follows]\n'.encode()
    # All raw output is harmless. Joining the retained prefix with a truncation
    # header must not create a credential absent from either separate segment.
    if raw_size > 1048576:
        raw[1048576 - 100000 - len(header) - 1] = ord('X')
    assert secret.encode() not in raw
    source = 'import os\ndef main(inputs):\n os.write(1,inputs["payload"].encode())\n os.write(2,inputs["payload"].encode())\n return {}'
    directory = tmp_path / 'attempt'
    result = run_script({'id': 'join', 'kind': 'python', 'source': source, 'config': {}},
        {'payload': raw.decode()}, directory, tmp_path, redaction_values=[secret])
    assert result['status'] == 'succeeded'
    for name in ('stdout', 'stderr'):
        content = (directory / (name + '.txt')).read_bytes()
        assert secret.encode() not in content, 'Truncation recombination leaked a known secret to disk'
        assert secret not in result[name], 'Returned log-tail notice synthesized a known secret'
        assert len(content) <= 1048576
        assert content.endswith(b'a' * 100000)
        assert result['log_capture'][name]['bytes_seen'] == len(raw)
        assert result['log_capture'][name]['bytes_stored'] == len(content)
        assert result['log_capture'][name]['truncated'] is (raw_size > 1048576)
