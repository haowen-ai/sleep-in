"""Real loopback SMTP outcomes; never contact an external recipient."""
import socketserver
import threading
import pytest
from test_workflow_operations import store, operations, put_run


@pytest.mark.parametrize('outcome', ['partial', 'lost_ack', 'refused', 'quit_failure', 'partial_quit_failure'])
def test_smtp_uncertain_delivery_cannot_duplicate_accepted_mail(store, outcome):
    accepted = []
    class SMTP(socketserver.StreamRequestHandler):
        def handle(self):
            self.wfile.write(b'220 fixture\r\n')
            recipients = []
            while line := self.rfile.readline():
                verb = line.split(b' ', 1)[0].strip().upper()
                if verb in {b'EHLO', b'HELO'}:
                    self.wfile.write(b'250 fixture\r\n')
                elif verb == b'MAIL':
                    self.wfile.write(b'250 OK\r\n')
                elif verb == b'RCPT':
                    if outcome == 'refused' or (outcome.startswith('partial') and b'refused@fixture' in line):
                        self.wfile.write(b'550 rejected\r\n')
                    else:
                        recipients.append(line.decode().strip())
                        self.wfile.write(b'250 OK\r\n')
                elif verb == b'DATA':
                    self.wfile.write(b'354 Send data\r\n')
                    content = bytearray()
                    while (chunk := self.rfile.readline()) not in {b'.\r\n', b''}:
                        content.extend(chunk)
                        assert len(content) < 1024 * 1024
                    accepted.append((recipients, bytes(content)))
                    if outcome == 'lost_ack':
                        return
                    self.wfile.write(b'250 Accepted\r\n')
                elif verb == b'QUIT':
                    self.wfile.write(b'500 Teardown failed\r\n' if outcome.endswith('quit_failure') else b'221 Bye\r\n'); return
                else:
                    self.wfile.write(b'250 OK\r\n')
    server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), SMTP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        op = operations(store)
        op.save_channel({'name': 'Isolated SMTP', 'kind': 'email', 'config': {
            'host': '127.0.0.1', 'port': server.server_address[1], 'tls': 'none',
            'from': 'sender@fixture', 'to': ['accepted@fixture', 'refused@fixture']}}, 'c1')
        put_run(store)
        op.on_run_terminal('r1'); op.tick()
        delivery = op.deliveries()[0]
        assert delivery['status'] == ('failed' if outcome == 'refused' else 'uncertain')
        if outcome == 'refused':
            assert accepted == []
            assert op.retry_delivery(delivery['id'], 1)['status'] == 'pending'
        else:
            assert len(accepted) == 1
            with pytest.raises(ValueError, match='uncertain'):
                op.retry_delivery(delivery['id'], 1)
            op.tick()
            assert len(accepted) == 1
        with store.transaction() as tx:
            assert tx.get('workflow_run', 'r1')['status'] == 'failed'
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)
