#Whatsapp Task Assistant (Security Purposes)

## Overview

This project is a single-user, security-focused personal task assistant that operates exclusively through WhatsApp message. 

The system is designed as a ** portfolio-grade cybersecurity project**, priotizing:

-strict trust boundaries
-deterministic command execution
-auditable state changes
-defense-in-depth over convience 

The assistant  is intentially **not agentic**: all execultion logic is controlled by a Python backend. AI components (when enabled) are advisory only. 

---

## Core Features
-Whatsapp-based command interface (via Twilio webhook)
-Python FastAPI backend 
-Local SQLite storage
-Incremental numeric task and list IDs
-Deterministic command parsin (no free-form execution)
-Full audit logging of action and state changes 

Supported Commands: 
-'/lists'
-'/newlist <name>'
-'/use <list_id>'
-'/todo <text>'
-'/list'
-'/done <item_id>'

## Architecture

WhatsApp Client -> Twilio WHatsApp API ->
FastAPI Webhook (Internet-facing trust boundary) ->
Command Parser & Allowlist ->
SQLite (Tasks, Lists, Preferences, Audit Log)


