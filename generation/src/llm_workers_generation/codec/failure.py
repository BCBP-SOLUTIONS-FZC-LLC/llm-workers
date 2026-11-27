"""The data converter a worker and the API client share (LLD WS-12).

``DefaultFailureConverterWithEncodedAttributes`` moves the message and stack trace of every failure
into encoded attributes, so the payload codec encrypts them: even the fixed text of an
``ApplicationError`` is unreadable in an exported history. The wrappers already raise content-free
errors (``activities/_guard.safe_error``); this is the second layer.
"""

from __future__ import annotations

from temporalio.converter import DataConverter, DefaultFailureConverterWithEncodedAttributes

from llm_workers_generation.codec.payload_codec import AesGcmPayloadCodec, KeyProvider


def build_data_converter(keys: KeyProvider) -> DataConverter:
    """A converter with the encrypting codec and the encoded-attributes failure converter."""
    return DataConverter(
        payload_codec=AesGcmPayloadCodec(keys),
        failure_converter_class=DefaultFailureConverterWithEncodedAttributes,
    )
