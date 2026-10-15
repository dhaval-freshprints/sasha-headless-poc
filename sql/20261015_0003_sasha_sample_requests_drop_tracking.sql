-- Remove tracking details from sasha_sample_requests. They stay in the conversation history:
-- the logistics email and Sasha's message to the client.
-- For a table created from an earlier version of 20261015_0002_sasha_sample_requests.sql.
-- New installs from the current 20261015_0002 script do not need this.

ALTER TABLE sasha_sample_requests
    DROP COLUMN tracking_number,
    DROP COLUMN tracking_url,
    DROP COLUMN eta;

-- Rollback
-- ALTER TABLE sasha_sample_requests
--     ADD COLUMN tracking_number VARCHAR(128)  NULL AFTER freshdesk_ticket_url,
--     ADD COLUMN tracking_url    VARCHAR(1000) NULL AFTER tracking_number,
--     ADD COLUMN eta             VARCHAR(64)   NULL AFTER tracking_url;
