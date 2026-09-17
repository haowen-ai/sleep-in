"""Password hashes and revocable server-side sessions."""
import hashlib
import hmac
import secrets
from datetime import timedelta,datetime
from .store import stamp,now,uid


def password_hash(password):
    if not isinstance(password,str) or not 12<=len(password)<=1024:
        raise ValueError('Password must be 12–1024 characters')
    salt=secrets.token_hex(16)
    digest=hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),600000).hex()
    return f'pbkdf2_sha256${salt}${digest}'


def password_valid(password,stored):
    if not isinstance(password,str) or len(password)>1024:
        return False
    try:
        _,salt,expected=stored.split('$')
        actual=hashlib.pbkdf2_hmac('sha256',password.encode(),salt.encode(),600000).hex()
        return hmac.compare_digest(actual,expected)
    except ValueError:
        return False


def public_user(user):
    return {key:user[key] for key in ('id','username','role','locale','enabled')}


def revoke(tx,user_id):
    for session in tx.all('session'):
        if session['user_id']==user_id:
            tx.remove('session',session['id'])


def session_create(tx,user):
    raw=secrets.token_urlsafe(32)
    value={'id':hashlib.sha256(raw.encode()).hexdigest(),'user_id':user['id'],'csrf':secrets.token_urlsafe(32),'expires_at':(now()+timedelta(hours=12)).isoformat()}
    tx.put('session',value)
    return raw,value


def session_lookup(tx,raw):
    if not raw:
        return None,None
    s=tx.get('session',hashlib.sha256(raw.encode()).hexdigest())
    if not s or datetime.fromisoformat(s['expires_at'])<=now():
        return None,None
    user=tx.get('user',s['user_id'])
    if not user or not user['enabled']:
        return None,None
    return user,s
