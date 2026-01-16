# Security Controls Evidence

## Twilio Signature Verification
**Goal:** Reject forged webhook requests that did not originate from Twilio.

**Expected behavior:**
- Twilio-delivered WhatsApp messages succeed.
- Direct HTTP requests to the public webhook URL fail with 403.
- Audit log records `AUTH_FAIL` with `bad_twilio_signature`.

**How to verify:**
1. Send `/lists` from WhatsApp → receive normal response.
2. Attempt direct POST to the public endpoint (no Twilio signature) → 403.
3. Check `audit_log` for `AUTH_FAIL`.

---

## Replay Protection (MessageSid)
**Goal:** Prevent duplicate processing of retried or replayed webhook deliveries.

**Expected behavior:**
- First-seen `MessageSid` is stored in `message_dedup`.
- Reuse of the same `MessageSid` is rejected with 409.
- Audit log records `REPLAY_REJECTED`.

**How to verify it worked:**
- Confirm `message_dedup` is populated after normal traffic.
