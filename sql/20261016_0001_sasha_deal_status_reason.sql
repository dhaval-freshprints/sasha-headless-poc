-- One reason column on sasha_deal_status: status_reason holds Account Manager's reasoning for the
-- current status (handed off, opted out, paused for a win-back answer, paused at the client's
-- request, ...). It replaces handoff_reason from 20261015_0001; existing handoff reasons are kept.
-- When to look at the deal again lives in v3's sasha_followup_schedule, not here.
-- Target: the operations schema.

ALTER TABLE sasha_deal_status
    CHANGE COLUMN handoff_reason status_reason TEXT NULL COMMENT 'Account Manager reasoning for the current status';

-- If you already added status_reason with an earlier draft (so both columns exist), run this instead:
-- UPDATE sasha_deal_status SET status_reason = handoff_reason
--     WHERE status_reason IS NULL AND handoff_reason IS NOT NULL;
-- ALTER TABLE sasha_deal_status DROP COLUMN handoff_reason;
-- (and, if that draft also added next_check_on:)
-- ALTER TABLE sasha_deal_status DROP KEY ix_sasha_deal_status_next_check_on, DROP COLUMN next_check_on;

-- Rollback
-- ALTER TABLE sasha_deal_status
--     CHANGE COLUMN status_reason handoff_reason TEXT NULL COMMENT 'Why the deal was handed off: <category>: <summary>';
