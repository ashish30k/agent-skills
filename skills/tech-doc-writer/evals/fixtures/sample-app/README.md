# sample-app

Small mobile-banking session module used as an eval fixture.

## Behavior (as documented)

- Access tokens are refreshed 5 minutes before they expire.
- Tokens are encrypted at rest with AES-256.
- A failed refresh retries up to 3 times before the user is logged out.
