# API Key Handling for a Self-Hosted Multi-Provider App

Scope: a single-user, self-hosted web app where the user enters their own provider keys (Anthropic, OpenAI-compatible endpoints) in a browser UI and the server calls providers on their behalf. Multi-tenant notes are flagged where they change the advice.

Labels used below: **[Requirement]** = source uses "must/never/should not" wording; **[Recommendation]** = source uses "should/recommend/prefer" or it is a provider's guidance; **[Inference]** = my synthesis, not stated by a source.

Source access note: the OWASP site (cheatsheetseries.owasp.org) and cryptography.io did not resolve from this environment, and neither did help.openai.com or developers.openai.com. OWASP and cryptography content below was read from the official GitHub repositories (OWASP/CheatSheetSeries, pyca/cryptography) and may differ slightly from the rendered sites. OpenAI claims come from search-result summaries and are not verified against the full pages.

## Where should keys live (server-side vs browser storage)?

### Takeaway
Keep provider keys on the server only. The browser should send a key once over HTTPS and never receive it back. OWASP explicitly rules out localStorage/sessionStorage for credentials, and OpenAI and Anthropic both say keys must stay out of browsers.

### Cited Findings
- OWASP Session Management: "Do not store authentication tokens, session IDs, JWTs, refresh tokens, or any credential" in localStorage or sessionStorage, because these APIs are "accessible to any JavaScript executing in the origin," so one XSS flaw can expose every token. **[Requirement-strength "do not"]** — [OWASP Session Management Cheat Sheet](https://github.com/OWASP/CheatSheetSeries/blob/master/cheatsheets/Session_Management_Cheat_Sheet.md)
- OWASP HTML5 Security: localStorage is shared across the origin and must not be treated as trusted; XSS can both read and write these objects; sensitive data there is "a serious risk when authorization is wrongly assumed." Recommends sessionStorage over localStorage when persistence is not needed, but sessionStorage is still JavaScript-readable. **[Recommendation]** — [OWASP HTML5 Security Cheat Sheet](https://github.com/OWASP/CheatSheetSeries/blob/master/cheatsheets/HTML5_Security_Cheat_Sheet.md)
- OWASP Session Management on cookies: HttpOnly is "mandatory" against session-ID theft via XSS, but "The `HttpOnly` cookie only protects the confidentiality of the cookie." An XSS-plus-CSRF attack can still use the cookie because browsers attach it automatically. **[Requirement for session cookies]** — [OWASP Session Management](https://github.com/OWASP/CheatSheetSeries/blob/master/cheatsheets/Session_Management_Cheat_Sheet.md)
- OWASP recommends `HttpOnly; Secure; SameSite=Strict` cookies or a Backend-for-Frontend pattern for auth tokens. **[Recommendation]** — same source.
- OpenAI: keys should stay server-side and "never placed in browsers, mobile apps, or repositories"; "Anyone who extracts a client-side key can make requests on your account." **[Provider recommendation]** — [OpenAI API key safety article](https://help.openai.com/en/articles/5112595-best-practices-for-api-key-safety) (search summary only; page not fetched)
- Anthropic: "Never include it directly in your code or configuration files." **[Provider recommendation]** — [Anthropic API key best practices](https://support.claude.com/en/articles/9767949-api-key-best-practices-keeping-your-keys-safe-and-secure)
- OWASP Secrets Management: "Never expose secrets through external access mechanisms shared among deployments or orchestrators" and "Secrets should never be retrievable by everyone and everything." **[Requirement-strength "never"]** — [OWASP Secrets Management](https://github.com/OWASP/CheatSheetSeries/blob/master/cheatsheets/Secrets_Management_Cheat_Sheet.md)

### Inferences
- A provider key is a long-lived account credential, so it falls under the "do not store in browser storage" rule even though it is not a session token. Do not put it in localStorage, sessionStorage, or any cookie the browser can read back.
- A provider key stored in an HttpOnly cookie is still sent with every request and is exposed to XSS-driven requests. Store keys server-side and have the browser hold only a session cookie.
- Any frontend code that calls providers directly (as opposed to through your backend) would expose the key in the browser. Route all provider calls through the server.

### Gaps
- No source covers Web Crypto or in-browser encryption as a way to protect keys in localStorage. The OWASP guidance above argues against that pattern, but I did not find a source that addresses it directly.

## How should keys be stored on disk (encryption at rest)?

### Takeaway
Encrypt with AES-GCM (or Fernet, which wraps AES with HMAC) using a maintained library, and keep the master key separate from the ciphertext. OWASP says to use GCM first, never store keys next to the data they protect, and never roll your own algorithm.

### Cited Findings
- OWASP Cryptographic Storage: AES with a key of at least 128 bits, "ideally 256 bits." **[Recommendation]** — [OWASP Cryptographic Storage](https://github.com/OWASP/CheatSheetSeries/blob/master/cheatsheets/Cryptographic_Storage_Cheat_Sheet.md)
- OWASP: "Where available, authenticated modes should always be used." GCM and CCM "should be used as a first preference." **[Requirement-strength "should always" for authenticated modes]** — same source.
- OWASP: "Custom algorithms: Don't do this." Nonce/IV handling "should be handled automatically by the library." **[Requirement-strength]** — same source.
- OWASP: "You should not store keys next to the secrets they encrypt." **[Requirement-strength "should not"]** — [OWASP Secrets Management §7.3](https://github.com/OWASP/CheatSheetSeries/blob/master/cheatsheets/Secrets_Management_Cheat_Sheet.md)
- OWASP Key Management: "encryption keys should be stored in a separate location from encrypted data"; keys "should themselves be stored in an encrypted form"; "the KEK must be stored separately from the DEK." The KEK "should also be at least as strong as the DEK." **[Requirement-strength "must" for KEK/DEK separation]** — [OWASP Key Management](https://github.com/OWASP/CheatSheetSeries/blob/master/cheatsheets/Key_Management_Cheat_Sheet.md)
- OWASP Key Management: "Keys should never be stored in plaintext format." Keys "should never be committed to source code repositories or embedded in build artifacts." **[Requirement-strength "never"]** — same source.
- OWASP Cryptographic Storage, when no managed key store exists: "Do not hard-code keys," "Do not check keys into version control," "Protect the configuration files containing the keys with restrictive permissions," and "Avoid storing keys in environment variables." **[Recommendation, with "avoid"]** — [OWASP Cryptographic Storage](https://github.com/OWASP/CheatSheetSeries/blob/master/cheatsheets/Cryptographic_Storage_Cheat_Sheet.md)
- OWASP Secrets Management: "Using environment variables is therefore not recommended" (in a container context). **[Recommendation]** — [OWASP Secrets Management §5.1](https://github.com/OWASP/CheatSheetSeries/blob/master/cheatsheets/Secrets_Management_Cheat_Sheet.md)
- OWASP Cryptographic Storage: "the secure storage mechanisms provided by the operating system, framework or cloud service provider should be used." **[Recommendation]** — same source.
- Python `cryptography`, Fernet: AES-128-CBC with PKCS7 padding plus HMAC-SHA256; IVs from a CSPRNG; each token contains a plaintext creation timestamp; `decrypt(token, ttl=...)` rejects old tokens; `generate_key()` produces a URL-safe base64 32-byte key. Documented as "generally unsuitable for very large files." Warning: "if anyone else gains access to it, they'll be able to decrypt all of your messages." — [pyca/cryptography Fernet docs](https://github.com/pyca/cryptography/blob/main/docs/fernet.rst) (rendered site not fetched)
- Fernet MultiFernet: encrypts with the first key in the list and tries each key on decrypt; `rotate(msg)` re-encrypts under the primary key and keeps the original timestamp. Suggested flow: add a new key at the front, rotate tokens, then retire old keys. — same source
- Python `cryptography`, AESGCM: key sizes 128, 192, or 256 bits. The docs say "NEVER REUSE A NONCE" with a key; reuse "compromises the security of any message with that nonce and key pair." 96-bit nonces are recommended. `associated_data` is authenticated but not encrypted, must match on decrypt, and failures raise `InvalidTag`. — [pyca/cryptography AEAD docs](https://github.com/pyca/cryptography/blob/main/docs/hazmat/primitives/aead.rst) (rendered site not fetched)
- OWASP Key Management: for AES-GCM with random 96-bit IVs, cap each key at "2^32" encryption operations across all devices sharing it. **[Recommendation]** — [OWASP Key Management](https://github.com/OWASP/CheatSheetSeries/blob/master/cheatsheets/Key_Management_Cheat_Sheet.md)

### Master key options (env var vs OS keyring)
- **Environment variable**: OWASP calls environment variables "not recommended" for secrets and says to avoid them for keys when no managed store exists. Still, OpenAI recommends loading keys from environment variables (`OPENAI_API_KEY`). The two sources conflict for provider keys, but the OWASP advice is about master keys and secrets in general. **[Conflict noted]**
- **OS keyring** (`keyring` Python library): backends are macOS Keychain, Freedesktop Secret Service (GNOME, needs `secretstorage`), KWallet (KDE, needs `dbus-python`), and Windows Credential Locker. The library auto-selects a backend. README caveats: on macOS, "Any Python script or application can access secrets created by keyring from that same Python executable"; the Secret Service, KWallet, and Windows backends are each marked "No analysis has been performed"; and the list of security concerns "is not exhaustive." It also says Windows and macOS keyring "is usually degenerate, meaning it will return empty values to the caller" — the wording is ambiguous and needs verification before relying on it. Failure modes are `KeyringError`, `InitError`, `PasswordSetError`, and `PasswordDeleteError`; `get_password` returns None when nothing is stored. — [jaraco/keyring README](https://github.com/jaraco/keyring)
- OWASP Key Management: keys should "never" be used by standard application code directly; use key management libraries. **[Requirement-strength]** — [OWASP Key Management](https://github.com/OWASP/CheatSheetSeries/blob/master/cheatsheets/Key_Management_Cheat_Sheet.md)

### Key loss
- OWASP Key Management: "Data that has been encrypted with lost cryptographic keys will never be recovered." Backup and escrow are the main safeguards. **[Fact from source]**
- OWASP: "Build in secure key backup," and "Encrypt the key store database with a validated module." **[Recommendation]**
- OWASP: "Never escrow keys used for performing digital signatures." Encryption keys may be escrowed. **[Requirement-strength "never" for signing keys]**

### Inferences
- Use AES-GCM directly (via `AESGCM`) or Fernet (which handles nonces and integrity internally). For a single-user app, Fernet is the lower-risk option because its nonce handling is done for you. The trade-off is that Fernet requires holding the whole message in memory, which is fine for short key strings.
- Store the master key outside the data directory (for example in the OS keyring, a file with restrictive permissions outside the app's data folder, or a secrets manager). If the key file and the encrypted store are backed up together, the encryption protects nothing.
- Because provider keys can be re-issued from the provider console, loss of the master key is recoverable by re-entering keys. Design for that: on `InvalidToken` or `InvalidTag`, prompt for re-entry and do not crash or silently delete. This is a reasonable recovery story, but it does not replace the OWASP backup guidance for other data.
- Store a key ID or version with each ciphertext so the master key can be rotated later (MultiFernet supports this pattern; for AES-GCM, add your own version byte).
- Rotating the master key requires decrypting and re-encrypting stored keys with the new master. OWASP says re-wrapping leaves cryptoperiods unchanged.

### Gaps
- No source compares Fernet and raw AES-GCM for this exact use case. The recommendation above is my synthesis.
- No source addresses whether a keyring can be used safely on a headless Linux server (Secret Service requires a running D-Bus session).

## Session scope: memory-only or persistent?

### Takeaway
No source I read directly compares memory-only and persisted keys for a self-hosted app. Persistence with encryption at rest and scheduled rotation is supported by the provider guidance. Memory-only is simpler but forces re-entry after every restart.

### Cited Findings
- OWASP Secrets Management: "Restarting the application does not itself revoke stolen credentials." Credentials should be revoked when no longer needed. **[Recommendation]** — [OWASP Secrets Management §2.4](https://github.com/OWASP/CheatSheetSeries/blob/master/cheatsheets/Secrets_Management_Cheat_Sheet.md)
- OWASP Secrets Management: "You should regularly rotate secrets so that any stolen credentials will only work for a short time." **[Recommendation]** — same source, §2.7.2.
- OWASP Secrets Management: "User credentials are excluded from regular rotation" and should be rotated on suspicion of compromise. **[Recommendation]** — same source.
- Anthropic: "Regularly rotate your API keys on a consistent schedule (for example, every 90 days)"; "use different API keys for development, testing, and production environments" where possible. **[Provider recommendation]** — [Anthropic API key best practices](https://support.claude.com/en/articles/9767949-api-key-best-practices-keeping-your-keys-safe-and-secure)
- OpenAI: "Rotate immediately if you suspect a leak"; rotation is "create a replacement, update your apps, then revoke the old key." **[Provider recommendation]** — [OpenAI API key safety](https://help.openai.com/en/articles/5112595-best-practices-for-api-key-safety) (search summary only)

### Inferences
- Memory-only: no at-rest exposure, but background or multi-agent jobs cannot run after a restart until the user re-enters keys. Acceptable if the user is always present.
- Persistent (encrypted): supports unattended runs, but the master key must be available on disk or in a keyring, so the at-rest protection depends on that key's protection.
- A hybrid works well: persist encrypted keys, load them into memory on startup, and avoid holding plaintext longer than a request needs. Decrypt per call rather than caching plaintext in long-lived objects.
- Whatever is chosen, add a "last used" and "last verified" field and a manual rotation flow.

### Gaps
- No primary source found on memory-only vs persistent trade-offs. OWASP has a "Handling Secrets in Memory" section (§2.5) that I did not extract in detail; it says not to use immutable strings for secrets in .NET or Java, which does not apply to Python.

## How should the API expose keys?

### Takeaway
Use a write-only input, return only a masked form and metadata, and never echo a key in any response. The strongest precedent is OpenAI's: the full secret is shown once at creation and only a redacted value is available afterward.

### Cited Findings
- OpenAI: "The full secret is shown only at creation. Later project API-key retrieval returns a redacted value, so you can't recover a lost key." **[Provider fact]** — [OpenAI service accounts / managing projects](https://developers.openai.com/api/reference/resources/organization/subresources/projects/subresources/service_accounts/methods/create) (search summary only; page not fetched)
- OpenAI: admins can enforce a maximum API key lifetime; keys can be set to expire. **[Provider fact]** — same summary.
- OpenAI: "Alerts alone don't stop traffic"; hard spend caps are the control that limits cost from a leaked key. **[Provider guidance]** — same summary.
- OpenAI: an exposed key found on the public internet or inside a published app "is disabled immediately." **[Provider fact]** — same summary.
- Anthropic: "Implement usage and spend limits in your account settings" (custom rate-limit orgs); standard orgs should "Enable and configure auto-reload settings." These "act as a safeguard against unexpected usage due to leaked keys or errant scripts." **[Provider recommendation]** — [Anthropic API key best practices](https://support.claude.com/en/articles/9767949-api-key-best-practices-keeping-your-keys-safe-and-secure)
- Anthropic: API keys are tied to the Workspace they were created in and cannot be moved; workspaces have their own spend and rate limits. **[Provider fact; the source is a 2024 announcement and help-center page, may be dated]** — [Anthropic Workspaces help article](https://support.claude.com/en/articles/9796807-creating-and-managing-workspaces-in-the-claude-console) (search summary only)
- OWASP Secrets Management: "Applications should verify that the secret is still active before trusting it." **[Recommendation]** — [OWASP Secrets Management §2.7.4](https://github.com/OWASP/CheatSheetSeries/blob/master/cheatsheets/Secrets_Management_Cheat_Sheet.md)
- OWASP Secrets Management: "Continually monitor who/what, from which IP, and what methodology accesses the secret." **[Recommendation]** — same source, §6.4.

### Inferences
- Suggested response shape for a stored key: `{provider, label, last4, created_at, last_verified_at, status}`. Never include the key, a prefix that reveals more than the provider's own display format does, or a reversible hash.
- Use a write-only form field (`type="password"`, `autocomplete="off"`, no pre-fill). After submit, clear the field and do not keep the value in the DOM, state, or history.
- A "test key" action should call a cheap provider endpoint and return only pass/fail plus a generic provider error. Do not echo the key in the error.
- Rotation: accept a new key, verify it, switch the active key, then delete the old one from the app and tell the user to revoke it at the provider. Deletion in the app alone does not revoke the key at the provider.
- Set spend limits at the provider as well as in the app. The app cannot enforce the provider's billing.
- Multi-tenant: each user's keys need access checks on every read, and the server should never return another user's key metadata. Per-user encryption keys (envelope encryption with a KMS) are the usual pattern, and the single master-key approach above becomes a shared blast radius.

### Gaps
- No OWASP or other primary source I read covers UI masking, write-only inputs, or API response shape for secrets. The points above are inferences from the OpenAI precedent and the OWASP storage rules.
- The OpenAI "auto-disable on public exposure" claim comes from a search summary; I did not confirm it on a primary page.

## What do provider docs say about key handling?

### Takeaway
Both providers say: keep keys server-side, load them from environment variables or a secret manager, never share keys, rotate on a schedule, and set spend limits. OpenAI is more explicit that keys must stay out of browsers. Anthropic's article does not say client-side use is prohibited, only that keys should not be placed in code or config files.

### Cited Findings (Anthropic, fetched)
- "Never include it directly in your code or configuration files." **[Recommendation]**
- "In cloud environments, prefer encypted secret storage instead of dotenv files." **[Recommendation]**
- "If someone needs access to the Claude API, they should obtain their own key." **[Recommendation]**
- "Don't include your API key in public discussions, emails, or support tickets, even between you and Anthropic." **[Recommendation]**
- "Regularly rotate your API keys on a consistent schedule (for example, every 90 days)." **[Recommendation]**
- "If possible, use different API keys for development, testing, and production environments." **[Recommendation]**
- Source: [Anthropic API key best practices](https://support.claude.com/en/articles/9767949-api-key-best-practices-keeping-your-keys-safe-and-secure) (fetched)

### Cited Findings (OpenAI, search summary only)
- Keys should not be shared; each person gets their own key. Key sharing violates OpenAI's Terms of Use (per the summary). **[Provider recommendation]**
- Keys should stay out of browsers and mobile apps; route calls through a backend. **[Provider recommendation]**
- Keys should stay out of source control, including private repos. **[Provider recommendation]**
- Load keys from environment variables, named `OPENAI_API_KEY`; consider a key management service. **[Provider recommendation]**
- Rotate immediately on suspected leak. **[Provider recommendation]**
- Sources: [OpenAI API key safety](https://help.openai.com/en/articles/5112595-best-practices-for-api-key-safety), [OpenAI RBAC](https://developers.openai.com/api/docs/guides/rbac)

### Inferences
- The provider docs and OWASP agree on the basic rules. They differ on environment variables: OpenAI recommends them, while OWASP discourages them for secrets. For a self-hosted app, the practical resolution is to use env vars only for the master key or a secret-file path, and keep provider keys encrypted at rest.
- Anthropic's "every 90 days" is an example, not a requirement.

### Gaps
- The full OpenAI help article and production best-practices page did not load. The OpenAI points above are from search summaries.
- I did not verify Anthropic's current workspace spend-limit UI or tier specifics. The third-party sources I found on tiers were unverified.

## What do OWASP and secrets-management guidance require?

### Takeaway
OWASP uses strong language ("never," "should not") for storage, logging, and key separation, but it does not give a checklist for API or UI handling. Treat its storage and logging rules as requirements and the rotation and access rules as recommendations.

### Cited Findings
- OWASP Secrets Management: "It is best to use a designated secret management solution in any environment." **[Recommendation]** — [OWASP Secrets Management §4.1](https://github.com/OWASP/CheatSheetSeries/blob/master/cheatsheets/Secrets_Management_Cheat_Sheet.md)
- OWASP Secrets Management: "Never transmit secrets via plaintext." **[Requirement-strength "never"]** — same source §2.8.
- OWASP Secrets Management: "Encrypt backups and put them on secure storage with reduced access rights." **[Recommendation]** — same source §2.9.
- OWASP Secrets Management: "engineers should not have access to all secrets in the secrets management system." **[Recommendation]** — same source §2.3.
- OWASP Secrets Management: "Configure a short lease duration and revoke credentials when they are no longer needed." **[Recommendation]** — same source §2.4.
- OWASP Secrets Management: "preferably select an algorithm that provides encryption and confidentiality at the same time" (§7.1). **[Recommendation]**
- OWASP Secrets Management: "Keys should be randomly generated using a cryptographically secure function" and "should not be based on common words or phrases." **[Requirement-strength "should"]** — [OWASP Cryptographic Storage](https://github.com/OWASP/CheatSheetSeries/blob/master/cheatsheets/Cryptographic_Storage_Cheat_Sheet.md)
- OWASP Key Management: "Keys must be protected on both volatile and persistent memory." **[Requirement-strength "must"]** — [OWASP Key Management](https://github.com/OWASP/CheatSheetSeries/blob/master/cheatsheets/Key_Management_Cheat_Sheet.md)

### Inferences
- The "volatile memory" rule applies to in-memory plaintext keys. Keep plaintext short-lived, avoid logging it, and clear it where the language allows (Python's immutable strings make full clearing impractical; note this as a limit).

### Gaps
- OWASP has no section on API key display or masking. I did not find a primary source covering that.

## How should keys stay out of logs, errors, and provider-call traces?

### Takeaway
OWASP's logging guidance lists access tokens, encryption keys, and authentication passwords as data that should not be logged. For provider-call traces, I did not find a primary source; the rules below are my synthesis.

### Cited Findings
- OWASP Logging: "Never log data unless it is legally sanctioned." Data that "should usually not be recorded directly in the logs" includes "Access tokens," "Authentication passwords," "Encryption keys and other primary secrets," and "Database connection strings." **[Requirement-strength "should usually not"; "Never" for unsanctioned data]** — [OWASP Logging Cheat Sheet](https://github.com/OWASP/CheatSheetSeries/blob/master/cheatsheets/Logging_Cheat_Sheet.md)
- OWASP Logging: data should be "removed, masked, sanitized, hashed, or encrypted"; sanitization also prevents log injection by neutralizing carriage return, line feed, and delimiter characters. **[Recommendation]** — same source.
- OWASP Logging: session identifiers may be replaced with a hashed value if needed to track session events. **[Recommendation]** — same source.
- OWASP Secrets Management: secrets "should never be logged" unless "must implement either an encryption or masking approach." **[Requirement-strength]** — [OWASP Secrets Management §8.3](https://github.com/OWASP/CheatSheetSeries/blob/master/cheatsheets/Secrets_Management_Cheat_Sheet.md)
- OWASP Secrets Management: "Make sure that pipeline output does not leak secrets." **[Recommendation]** — same source §3.1.

### Inferences
- Log provider calls as metadata only: provider, model, endpoint host, status, latency, token counts, request ID. Never log headers or request bodies that may carry the key (for example `Authorization`, `x-api-key`).
- Redact at the logging layer as a second line of defense: a logging filter that masks known key patterns (for example `sk-` prefixes and `sk-ant-` prefixes) and any value that matches a stored key. Do not rely on the filter alone.
- Check how your HTTP client and provider SDK format exceptions and `repr()` output. Some libraries include request headers in error messages. I did not verify this for specific SDKs, so verify it in the code.
- Error responses to the UI should use generic messages ("provider rejected the key") and never include the key or the upstream request.
- Session tracking: keep session IDs out of logs or hash them, per OWASP.
- Any analytics, tracing, or crash-reporting tools must be configured to scrub these fields too. This is a common leak path in LLM apps; I did not find a primary source on it, so treat it as an inference.

### Gaps
- No primary source on LLM provider-call tracing or observability tools and secret redaction. This is the weakest area of the research.

## Summary: requirements vs recommendations

Requirement-strength (from sources, with "never/must/should not" wording):
- Keys must not be in browser localStorage or sessionStorage (OWASP Session Management, "do not").
- Authenticated encryption (GCM) should always be used where available; custom algorithms should not be used (OWASP Cryptographic Storage).
- Keys must not be stored next to the data they encrypt; KEK and DEK must be stored separately (OWASP Secrets Management §7.3; OWASP Key Management).
- Keys must not be stored in plaintext or committed to source control (OWASP Key Management, Cryptographic Storage).
- Secrets must not be logged unless encrypted or masked (OWASP Secrets Management §8.3).
- Data encrypted with lost keys cannot be recovered (OWASP Key Management, a stated fact).
- AES-GCM nonces must never be reused with the same key (Python `cryptography` AEAD docs).

Recommendation-strength (from sources):
- Use a secret manager; rotate regularly; use separate keys per environment; set provider spend limits; avoid env vars for secrets; verify keys are still active.

Inferences (mine, not a source):
- Write-only inputs, masked display with last 4 and metadata, no echo in API or error responses.
- Fernet or AES-GCM with a master key from the OS keyring (preferred on desktops) or a restricted file outside the data directory (preferred on servers).
- Memory-only vs persistent: persist encrypted by default if unattended runs are needed; otherwise memory-only.
- Log provider calls as metadata only; redact at the logging layer.

## Source conflicts and reliability notes
- Environment variables: OWASP discourages them for secrets; OpenAI recommends them for `OPENAI_API_KEY`. Not a true contradiction for this app, but the advice differs.
- OpenAI sources were not fetched in full (DNS failure on help.openai.com and developers.openai.com). Their claims rest on search summaries.
- The keyring README's "usually degenerate ... on Windows and macOS" sentence is ambiguous. Verify on the target OS before relying on it.
- Anthropic workspace spend-limit details come from a 2024 announcement and a help article seen only as a search summary; the tier details were from unverified third-party sites.
- OWASP content was read from GitHub mirrors; check the live site before quoting it in a published document.

## Overall gaps
- No primary source on memory-only vs persistent session trade-offs.
- No primary source on secret masking in API or UI responses.
- No primary source on LLM provider-call tracing and redaction.
- The OpenAI help article and production best-practices page were not fetched in full.
- cryptography.io rendered docs were not fetched; the GitHub `.rst` source was used instead.
