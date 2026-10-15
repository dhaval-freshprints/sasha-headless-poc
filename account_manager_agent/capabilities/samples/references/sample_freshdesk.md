# Sample Freshdesk tool (agentic)

Use this when the client asks about sample shipping, tracking, or a change to an **already placed** sample that has a ticket. The background sync may be stale; refresh before you tell the client what shipped.

The fixed command is `python /opt/sasha/sample_freshdesk.py`. It only works for sample rows on **this run's deal** and only when `FRESHDESK_DOMAIN` and `FRESHDESK_API_KEY` are configured. Output is JSON on stdout.

## Commands

```bash
# Ticket status and recent logistics thread for sample id 1
python /opt/sasha/sample_freshdesk.py show 1

# Pull Freshdesk thread and advance stored status (shipped / delivered / cancelled) when the thread shows it
python /opt/sasha/sample_freshdesk.py sync 1

# Public reply on the sample ticket (logistics sees this on the thread)
python /opt/sasha/sample_freshdesk.py reply 1 --body "Client confirmed Purple White size S. Please ship blank only."

# Update garment fields on the ticket, post a public reply, and patch the sample row in the database
python /opt/sasha/sample_freshdesk.py amend 1 --color "Purple White" --size S --body "Client changed color from Royal Blue mto to Purple White."
```

Use `reply` for coordination that does not change style, color, or size. Use `amend` when the client confirmed a different blank garment detail and logistics must fulfill the new spec.

## Client-facing rules

- Never mention Freshdesk, tickets, or this command to the client.
- After `sync`, use `recent_thread` and the sample `status` in context before saying whether tracking exists.
- If the sample cannot be changed (delivered, cancelled, or no ticket), say what you are checking and note it; do not invent tracking.
