CREATE TABLE IF NOT EXISTS received_messages (
    id BIGSERIAL PRIMARY KEY,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_ip VARCHAR(100),
    message_control_id VARCHAR(100),
    raw_message TEXT NOT NULL,
    ack_message TEXT NOT NULL,
    parsed_json JSONB,
    parse_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_received_messages_received_at
    ON received_messages (received_at DESC);
