-- Sasha samples: one row per free blank sample (one style, color and size) placed for a deal.
-- Target: the operations schema (DEAL_MANAGER_OPERATIONS_SCHEMA), next to sasha_deal_status.
-- Only what Sasha needs later is kept here; shipping address and contact details live on the
-- Freshdesk ticket, and the product page and price follow from the style code.

CREATE TABLE IF NOT EXISTS sasha_sample_requests (
    id                   INT           NOT NULL AUTO_INCREMENT,
    deal_id              INT           NOT NULL,
    run_id               VARCHAR(64)   NULL     COMMENT 'Account Manager run that placed the sample',
    status               ENUM('placed', 'shipped', 'delivered', 'cancelled') NOT NULL DEFAULT 'placed',
    style_code           VARCHAR(64)   NOT NULL,
    color                VARCHAR(128)  NOT NULL,
    size                 VARCHAR(32)   NOT NULL,
    freshdesk_ticket_url VARCHAR(500)  NULL     COMMENT 'NULL when Freshdesk is not configured',
    tracking_number      VARCHAR(128)  NULL,
    tracking_url         VARCHAR(1000) NULL,
    eta                  VARCHAR(64)   NULL,
    shipped_at           DATETIME      NULL,
    delivered_at         DATETIME      NULL,
    cancelled_at         DATETIME      NULL,
    cancel_reason        VARCHAR(255)  NULL,
    created_at           DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'When the sample was placed',
    updated_at           DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at           DATETIME      NULL,
    PRIMARY KEY (id),
    KEY ix_sasha_sample_requests_deal_id (deal_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Free blank samples Sasha placed for a deal';

-- Rollback
-- DROP TABLE IF EXISTS sasha_sample_requests;
