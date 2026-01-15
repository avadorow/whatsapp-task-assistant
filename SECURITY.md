# Security and Threat Model

## Purpose

This document defines the security of the WhatsAPP Task Assitant, including threat model assumptions, trust boundaries, implemented controls, and known risks. 

---

## Scope 

### In Scope 
-Twilio -> FastAPI webhook ingestion
-Command parsing and execution (only commands in the allowed list)
-Local persistence (SQLite) 
-Audit logging (messages, actions, state changes)
-Rate limiting and designated senders
-Secrets handling via environment variables
-Optional advisoryy AI integration (Ollama) with strict isolation
-Optional Google Calendar writes with minimal OAuth scope

### Out of Scope
-Multi-User support
-WhatsApp client compromise or device theft
-SIM swap attacks and telecom account compromise
-Host OS compromise / malware
-Cloud infrastructure security (WAF, DDoS migation, etc)
-WhatsApp Web Scraping / browser automation

---

## Trust Boundaries
1. **Internet-facing webhook boundary**
All incoming HTTP requests are treated as untrusted until verified

2. **Execution authority boundary**
Only Python backend executes actions. No other component may mutate state.

3. **AI Boundary (Ollama)**
The LLM is treated as an untrusted advisory engine. It does not have access to the follwoing:
-no access to secrets
-no direct DB access
-no direct API access 
-no ability to execute commands

---

## Assets
-Task and list data (SQLite)
-Audit log(SQLite)
-Twilio AUth Token(env)
-Google OAuth tokens (env or local secure file )
-Allowed sneder list (env) 

---
## Threat Model (STRIDE)

### Spoofing
**Threat:** Attacker sends forged webhook requests to execute commands.
**Mitigations**
-Twilio webhook signature verification (planned) 
-Sender allowlisting (implemented)
-Rate limiting (implemented)

### Tampering
**Threat:** Malicious payloads attempt to modify tasks/lists outside intended commands
**Mitigations:**
-Strict command parsing(implemented)
-Command allowlisting (implemented)
-Deterministic argument validation (implemented)
-DB constraints and transactions (implemented)

### Repudiation
**Threat:** 
User disputes actions or changes
**Mitigations:**
-Append-only audit logging for:
    -message reciept
    -command execution
    -state changes
    -external API writes (planned)

### Information Disclosure
**Threat:**
Secrets leak via logs, prompts, repo commits, or error messages
**Mitigations:**
-Secrets in envirnment variables only (implemented)
-'.env' excluded from Git (implemented)
-Never include secrets in AI prompts (planned enforcement)
-Avoid logging raw message bodies
    -log only metadata (partial)

### Denial of Service
**Threat:**
Message spam triggers resource exhaustion (CPU,DB writes, LLM calls)
**Mitigations:**
-Per-sender rate limiting (implemented) 
-Tighten rate linits for expensive routes 
-Prompt token caps for LLM calls (planned0
-Timeouts on external calls (planned))

### Elevation of Privilege
**Threat:**
Prompt injection convinces the system to execute unauthorized commands and actions
**Mitigations:**
-LLM cannot execute only advise
-Command allowlisting prevents arbitrary execution 
-No free-form execution surface (implemented)

---
## Security Controls Summary

### Implemented
- Sender allowlist (`ALLOWED_SENDERS`)
- Strict command allowlist and parsing
- Rate limiting per sender
- SQLite persistence with constrained schema
- Audit logging (messages and actions)
- Secrets excluded from Git (`.gitignore`)

### Planned (Next Hardening Steps)
- Twilio signature verification (`X-Twilio-Signature`)
- Replay protection via Twilio Message SID tracking
- Hardening audit log (hash-chaining / integrity checks)
- Separate “System output” vs “LLM suggestion” labeling
- Minimal-scope Google Calendar OAuth (`calendar.events`)

---

## Audit Logging
Audit events are written to the `audit_log` table with:
- timestamp
- sender
- event type
- JSON metadata

Design goal: reconstruct all state changes and external writes from the audit log.

---

## Known Risks
- If the host machine is compromised, local DB and tokens can be stolen.
- Sender allowlisting does not prevent spoofed HTTP requests without signature verification.
- SQLite does not provide built-in encryption at rest.

---

## Future Improvements
- Encrypt SQLite at rest (SQLCipher)
- Integrity protection for audit logs (hash chaining)
- Persistent rate limiting (survives restart)
- Continuous security testing (fuzz command parser, webhook auth tests)

---

## Security Testing Checklist
- [ ] Reject requests with invalid Twilio signature
- [ ] Reject requests from non-allowlisted senders
- [ ] Ensure unknown commands are rejected
- [ ] Verify rate limiting triggers
- [ ] Confirm audit log entries for all state changes
- [ ] Ensure `.env` never appears in git history