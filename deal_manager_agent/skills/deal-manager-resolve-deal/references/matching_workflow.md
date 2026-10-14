# Input, decision checks and fallback queries

Resolve `<main>` and `<operations>` from the database helper’s `schemas` command or `/workspace/database_schemas.json`. They are placeholders in the SQL below, not literal database names. Quote the configured identifiers with backticks; keep message values in bound parameters.

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

For SMS use `"channel":"SMS"` and `sender_phone` in international format; preserve `recipient_phone` when supplied. The collector matches the sender against inbound conversation `sender_name` and main-schema contacts. History has no recipient-phone column, so it cannot verify an endpoint pair. For a ten-digit US/Canadian national number supply `phone_country` as `US` or `CA`; other national formats need a verified country before lookup.

`channel` and timezone-aware `received_at` are required. Omit unavailable optional IDs instead of sending null/empty strings. At least one usable identity or reference is needed for a meaningful search. An empty evidence collection is not a no-match decision when the input lacks identifiers.

These requirements apply to the automated collector. If a text-only example lacks its timestamp, use bounded manual database queries to investigate the named person and organization; do not invent a timestamp to satisfy the script. Missing chronological metadata is a blocker only when chronology is needed to distinguish candidates.

`message_id` is the provider ID stored in `sasha_conversation_history.external_message_id`. Optional `sasha_external_message_id` is a compatibility input for an exact value of that column. Neither is the history row's numeric primary key. `reply_to_message_id` and `reply_to_sasha_external_message_id` identify a parent's external ID. Do not substitute an RFC header unless equivalence is established. Preserve mailbox/provider/grant and recipient metadata for review; history has no such columns.

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
FROM `<operations>`.sasha_conversation_history WHERE id=%s AND deleted_at IS NULL
```

Find prior SMS messages from a known sender, then compare every represented deal:

```sql
SELECT id,deal_id,sender_name,direction,body,external_thread_id,message_timestamp
FROM `<operations>`.sasha_conversation_history
WHERE sender_name=%s AND message_type='SMS' AND direction='INBOUND'
  AND deleted_at IS NULL AND COALESCE(message_timestamp,created_at)<%s
ORDER BY COALESCE(message_timestamp,created_at) DESC,id DESC
```

For internal email, use the same history table with `convo_type='internal_mail'`, channel `EMAIL`, and a known thread, sender or deal. Verify explicit entities and contact relationships. A shared team mailbox cannot uniquely identify a client.

Provider and reply-parent lookups use `external_message_id=%s` and `message_type=%s` in this table. Do not join through CRM messages or activity IDs. Generated outbound drafts are not delivered messages. Missing rows mean no saved Sasha conversation; continue with verified contact/entity relationships and disclose missing context when it prevents matching.

## Validation rules

A replay must exclude the target association, future conversations and facts extracted from the target. The script filters prior history by timestamp and skips current-message label lookup; it does not reconstruct historical contact ownership or remove duplicated content in differently timestamped copies. Review these limits before treating a replay as an evaluation.

Use independently labeled email and SMS failures for quality evaluation. Include multiple deals on one thread/contact, duplicate contacts, closed/deleted deals, internal/forwarded messages, shared proof mappings, new inquiries and short ambiguous replies. Measure correct automatic matches, incorrect automatic matches and review/no-match coverage separately. Do not count agreement with an existing stored label as independently established correctness.

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
