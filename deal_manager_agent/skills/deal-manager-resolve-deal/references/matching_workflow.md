# Input, decision checks and fallback queries

Resolve `<main>`, `<operations>` and `<crm>` from the database helper’s `schemas` command or `/workspace/database_schemas.json`. They are placeholders in the SQL below, not literal database names. Quote the configured identifiers with backticks; keep message values in bound parameters.

## Collector input

The following is a synthetic shape, not a real customer message or verified ID:

```json
{
  "channel": "EMAIL",
  "received_at": "2026-10-12T09:00:00Z",
  "sender_email": "client@example.com",
  "recipient_emails": ["business@example.com"],
  "message_id": "provider-message-id",
  "thread_id": "provider-thread-id",
  "subject": "Re: event shirts",
  "body": "Can we use the order we discussed?",
  "deal_ids": [],
  "order_ids": [],
  "proof_ids": [],
  "client_ids": [],
  "account_ids": []
}
```

For SMS use `"channel":"SMS"`, `sender_phone` and `recipient_phone` in international format. The collector uses `sender_phone` to find contacts and both phones for a CRM pair lookup. The agent must verify that retrieved endpoints and direction fit the incoming message. For a ten-digit US/Canadian national number supply `phone_country` as `US` or `CA`; without country context the collector rejects it. Other national formats must first be resolved to a complete international number with evidence.

`channel` and timezone-aware `received_at` are required. Omit unavailable optional IDs instead of sending null/empty strings. At least one usable identity or reference is needed for a meaningful search. An empty evidence collection is not a no-match decision when the input lacks identifiers.

These requirements apply to the automated collector. If a text-only example lacks its timestamp, use bounded manual database queries to investigate the named person and organization; do not invent a timestamp to satisfy the script. Missing chronological metadata is a blocker only when chronology is needed to distinguish candidates.

Optional `sasha_external_message_id` is an actual Sasha history field value, distinct from provider `message_id`. `reply_to_message_id` is a known provider parent ID, not an RFC header unless equivalence is established. `reply_to_sasha_external_message_id` is a known parent history external ID. Preserve mailbox/provider/grant and RFC headers in additional fields for manual verification; the collector cannot validate mailbox namespace against columns that do not exist.

`deal_ids`, `order_ids`, `proof_ids`, `client_ids` and `account_ids` are hints, limited to 20 per field. Attach extraction provenance in an extra `reference_evidence` field with the message quote/URL and whether it came from current or quoted text. The collector deliberately does not regex-extract arbitrary numbers or rank candidates. Current-text interpretation and the final evidence comparison are agent work.

The output includes `all_candidate_ids`, hydrated `candidates`, query-numbered `observations`, `warnings` and `limitations`. Only the agent returns a matching decision. Hydration caps at 30 candidates; identity expansion caps at 20 clients/accounts. Histories are bounded. Any cap/timeout affecting uniqueness requires a narrower follow-up, not selection of the first result.

## How to weigh the evidence

| Evidence | What it establishes | Required check |
|---|---|---|
| Existing Sasha message association | A stored label exists | Verify namespace, contact compatibility, live deal state and conflicts; not ground truth. |
| Exact reply parent + contact | A likely conversational antecedent | Parent belongs to the same channel/participants and the reply fits its subject. |
| Shared email thread | A candidate set | Inspect every deal; check sender/mailbox evidence where available. |
| Explicit order/proof mapping | Entity-related candidates | Typed reference, non-deleted mapping, matching time, participant and content context. |
| Exact sender email/phone | A candidate contact set | Duplicates, country, soft deletion and related deals. |
| Explicit “on behalf of” person + organization | A represented client's candidate deals | Verify the person/organization/deal relationship and uniqueness; no sender record is required. |
| Account affiliation | Additional candidate deals | Another distinctive relationship; account alone is insufficient. |
| Product/event/date/quantity | Contextual corroboration | Distinctive among competing candidates, supported by prior raw conversation. |
| Newest deal, manager, stage | Operational context | Never decisive by itself. |

Do not discard closed deals during discovery. A message can concern a won order or lost opportunity. Soft-deleted deals remain visible as evidence but require review. A record created after receipt cannot justify a match at receipt; current rows are not time-travel truth.

For unresolved multiple-deal evidence, return `needs_review` with all credible candidates and the smallest missing check. Under the conservative system prompt, only `matched` carries a non-null deal ID. If invoking this skill without that system prompt, use the same policy and return at least `status`, `deal_id`, `reason`, `candidate_deals`, `evidence`, `limitations` and `next_check`.

## Focused follow-up queries

Use the database skill's helper with bound parameters and explicit row limits. Record each manual query so the result can be cited in the final decision.

Retrieve the complete body of a specific history row only when the excerpt omitted necessary context:

```sql
SELECT id,deal_id,message_type,direction,subject,body,external_message_id,
       external_thread_id,message_timestamp
FROM `<operations>`.sasha_communication_history WHERE id=%s
```

Read SMS endpoints with a verified client and bounded time interval. Compare the unordered endpoint pair to the incoming sender/recipient, then use type 3/4 for direction:

```sql
SELECT id,relevant_id,deal_id,type,content,happened_at,
       JSON_EXTRACT(activity_details,'$.sender_number') AS sender_number,
       JSON_EXTRACT(activity_details,'$.recipient_number') AS recipient_number
FROM `<main>`.activity
WHERE client_id=%s AND type IN (3,4) AND deleted_at IS NULL
  AND happened_at>=%s AND happened_at<%s
ORDER BY happened_at DESC,id DESC
```

A provider email ID can link to an activity without a populated deal ID:

```sql
SELECT id,relevant_id,deal_id,type,thread_id,happened_at,
       JSON_EXTRACT(activity_details,'$.sender_email') AS sender_email,
       JSON_EXTRACT(activity_details,'$.recipient_email') AS recipient_email
FROM `<main>`.activity
WHERE client_id=%s AND type IN (1,2) AND deleted_at IS NULL
  AND JSON_UNQUOTE(JSON_EXTRACT(activity_details,'$.message_id'))=%s
```

Then look for the string form of that **activity.id** in history `external_message_id`, with channel and chronological constraints. Do not use `relevant_id`, which is the CRM message row ID in these communication activities. If no imported history row exists, use the activity's actual deal association or continue contact/entity comparison.

For an internal message, inspect the dedicated association table:

```sql
SELECT id,deal_id,source_sender,source_subject,source_thread_id,entity_type,
       entity_id,status,created_at
FROM `<operations>`.sasha_internal_emails
WHERE source_thread_id=%s AND created_at<=%s ORDER BY id DESC
```

This column was not verified as indexed; keep the query bounded and stop on timeout. Match source sender and explicit entities before accepting a result. For unrecognized entities such as design IDs, use the database skill to describe the relevant tables and establish the actual joins; do not invent a design→proof→deal relationship.

## Validation rules

A replay must exclude the target association, future conversations and facts extracted from the target. The script filters prior history by timestamp and skips current-message label lookup; it does not reconstruct historical contact ownership or remove duplicated content in differently timestamped copies. Review these limits before treating a replay as an evaluation.

Use independently labeled email and SMS failures for quality evaluation. Include multiple deals on one thread/contact, duplicate contacts, closed/deleted deals, internal/forwarded messages, shared proof mappings, new inquiries and short ambiguous replies. Measure correct automatic matches, incorrect automatic matches and review/no-match coverage separately. Do not count agreement with an existing stored label as independently established correctness.

## CRM-first retrieval

Use the configured CRM schema. For an exact provider ID:

```sql
SELECT id,type,message_id,thread_id,sender_email,recipient_email,
       sender_number,recipient_number,subject,sent_at,deleted_at
FROM `<crm>`.message WHERE message_id=%s AND deleted_at IS NULL
```

The collector now retrieves provider messages/reply parents, prior email threads and SMS phone-pair conversations from this table. It follows `message.id → activity.relevant_id`, checking channel and exact timestamp, and then `activity.id → history.external_message_id` for imported history. Those IDs have different namespaces. Where an activity's deal is null, it expands the activity's client into candidate deals. Participant compatibility still needs independent examination; IDs alone are not enough.

CRM and activity rows can be absent or stale across schemas. Missing bridge rows produce an explicit collector warning. Exact-index SMS queries cover the storage formats seen in the sample and standard digit forms; if a message is missing, inspect a bounded sender/time subset for formatting before concluding no match.

## Proof-number and subject lookup paths

Use both the subject and new body text. A shared art-team sender does not identify a client. Resolve specific proof clues first, then compare client/account deals and any available conversation evidence.

For a supplied proof number, query proof_request.id and deal_proofs.proof_id. If neither exists, query proof_item.id and follow its proof_id to proof_request and deal_proofs. Retain deleted flags and all mappings. Explain the proof-item interpretation in the result; do not silently relabel IDs. Failed queries do not establish absence. The collector implements this fallback for proof_ids, but incomplete queries still require review.

When the number matches a proof_request.id, that is the proof. Do not then query proof_item.id for the same number: proof requests and proof items are different ID namespaces, and a proof item that happens to share the number is not a competing interpretation. Only a competing deal can block a match. A record that leads to no deal mapping, or only to deleted mappings, is not a competing deal.

Fresh Prints team mail (art@, logistics@, sales@ and similar) is often pasted or forwarded without a subject, thread, message ID or received time, and its sender never identifies the client. Those gaps are not a review reason by themselves. When a typed proof, order or deal reference leads to exactly one live, non-deleted deal whose client matches the proof or order, return `matched`.

For a subject such as “Re: Revision Request on Proof : Design for <client name>”, extract the actual design name from the supplied subject and query proof_request.name with a bound value. Compare every matching proof and mapping. Verify the named client's relationship and the final deal; a name alone without a verified relationship is insufficient. The collector accepts proof_names hints with a real received_at timestamp. For the three-field web form, run bounded manual queries instead.

```sql
SELECT pr.id AS proof_id,pr.name,pr.client_id,pr.created_at,
       pr.deleted_at AS proof_deleted_at,dp.id AS mapping_id,dp.deal_id,
       dp.deleted_at AS mapping_deleted_at
FROM `<main>`.proof_request pr
LEFT JOIN `<main>`.deal_proofs dp ON dp.proof_id=pr.id
WHERE pr.name=%s
```

```sql
SELECT pi.id AS proof_item_id,pi.proof_id,pi.deleted_at AS item_deleted_at,
       pr.name,pr.client_id,pr.deleted_at AS proof_deleted_at,
       dp.id AS mapping_id,dp.deal_id,dp.deleted_at AS mapping_deleted_at
FROM `<main>`.proof_item pi
LEFT JOIN `<main>`.proof_request pr ON pr.id=pi.proof_id
LEFT JOIN `<main>`.deal_proofs dp ON dp.proof_id=pi.proof_id
WHERE pi.id=%s
```

If exact names have no hits, use a bounded distinctive-name search and inspect all relevant candidates. Prefer information_schema for unknown columns. Missing sender/history rows do not invalidate a unique proof/client/deal relationship. Conversely, a generic proof-complete body with no typed reference and no distinguishing metadata must remain needs_review. When the incoming timestamp is unknown, a live, non-deleted mapping from a typed reference is still sufficient; abstain only when the current rows show a competing or changed mapping, such as a deleted mapping or a proof moved between deals.
