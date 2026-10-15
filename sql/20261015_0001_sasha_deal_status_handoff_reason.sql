-- Sasha handoff: store why a deal was handed to a person.
-- Target: the operations schema (DEAL_MANAGER_OPERATIONS_SCHEMA), which already holds
-- sasha_deal_status. Handoff offers reuse v3's sasha_handoff_isas and sasha_handoff_offers
-- unchanged; if they are missing from this schema, apply v3 migrations
-- 20260710_0001_create_sasha_handoff_tables and 20260710_0003_add_handoff_offer_thread_id.
--
-- handoff_reason format: "<category>: <summary for the ISA, up to five sentences>",
-- category one of external_item, group_order, client_upset, unresolved_blocker, repeated_failures.

ALTER TABLE sasha_deal_status
    ADD COLUMN handoff_reason TEXT NULL
        COMMENT 'Why the deal was handed off: <category>: <summary for the ISA>'
        AFTER status;

-- Rollback
-- ALTER TABLE sasha_deal_status DROP COLUMN handoff_reason;
