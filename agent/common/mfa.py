import base64
import hashlib
import io

try:
    import pyotp
except ImportError:
    pyotp = None

try:
    import qrcode
except ImportError:
    qrcode = None

try:
    from cryptography.fernet import Fernet
except ImportError:
    Fernet = None

from agent.config import settings


def _get_fernet():
    if Fernet is None:
        raise RuntimeError("cryptography package not installed")
    key = settings.mfa_encryption_key
    if not key:
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
    if pyotp is None:
        raise RuntimeError("pyotp package not installed")
    return pyotp.random_base32()


def get_totp_uri(username: str, secret: str, issuer: str = "Ananta"):
    if pyotp is None:
        raise RuntimeError("pyotp package not installed")
    return pyotp.totp.TOTP(secret).provisioning_uri(name=username, issuer_name=issuer)


def verify_totp(secret: str, token: str):
    if pyotp is None:
        raise RuntimeError("pyotp package not installed")
    if not secret:
        return False
    totp = pyotp.totp.TOTP(secret)
    return totp.verify(token)


def generate_qr_code_base64(uri: str):
    if qrcode is None:
        raise RuntimeError("qrcode package not installed")
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buffered = io.BytesIO()
    img.save(buffered, format="PNG")
    return base64.b64encode(buffered.getvalue()).decode()
