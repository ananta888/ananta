import pyotp
import qrcode
import io
import base64
import hashlib
from cryptography.fernet import Fernet
from agent.config import settings

def _get_fernet():
    key = settings.mfa_encryption_key
    if not key:
        # Fallback: Key aus secret_key ableiten
        key = base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode()).digest())
    else:
        try:
            Fernet(key)
        except Exception:
             key = base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest())
    return Fernet(key)

def encrypt_secret(secret: str) -> str:
    if not secret:
        return None
    f = _get_fernet()
    return f.encrypt(secret.encode()).decode()

def decrypt_secret(encrypted_secret: str) -> str:
    if not encrypted_secret:
        return None
    f = _get_fernet()
    try:
        return f.decrypt(encrypted_secret.encode()).decode()
    except Exception:
        # Fallback für bereits existierende Klartext-Secrets (Migration)
        return encrypted_secret

def generate_mfa_secret():
    return pyotp.random_base32()

def get_totp_uri(username: str, secret: str, issuer: str = "Ananta"):
    # Das Secret muss hier im Klartext vorliegen
    return pyotp.totp.TOTP(secret).provisioning_uri(name=username, issuer_name=issuer)

def verify_totp(secret: str, token: str):
    # Das Secret muss hier im Klartext vorliegen
    if not secret:
        return False
    totp = pyotp.totp.TOTP(secret)
    return totp.verify(token)

def generate_qr_code_base64(uri: str):
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode()
