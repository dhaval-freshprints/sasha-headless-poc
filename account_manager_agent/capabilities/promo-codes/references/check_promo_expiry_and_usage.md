# Check promo expiry and usage

Use these read-only operations for the current deal's promo during client-response work, or to verify an existing deal promo for a permitted follow-up. Creation and replacement remain V4 browser actions. These checks do not redeem a code or guarantee checkout success.

## Connection and scope

The existing command is `python /opt/sasha/database.py`. Use `--role main` so it reads the database configured for this run; never hardcode a schema, host, or credentials. The shared connector runs a read-only transaction with session timezone `+00:00`. Do not use `--env-file`, another environment, or database writes. If configuration, schema, or access is unavailable, report the check as blocked.

Bind codes and IDs through `--params` as a JSON array; never interpolate customer text into SQL. Replace example values with the exact code recorded for this deal, using proper JSON and shell escaping. The SQL itself remains unchanged. A `truncated: true` result is incomplete evidence.

## 1. Recover the deal's current code

Start with the exact code and ID in the supplied notebook. If missing, check this deal's conversation and any recorded creation attempt. Never select an unrelated code merely because it applies to All Accounts.

New codes use the prefix `SASHA<deal_id>-`. If notes were not persisted after creation, look up only that exact numeric deal prefix. Replace the example parameter with `SASHA` plus the supplied deal ID plus `-%`; the final `%` is the only wildcard.

```bash
python /opt/sasha/database.py --role main --limit 20 query "SELECT id, code, status, start_date, end_date, total_usage_count, total_usage_limit, created_at FROM promo_code WHERE code LIKE %s ORDER BY id DESC" --params '["SASHA397012-%"]'
```

This is recovery, not a search for alternative discounts. Verify each candidate with operation 2. Recover a single valid unused code as current. If more than one could be current, the results are truncated, or a previously recorded code is missing, resolve the ambiguity before creating anything. If all recovered codes are conclusively expired or used, a replacement may be created during eligible client-response work. Older codes without this prefix require an association in this deal's notes or conversation; the promo table itself has no deal ID. If context indicates an earlier creation but its identity cannot be recovered, report that gap instead of assuming none exists.

## 2. Read live expiry and usage

Run this for the current code, a recovered candidate, an uncertain save, and a newly created replacement. Run it again immediately before drafting the offer.

```bash
python /opt/sasha/database.py --role main --limit 2 query "SELECT p.id, p.code, p.status, p.type, p.value, p.order_type, p.applies_to_regular_order, p.minimum_amount, p.minimum_quantity, p.total_usage_limit, p.total_usage_count, p.participant_usage_limit, p.start_date AS start_at_utc, p.end_date AS end_at_utc, p.expired_at AS disabled_or_expired_at_utc, p.show_on_corp, p.show_on_corp_campaign, UTC_TIMESTAMP() AS checked_at_utc, (p.start_date > UTC_TIMESTAMP()) AS not_started, (p.end_date IS NOT NULL AND p.end_date <= UTC_TIMESTAMP()) AS expired_by_time, (SELECT COUNT(*) FROM promo_code_user u WHERE u.promo_code = p.id AND u.status = 2 AND u.deleted_at IS NULL) AS completed_redemptions FROM promo_code p WHERE p.code = %s" --params '["CODE_FROM_THIS_DEAL"]'
```

Require exactly one row, no truncation, the expected code, and the expected ID when notes contain one. The code index is not unique; do not silently choose a row if duplicates exist. A zero-row result for an expected saved code is unresolved, not proof it expired or was used.

| Evidence | Decision |
|---|---|
| `status = 1`, `not_started = 0`, `expired_by_time = 0`, `total_usage_count = 0`, and `completed_redemptions = 0` | Candidate for reuse, after confirming one-total-use policy and current order eligibility |
| `expired_by_time = 1` | Expired even if a delayed status update still says active; replace during eligible client-response work |
| `total_usage_count > 0` or `completed_redemptions > 0` | Used; do not offer again; replace during eligible client-response work |
| `total_usage_count >= total_usage_limit` for a positive limit | Exhausted; do not offer |
| `status = 0` without a passed end date or verified use | Inactive/disabled; do not offer or automatically replace, since this alone does not establish why it was disabled |
| `not_started = 1` | Not yet usable; do not call it active now or create a duplicate to bypass its start date |
| Null/unknown status, missing counter/start date, conflicting identity, or query error | Unverified; do not offer or create a speculative replacement |

For this skill's codes, require `total_usage_limit = 1`, no participant-only limit, `order_type = 1` (regular orders), null or empty `applies_to_regular_order` (All Accounts), and both corporate visibility flags equal to 0. A code with different terms is not automatically eligible for replacement; report the policy conflict. `end_at_utc = null` means no stored end timestamp, not expired; do not invent an expiry. An `expired_at` value alone does not distinguish manual disabling from natural expiry. If it conflicts with an active status and future validity, resolve that inconsistency.

`promo_code_user.status` means `0 = removed`, `1 = applied in cart`, `2 = used after order creation`. Only non-deleted status-2 rows count as completed redemptions here. Status 1 is not proof of use. Check usage across all users because the code targets All Accounts and has one total use. Never infer that deletion, removal, or cancellation restores a use; a nonzero total counter still blocks reuse.

## 3. Inspect usage evidence when needed

Use the verified numeric promo ID, not its text code, in `promo_code_user.promo_code`.

```bash
python /opt/sasha/database.py --role main --limit 20 query "SELECT id, order_id, status, created_at, updated_at, deleted_at FROM promo_code_user WHERE promo_code = %s ORDER BY id DESC" --params '[4534]'
```

This can distinguish a cart application from an order redemption. It does not authorize inspecting unrelated customers or placing another order. If the redemption belongs to an already completed request, follow Account Manager's routing before deciding a replacement offer is appropriate.

## Dates and the V4 detail dialog

The V4 creation path converts its entered dates to UTC for storage. Compare stored timestamps against `checked_at_utc`, not the task date, browser clock, list date, or a naive local time. `expired_by_time` and `not_started` perform that comparison in the database.

Always click the promo in V4 to inspect its saved benefit and eligibility. Use database timestamps for expiry and the database status/counters/redemptions for availability. Neither the list nor the detail dialog overrides those facts. The detail dialog can display a date in the browser's timezone alongside a separately formatted time; do not combine them into an assumed timezone.

For a customer-facing deadline, convert the database UTC instant to `America/New_York` with a timezone-aware library, and label it Eastern Time, or convert to the customer's explicitly known timezone. Do not add a fixed offset or add a day to match the UI. For example, `2026-11-06 04:00:00 UTC` is `2026-11-05 23:00:00` in `America/New_York`. Record UTC in the notebook so later runs can recheck the same instant. If a non-date term differs between DB and the opened details, or the verified instant violates the client's requirements, report the unresolved conflict.

## Verified basis

The `prod-v3` schema was inspected on 2026-10-15: `promo_code` contains the status, dates, counters, targeting, and visibility fields above; `promo_code_user` links to the numeric promo ID and contains status, order, and deletion fields. Validate through the supplied environment rather than assuming another deployment has the same schema.

Source checks in the local Fresh Prints checkout:

- `v3/models/promo-code.js`: status 0/1 and regular-order targeting meanings.
- `v3/models/promo-code-user.js`: statuses 0, 1, and 2.
- `v3/routes/services/promo-code-user-svc.js`, `updatePromoCodeUserOnOrderCreation`: status 2 and total counter increment on order creation.
- `v4-web/src/app/shared/utils/promo-code.utils.ts`, `convertDateTimeToUTC`: UTC storage conversion.
- `v4-web/src/app/pages/full-pages/promocodes/promocodes-list/promocodes-list.component.ts`: detail dates use browser-local date formatting.
- Sasha's `sasha_database/connection.py` and `sasha_database/read.py`: UTC session, bounded SELECT queries, read-only transaction.
