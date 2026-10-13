# Verified database and identifier map

This map describes the application relationships used by the collector. Resolve `<main>`, `<operations>` and `<crm>` from the configured schema map before executing any SQL. Table names and joins are application knowledge, not schema configuration. Check the target tables with `describe` when a deployment differs. Historical discovery records are kept outside the runtime skills in the package's test results; they must not supply answers to new messages.

## Tables and joins

| Table | Useful verified fields | Role |
|---|---|---|
| `<crm>.message` | `id`, `type`, `message_id`, `thread_id`, `sender_email`, `recipient_email`, `sender_number`, `recipient_number`, `sent_at`, `deleted_at` | Original CRM envelope/body. `type=1` is email and `type=2` SMS per the inspected message model; there is no `deal_id` column. |
| `<main>.deals` | `id`, `client_id`, `account_id`, `manager_id`, `title`, `stage`, `created_at`, `deleted_at`, `order_due_date` | Verify the candidate deal; include closed stages during discovery. |
| `<main>.user` | `id`, `email`, `phone`, `account_id`, `deleted_at` | Candidate identities; join `deals.client_id = user.id`. |
| `<main>.account` | `id`, `active_contact_email`, `active_contact_phone`, `deleted_at` | Additional identity hints; join `deals.account_id = account.id`. A shared account is not a unique deal. |
| `<main>.activity` | `id`, `relevant_id`, `deal_id`, `client_id`, `account_id`, `type`, `thread_id`, `activity_details`, `content`, `email_body_plain`, `happened_at`, `deleted_at` | Scoped correspondence and entity links. `relevant_id` depends on activity type. |
| `<main>.deal_orders` | `id`, `deal_id`, `order_id`, `created_at`, `deleted_at` | Order-to-deal association; retain all matching rows. |
| `<main>.order` | `id`, `deal_id`, `client_id`, `proof_id`, `created_at`, `deleted_at` | Check the direct order link as well as `deal_orders`. A null direct link does not invalidate a populated mapping table. |
| `<main>.deal_proofs` | `id`, `deal_id`, `proof_id`, `created_at`, `deleted_at` | Proof-to-deal association; a proof may occur on several deals. |
| `<operations>.sasha_communication_history` | `id`, `deal_id`, `sasha_deal_context_id`, `message_type`, `direction`, `external_message_id`, `external_thread_id`, `subject`, `body`, `message_timestamp` | Conversation evidence, not a guaranteed correct label. |
| `<operations>.sasha_deal_context` | `id`, `deal_id`, `client_id`, `account_id`, `sasha_status`, `meta`, `extracted_client_facts`, `created_at`, `updated_at` | Sasha-side contact/deal relationships. Model-extracted facts are secondary evidence; corroborate with raw history. |
| `<operations>.sasha_internal_emails` | `id`, `deal_id`, `source_sender`, `source_subject`, `source_thread_id`, `entity_type`, `entity_id`, `created_at`, `status` | Additional internal-message associations; verify entity types and participant context. |

The joins above are application-level relationships observed in schema, source and working queries. They are not a claim that every relationship is enforced by a DB foreign key. The collector verifies final deals against `<main>.deals` and preserves conflicting candidates.

## Namespaces that must remain separate

The inspected local `v3-crons` checkout was at commit `bed879798b2ee218b168fbf1f5f58d921bdbb158`. Source inspection establishes local code behavior, not which revision is deployed.

- In [crm-email.js:306](/Users/dhaval/Desktop/fresh_prints/code/v3-crons/cron/services/crm-email/crm-email.js:306), email activities set `relevant_id` to the CRM message row ID; provider message ID goes in `activity_details.message_id`; thread ID goes in `activity.thread_id`. Types 1/2 mean outbound/inbound email.
- In [true-dialog.js:744](/Users/dhaval/Desktop/fresh_prints/code/v3-crons/cron/true-dialog.js:744), types 3/4 mean outbound/inbound SMS. Endpoint numbers go in `activity_details.sender_number` and `.recipient_number`. `relevant_id` is the CRM message row ID, not the deal ID.
- In the inspected Sasha pre-prod [initialisation/helpers.py:335](/Users/dhaval/Desktop/fresh_prints/sasha_code/sasha_pre_prod/sasha-pre-prod/src/core/workflow/nodes/initialisation/helpers.py:335), imported history sets `external_message_id = str(activity.id)`.
- In [initialisation/helpers.py:405](/Users/dhaval/Desktop/fresh_prints/sasha_code/sasha_pre_prod/sasha-pre-prod/src/core/workflow/nodes/initialisation/helpers.py:405), newly saved inbound history generates `uuid.uuid4().hex`. Therefore a provider ID must not be blindly looked up as a Sasha external ID. The collector uses separate fields for these namespaces.
- The same code writes the inbound timestamp using `datetime.now(timezone.utc)` at line 426. The database helper sets its session timezone to UTC and converts timezone-aware input to UTC. Investigate historical rows with unknown producer/time semantics rather than assuming exact event-time equivalence.

## Relationship limitations

Stored conversation labels can be wrong or point to deleted deals. One thread or contact can reference multiple deals; scope by channel, inspect all candidates, and verify participants. The absence of a collision in a bounded query does not establish global uniqueness.

A proof can map to several deals. An order's direct deal field can be null while its mapping table is populated. Follow all relevant mappings, retain conflicts and check deletion flags. Never choose a deal solely because it is newest.

## Read patterns

Use `%s` bound parameters with the database helper. The examples below specify query shapes, not concrete production IDs.

```sql
SELECT id,deal_id,message_type,direction,external_thread_id,message_timestamp
FROM `<operations>`.sasha_communication_history
WHERE external_thread_id=%s AND message_type=%s AND message_timestamp<%s
ORDER BY message_timestamp DESC,id DESC
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

## Verified CRM bridge

`<crm>.message` columns and indexes were inspected with the new login. Provider `message_id` has a unique index; thread/email/phone/sent-time indexes include `deleted_at`. The message model documents CRM type 1=email and 2=SMS ([message.js:9](/Users/dhaval/Desktop/fresh_prints/code/v3-crons/models/message.js:9)). The message table does not contain `deal_id`.

An activity can have a null deal ID even when its CRM link is valid. Continue through its client and imported history. A CRM message can lack a corresponding activity; missing bridge rows do not prove that no deal exists.

```sql
SELECT a.id,a.relevant_id,a.deal_id,a.client_id,a.type,a.happened_at,
       m.message_id,m.type AS crm_type,m.sent_at
FROM `<crm>`.message m JOIN `<main>`.activity a ON a.relevant_id=m.id
WHERE m.message_id=%s AND m.deleted_at IS NULL AND a.deleted_at IS NULL
  AND ((m.type=1 AND a.type IN (1,2)) OR (m.type=2 AND a.type IN (3,4)))
```

Verify participants and time as well as the join. A copied or stale dataset can yield missing or conflicting records. The collector requires exact matching timestamp/channel for this bridge, so a timestamp discrepancy may require manual investigation; it is never an automatic no-match.
