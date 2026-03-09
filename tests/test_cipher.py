from cryptography.fernet import Fernet

from app.infrastructure.security.fernet_cipher import FernetCipher


def test_cipher_roundtrip() -> None:
    key = Fernet.generate_key().decode()
    cipher = FernetCipher(key)
    encrypted = cipher.encrypt("secret")
    assert encrypted != "secret"
    assert cipher.decrypt(encrypted) == "secret"
