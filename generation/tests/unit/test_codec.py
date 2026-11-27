import pytest
from cryptography.exceptions import InvalidTag
from llm_workers_generation.codec.failure import build_data_converter
from llm_workers_generation.codec.payload_codec import AesGcmPayloadCodec, StaticKeyProvider
from temporalio.api.common.v1 import Payload
from temporalio.converter import DefaultFailureConverterWithEncodedAttributes

K1, K2 = b"1" * 32, b"2" * 32


def payload(data: bytes = b'"hello"') -> Payload:
    return Payload(metadata={"encoding": b"json/plain"}, data=data)


async def test_round_trip_and_ciphertext_is_unreadable() -> None:
    codec = AesGcmPayloadCodec(StaticKeyProvider({"k1": K1}, "k1"))
    [enc] = await codec.encode([payload(b"CANARY-SECRET")])
    assert b"CANARY-SECRET" not in enc.data and enc.metadata["encryption-key-id"] == b"k1"
    [dec] = await codec.decode([enc])
    assert dec.data == b"CANARY-SECRET" and dec.metadata["encoding"] == b"json/plain"


async def test_rotated_key_still_decrypts_old_history() -> None:
    old = AesGcmPayloadCodec(StaticKeyProvider({"k1": K1}, "k1"))
    [enc] = await old.encode([payload()])
    new = AesGcmPayloadCodec(StaticKeyProvider({"k1": K1, "k2": K2}, "k2"))
    [dec] = await new.decode([enc])
    assert dec.data == b'"hello"'
    [enc2] = await new.encode([payload()])
    assert enc2.metadata["encryption-key-id"] == b"k2"


async def test_tampering_and_wrong_key_are_rejected() -> None:
    codec = AesGcmPayloadCodec(StaticKeyProvider({"k1": K1}, "k1"))
    [enc] = await codec.encode([payload()])
    enc.data = enc.data[:-1] + bytes([enc.data[-1] ^ 1])
    with pytest.raises(InvalidTag):
        await codec.decode([enc])
    other = AesGcmPayloadCodec(StaticKeyProvider({"k1": K2}, "k1"))
    [good] = await codec.encode([payload()])
    with pytest.raises(InvalidTag):
        await other.decode([good])


async def test_unencrypted_payloads_pass_through() -> None:
    codec = AesGcmPayloadCodec(StaticKeyProvider({"k1": K1}, "k1"))
    [out] = await codec.decode([payload()])
    assert out.data == b'"hello"'


def test_keys_must_be_aes256() -> None:
    with pytest.raises(ValueError):
        StaticKeyProvider({"k": b"short"}, "k")


def test_data_converter_uses_encoded_failure_attributes() -> None:
    dc = build_data_converter(StaticKeyProvider({"k1": K1}, "k1"))
    assert dc.failure_converter_class is DefaultFailureConverterWithEncodedAttributes
    assert isinstance(dc.payload_codec, AesGcmPayloadCodec)
