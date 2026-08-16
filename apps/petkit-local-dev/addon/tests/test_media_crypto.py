import os
import tempfile
from pathlib import Path

from petkit_local.media import crypto


def test_resolve_key_never_rotates_an_existing_key():
    """Regenerating would make every already-uploaded encrypted file
    permanently undecryptable, so a key on disk wins unconditionally."""
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "bucket_aes_key.txt").write_text("deadbeefdeadbeef\n")
        assert crypto.resolve_key({"data_dir": tmp}) == b"deadbeefdeadbeef"


def test_resolve_key_replaces_an_empty_key_file():
    """A zero-length file is what a torn pre-atomic write left behind; it
    holds no key, so there is nothing to lose by generating a new one."""
    with tempfile.TemporaryDirectory() as tmp:
        key_file = Path(tmp) / "bucket_aes_key.txt"
        key_file.write_text("")
        key = crypto.resolve_key({"data_dir": tmp})
        assert len(key) == 16
        assert key_file.read_text().strip() == key.decode("ascii")


def test_resolve_key_write_leaves_no_partial_files():
    """The key is written via atomic_write_json's temp-then-rename, so the
    data dir must end up holding exactly the key file — a half-written
    `bucket_aes_key.txt` would orphan every encrypted upload."""
    with tempfile.TemporaryDirectory() as tmp:
        crypto.resolve_key({"data_dir": tmp})
        assert os.listdir(tmp) == ["bucket_aes_key.txt"]


def test_resolve_key_creates_a_missing_data_dir():
    with tempfile.TemporaryDirectory() as tmp:
        nested = os.path.join(tmp, "does", "not", "exist")
        key = crypto.resolve_key({"data_dir": nested})
        assert len(key) == 16
        assert crypto.resolve_key({"data_dir": nested}) == key


def test_resolve_key_generates_and_persists_16_bytes():
    with tempfile.TemporaryDirectory() as tmp:
        config = {"data_dir": tmp}
        key1 = crypto.resolve_key(config)
        assert len(key1) == 16
        key2 = crypto.resolve_key(config)
        assert key1 == key2  # persisted, stable across calls
        assert (Path(tmp) / "bucket_aes_key.txt").exists()


def test_resolve_key_string_matches_resolve_key():
    with tempfile.TemporaryDirectory() as tmp:
        config = {"data_dir": tmp}
        assert crypto.resolve_key_string(config).encode("ascii") == crypto.resolve_key(config)


def test_looks_plaintext_jpeg_png_ts():
    assert crypto.looks_plaintext(b"\xff\xd8\xff\xe0" + b"\x00" * 20) is True
    assert crypto.looks_plaintext(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20) is True
    ts_packet = bytes([0x47]) + b"\x00" * 187 + bytes([0x47]) + b"\x00" * 187
    assert crypto.looks_plaintext(ts_packet) is True


def test_looks_plaintext_random_bytes_is_false():
    assert crypto.looks_plaintext(b"\x01\x02\x03\x04" * 10) is False
    assert crypto.looks_plaintext(b"") is False


def test_decrypt_aes_roundtrip():
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = b"0123456789abcdef"
    iv = b"\xaa" * 16
    plaintext = b"\xff\xd8\xff\xe0" + b"A" * 60  # 64 bytes, block-aligned, JPEG-ish header
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(plaintext) + encryptor.finalize()

    decrypted = crypto.decrypt_aes(ciphertext, key, "0x" + iv.hex())
    assert decrypted == plaintext
    assert crypto.looks_plaintext(decrypted) is True


def test_decrypt_aes_preserves_non_block_aligned_tail():
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = b"0123456789abcdef"
    iv = b"\xbb" * 16
    aligned = b"B" * 32
    tail = b"XY"  # 2 extra bytes, not a full block
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(aligned) + encryptor.finalize() + tail

    decrypted = crypto.decrypt_aes(ciphertext, key, iv.hex())
    assert decrypted == aligned + tail


def test_decrypt_aes_rejects_bad_iv():
    try:
        crypto.decrypt_aes(b"\x00" * 32, b"0123456789abcdef", "not-hex")
        assert False, "expected ValueError"
    except ValueError:
        pass

    try:
        crypto.decrypt_aes(b"\x00" * 32, b"0123456789abcdef", "aabb")  # too short
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_decrypt_aes_rejects_bad_key_length():
    try:
        crypto.decrypt_aes(b"\x00" * 32, b"short", "aa" * 16)
        assert False, "expected ValueError"
    except ValueError:
        pass
