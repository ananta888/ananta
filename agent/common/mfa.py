import pyotp
import qrcode
import io
import base64

def generate_mfa_secret():
    return pyotp.random_base32()

def get_totp_uri(username: str, secret: str, issuer: str = "Ananta"):
    return pyotp.totp.TOTP(secret).provisioning_uri(name=username, issuer_name=issuer)

def verify_totp(secret: str, token: str):
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
