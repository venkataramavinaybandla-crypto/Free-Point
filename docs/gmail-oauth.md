# Gmail OAuth (read-only)

Free Point requests exactly one Google scope:

```
https://www.googleapis.com/auth/gmail.readonly
```

There is no send, draft, modify, delete, or attachment code path. The consent
flow is browser-session protected:

- `GET /oauth/google/start` creates a random state value and an S256 PKCE
  verifier, stores both in an encrypted HttpOnly cookie, and redirects to
  Google.
- `GET /oauth/google/callback` checks the session, state, and cookie age, then
  exchanges the code with PKCE.
- The refresh token is KMS-envelope encrypted with EncryptionContext
  `user_id` + `gmail` and stored in `user_secrets`. It is never returned to the
  browser, placed in a tool argument, or logged.
- For each Gmail tool call the harness decrypts the refresh token, mints a
  short-lived access token, and sends that token only in the signed internal
  header.

## Environment

Set these on the harness and the Gmail MCP container as appropriate:

```
GOOGLE_CLIENT_ID=<oauth client id>
GOOGLE_CLIENT_SECRET=<oauth client secret>
GOOGLE_REDIRECT_URI=https://<your-host>/oauth/google/callback
GMAIL_MCP_URL=http://gmail-mcp:8080/mcp
```

The redirect URI is taken only from `GOOGLE_REDIRECT_URI`; no domain is
hardcoded. The same exact URI must be registered on the Google OAuth client.
`GOOGLE_CLIENT_SECRET` is needed by the harness for the code exchange and token
refresh; the Gmail MCP server receives only the minted access token.

## Testing-mode limits

While the Google Cloud OAuth consent screen is in **Testing** status:

- Only explicitly added test users can consent (up to 100 users).
- Refresh tokens issued in testing mode expire after **7 days**, so the user
  must reconnect Gmail weekly.
- The app does not need Google verification for these test users, but the
  warning screen is shown.
- Publish the app (or switch to an internal Workspace app) before relying on
  long-lived refresh tokens.

Revoking access at <https://myaccount.google.com/permissions> invalidates the
stored refresh token; the next tool call fails with a reconnect message.
