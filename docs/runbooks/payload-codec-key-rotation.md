# A payload-codec key rotation

**What you see.** The key that encrypts what is written to Temporal history must be replaced.

**Check and do.**
1. The key is a 32-byte file in the secret named by `codecKey.secretName`, mounted at `/var/run/codec/<keyId>`; `PAYLOAD_CODEC_KEY_ID` names it. The id is carried in each encrypted payload so old history still decrypts.
2. Add the new key to the secret **beside** the old one, set `codecKey.keyId` to the new id, and roll the pods. Keep the old key as long as any history encrypted with it can be read (retention, plus the longest run).
3. Check how the codec reads several keys before removing the old one: whether a worker can hold more than the current key is a code question, see `codec/` and ADR-0002.

**Do not.** Do not delete the old key while open workflows or readable history still use it.
