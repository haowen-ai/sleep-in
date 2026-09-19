"""Real loopback SMTP exchanges only; no public server or real recipient."""
import copy
from email import policy
from email.parser import BytesParser
import socketserver
import threading
import pytest
from taskconsole.store import Store, stamp
from taskconsole.workflows_operations import WorkflowOperations


class SMTPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(5)
        def reply(line):self.wfile.write(line+b'\r\n');self.wfile.flush()
        reply(b'220 synthetic.local ESMTP')
        envelope={'from':None,'to':[]}
        while raw:=self.rfile.readline(65537):
            command,_,argument=raw.rstrip(b'\r\n').partition(b' ')
            command=command.upper()
            if command in {b'EHLO',b'HELO'}:reply(b'250 synthetic.local')
            elif command==b'MAIL':envelope['from']=argument.decode();reply(b'250 sender accepted')
            elif command==b'RCPT':
                envelope['to'].append(argument.decode())
                self.server.attempted.append(argument.decode())
                reply(b'550 synthetic recipient rejected' if self.server.reject or argument.decode() in self.server.reject_recipients else b'250 recipient accepted')
            elif command==b'DATA':
                reply(b'354 End with dot')
                lines=[]
                while True:
                    line=self.rfile.readline(65537)
                    if line in {b'.\r\n',b'.\n',b''}:break
                    lines.append(line[1:] if line.startswith(b'..') else line)
                self.server.received.append({**envelope,'message':BytesParser(policy=policy.default).parsebytes(b''.join(lines))})
                self.server.delivered.set();reply(b'250 accepted')
            elif command==b'QUIT':reply(b'221 bye');break
            elif command==b'RSET':reply(b'250 reset')
            else:reply(b'502 unsupported command')


@pytest.fixture
def smtp():
    server=socketserver.ThreadingTCPServer(('127.0.0.1',0),SMTPHandler)
    server.daemon_threads=True;server.reject=False;server.reject_recipients=set();server.received=[];server.attempted=[];server.delivered=threading.Event()
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield server
    finally:server.shutdown();server.server_close();thread.join(timeout=3)


@pytest.fixture
def setup(tmp_path,smtp):
    store=Store(tmp_path,'sqlite:///'+str(tmp_path/'smtp.sqlite'));op=WorkflowOperations(store)
    channel=op.save_channel({'name':'Synthetic SMTP','kind':'email','config':{'host':'127.0.0.1','port':smtp.server_address[1],'tls':'none','from':'sender@example.invalid','to':['exact-recipient@example.invalid']}})
    run={'id':'synthetic-run','workflow_id':'synthetic-workflow','workflow_name':'Synthetic report','status':'failed','error':'Known synthetic-secret-value must be masked','created_at':stamp(),'finished_at':stamp(),'test':False,'nodes':{'query':{'status':'failed','attempts':[{'number':1,'status':'failed'}]}},'snapshot':{'notifications':{'channel_ids':[channel['id']],'events':['failed']}}}
    with store.transaction() as tx:
        tx.put('workflow_credential',{'id':'credential','encrypted':store.fernet.encrypt(b'synthetic-secret-value').decode()})
        tx.put('workflow_run',copy.deepcopy(run))
    try:yield store,op,channel,run
    finally:store.engine.dispose()


def test_actual_loopback_smtp_exact_recipient_masked_failure_and_dedupe(setup,smtp):
    store,op,channel,original=setup
    op.tick()
    assert smtp.delivered.wait(1)
    assert smtp.attempted==['TO:<exact-recipient@example.invalid>']
    assert len(smtp.received)==1
    delivery=smtp.received[0];message=delivery['message']
    assert delivery['from']=='FROM:<sender@example.invalid>'
    assert message['To']=='exact-recipient@example.invalid'
    assert str(message['Subject'])=='Sleep In · failed · Synthetic report'
    content=message.get_content()
    assert 'Known [redacted] must be masked' in content
    assert 'synthetic-secret-value' not in content
    assert '/workflow-runs/synthetic-run' in content
    notices=op.deliveries();assert len(notices)==1 and notices[0]['status']=='sent' and notices[0]['attempts']==1
    op.tick();assert len(smtp.received)==1
    with store.transaction() as tx:assert tx.get('workflow_run',original['id'])==original


def test_actual_smtp_recipient_rejection_does_not_change_business_state(setup,smtp):
    store,op,channel,original=setup;smtp.reject=True
    op.tick()
    assert smtp.attempted==['TO:<exact-recipient@example.invalid>'] and smtp.received==[]
    notice=op.deliveries()[0]
    assert notice['status']=='failed' and notice['attempts']==1
    assert 'SMTPRecipientsRefused' in notice['error']
    with store.transaction() as tx:assert tx.get('workflow_run',original['id'])==original
    op.tick()
    assert len(smtp.attempted)==1


def test_actual_smtp_partial_recipient_refusal_is_not_reported_as_full_success(setup,smtp):
    store,op,channel,original=setup
    op.save_channel({'name':'Two synthetic recipients','kind':'email','config':{'host':'127.0.0.1','port':smtp.server_address[1],'tls':'none','from':'sender@example.invalid','to':['exact-recipient@example.invalid','rejected@example.invalid']}},channel['id'])
    smtp.reject_recipients={'TO:<rejected@example.invalid>'}
    op.tick()
    assert smtp.delivered.wait(1) and len(smtp.received)==1
    assert smtp.attempted==['TO:<exact-recipient@example.invalid>','TO:<rejected@example.invalid>']
    notice=op.deliveries()[0]
    assert notice['status']=='uncertain', 'A partial delivery must neither claim full success nor permit a whole-message retry'
    assert 'partial' in notice['error'].lower()
    with pytest.raises(ValueError,match='uncertain'):
        op.retry_delivery(notice['id'],1)
    with store.transaction() as tx:assert tx.get('workflow_run',original['id'])==original
    op.tick();assert len(smtp.received)==1
