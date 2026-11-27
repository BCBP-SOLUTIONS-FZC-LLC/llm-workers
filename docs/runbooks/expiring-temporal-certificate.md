# An expiring Temporal client certificate

**What you see.** Fewer than 14 days to the expiry of the worker's client certificate.

**Check and do.**
1. Workers dial Temporal with a client certificate and key from mounted files (`TEMPORAL_TLS_CERT_PATH`, `TEMPORAL_TLS_KEY_PATH`); there is no CA path and no API key. The secret named by `temporal.tlsSecretName` holds `tls.crt` and `tls.key`.
2. The cluster operator issues the certificate (who and how: open item PL-7). The API and the workers share one.
3. Replace the secret's contents, then restart the pods (a rolling restart): the files are read at start. An expired certificate makes readiness fail.

**Do not.** Do not store the key in the image or in the chart values.
