"""Local administration commands; never ships a fixed administrator password."""
import argparse
import getpass
import os
from pathlib import Path
from .store import Store


def main():
    parser=argparse.ArgumentParser(prog='python -m taskconsole')
    sub=parser.add_subparsers(dest='command',required=True)
    for command in ('init','serve','worker','setup-token'):sub.add_parser(command)
    reset=sub.add_parser('reset-password');reset.add_argument('username')
    back=sub.add_parser('backup');back.add_argument('destination');back.add_argument('--include-runs',action='store_true')
    rest=sub.add_parser('restore');rest.add_argument('archive')
    args=parser.parse_args()
    path=Path(os.environ.get('APP_STATE_DIR','state')).resolve()
    url=os.environ.get('DATABASE_URL',f'sqlite:///{path}/console.db')
    if args.command=='serve':
        import uvicorn
        from .app import create_app
        uvicorn.run(create_app(path,url),host=os.environ.get('APP_HOST','0.0.0.0'),port=int(os.environ.get('APP_PORT','8080')),access_log=False)
        return
    store=Store(path,url)
    if args.command=='init':
        from .app import initialize
        initialize(store)
        print('Initialized. Retrieve your local setup token with: python -m taskconsole setup-token')
    elif args.command=='setup-token':
        with store.transaction() as tx:
            if tx.all('user'):parser.exit(1,'Setup is already complete. Use reset-password for local recovery.\n')
        print((path/'setup-token').read_text().strip())
    elif args.command=='worker':
        from .worker import Worker
        Worker(store).serve()
    elif args.command=='reset-password':
        from .auth import password_hash,revoke
        password=getpass.getpass('New password (at least 12 characters): ')
        if password!=getpass.getpass('Confirm new password: '):parser.error('Passwords do not match')
        hashed=password_hash(password)
        with store.transaction() as tx:
            user=next((u for u in tx.all('user') if u['username']==args.username),None)
            if not user:parser.error('User not found')
            user.update(password=hashed,enabled=True,role='admin');tx.put('user',user);revoke(tx,user['id'])
        print('Password reset; old sessions revoked.')
    elif args.command=='backup':
        from .backup import backup
        backup(store,args.destination,args.include_runs);print('Backup created without user credentials, sessions or variable values.')
    elif args.command=='restore':
        from .backup import restore
        restore(store,args.archive);print('Restored with schedules disabled. Complete setup and re-enter variables before enabling tasks.')


if __name__=='__main__':main()
