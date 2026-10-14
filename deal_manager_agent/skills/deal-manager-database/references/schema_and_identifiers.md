# Verified database and identifier map

This map describes the application relationships used by the collector. Resolve `<main>` and `<operations>` from the configured schema map before executing any SQL. Table names and joins are application knowledge, not schema configuration. Check the target tables with `describe` when a deployment differs. Historical discovery records are kept outside the runtime skills in the package's test results; they must not supply answers to new messages.

## Tables and joins

| Table | Useful verified fields | Role |
|---|---|---|
| `<main>.deals` | `id`, `client_id`, `account_id`, `manager_id`, `title`, `stage`, `created_at`, `deleted_at`, `order_due_date` | Verify the candidate deal; include closed stages during discovery. |
| `<main>.user` | `id`, `email`, `phone`, `account_id`, `deleted_at` | Candidate identities; join `deals.client_id = user.id`. |
| `<main>.account` | `id`, `active_contact_email`, `active_contact_phone`, `deleted_at` | Additional identity hints; join `deals.account_id = account.id`. A shared account is not a unique deal. |
| `<main>.deal_orders` | `id`, `deal_id`, `order_id`, `created_at`, `deleted_at` | Order-to-deal association; retain all matching rows. |
| `<main>.order` | `id`, `deal_id`, `client_id`, `proof_id`, `created_at`, `deleted_at` | Check the direct order link as well as `deal_orders`. A null direct link does not invalidate a populated mapping table. |
| `<main>.deal_proofs` | `id`, `deal_id`, `proof_id`, `created_at`, `deleted_at` | Proof-to-deal association; a proof may occur on several deals. |
| `<operations>.sasha_conversation_history` | `id`, `deal_id`, `run_id`, `convo_type`, `sender_name`, `message_type`, `direction`, `external_message_id`, `external_thread_id`, `subject`, `body`, `delivery_status`, `message_timestamp`, `created_at`, `deleted_at` | Conversation evidence with direct deal association; a stored label is not guaranteed correct. |

The joins above are application-level relationships observed in schema, source and working queries. They are not a claim that every relationship is enforced by a DB foreign key. The collector verifies final deals against `<main>.deals` and preserves conflicting candidates.

## Namespaces that must remain separate

- Provider message IDs are stored in `sasha_conversation_history.external_message_id`. The collector accepts `message_id` and the compatibility field `sasha_external_message_id` for those values. A numeric history `id` is a different identifier.
- Provider thread IDs are stored in `external_thread_id`; they may span multiple deals. Scope all message lookups by channel and exclude soft-deleted rows.
- `sender_name` holds an email address or SMS sender phone. There are no recipient-address, recipient-phone or mailbox/grant columns. Do not infer those values from the sender.
- Timestamps use UTC. Use `COALESCE(message_timestamp,created_at)` for ordering and chronological filters, with `id` breaking ties.
- `delivery_status=generated` is an unsent draft. It provides draft context, not proof that the client received a message.
- Legacy CRM message, activity and Sasha context/history tables are not conversation sources for this POC.

## Relationship limitations

Stored conversation labels can be wrong or point to deleted deals. One thread or contact can reference multiple deals; scope by channel, inspect all candidates, and verify participants. The absence of a collision in a bounded query does not establish global uniqueness.

A proof can map to several deals. An order's direct deal field can be null while its mapping table is populated. Follow all relevant mappings, retain conflicts and check deletion flags. Never choose a deal solely because it is newest.

## Read patterns

Use `%s` bound parameters with the database helper. The examples below specify query shapes, not concrete production IDs.

```sql
SELECT id,deal_id,message_type,direction,external_thread_id,message_timestamp
FROM `<operations>`.sasha_conversation_history
WHERE external_thread_id=%s AND message_type=%s AND deleted_at IS NULL
  AND COALESCE(message_timestamp,created_at)<%s
ORDER BY COALESCE(message_timestamp,created_at) DESC,id DESC
```

```sql
SELECT id,account_id,email,phone,deleted_at
FROM `<main>`.user WHERE email=%s
```

```sql
SELECT id,title,client_id,account_id,stage,created_at,deleted_at
FROM `<main>`.deals WHERE client_id=%s AND created_at<=%s
ORDER BY id DESC
```

```sql
SELECT id,deal_id,order_id,created_at,deleted_at
FROM `<main>`.deal_orders WHERE order_id=%s
```

```sql
SELECT id,deal_id,proof_id,created_at,deleted_at
FROM `<main>`.deal_proofs WHERE proof_id=%s
```

The helper's schema inventory and describe commands can inspect additional tables as needed. Do not guess columns from names. Schema names and access are determined by the current deployment configuration.
