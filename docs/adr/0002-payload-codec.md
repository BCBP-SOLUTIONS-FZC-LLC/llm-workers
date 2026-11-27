# ADR-0002: payload codec, failure converter, key source

**Status:** accepted. **LLD:** §14.2 (WS-11..13). PL-2 (the key's IRSA permission) is open.

**Decision.** AES-256-GCM codec with the key id in payload metadata and authenticated as associated data, so
a rotated key still decrypts old history. `DefaultFailureConverterWithEncodedAttributes` moves failure
messages and stack traces through the codec. Wrappers also raise content-free `ApplicationError`s (fixed text,
no details, no chaining), so there are two layers. The key arrives through a `KeyProvider` port; this repository
imports no cloud SDK (WD-3), so the KMS-unwrapped data key is **mounted by the platform secret store**
(`PAYLOAD_CODEC_KEY_PATH`). **Assumption:** the platform can mount it. If it cannot, add a KMS-backed provider
in a module the architecture test whitelists.

**Not built:** the codec server for the Temporal UI (needs single sign-on, a platform item).
