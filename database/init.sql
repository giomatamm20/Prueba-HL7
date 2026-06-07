CREATE TABLE IF NOT EXISTS patients (
    id BIGSERIAL PRIMARY KEY,
    patient_external_id VARCHAR(100) NOT NULL UNIQUE,
    full_name VARCHAR(255),
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lab_orders (
    id BIGSERIAL PRIMARY KEY,
    order_external_id VARCHAR(100) NOT NULL,
    analyzer_code VARCHAR(100) NOT NULL,
    patient_id BIGINT NOT NULL REFERENCES patients(id),
    panel_code VARCHAR(100),
    panel_name VARCHAR(255),
    status VARCHAR(30),
    last_message_type VARCHAR(30),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (analyzer_code, order_external_id)
);

CREATE TABLE IF NOT EXISTS raw_hl7_messages (
    id BIGSERIAL PRIMARY KEY,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_ip VARCHAR(100),
    analyzer_code VARCHAR(100),
    message_type VARCHAR(30),
    message_control_id VARCHAR(120),
    processing_status VARCHAR(30) NOT NULL,
    raw_message TEXT NOT NULL,
    parsed_json JSONB,
    ack_code VARCHAR(5) NOT NULL,
    ack_message TEXT NOT NULL,
    parse_error TEXT
);

CREATE TABLE IF NOT EXISTS lab_results (
    id BIGSERIAL PRIMARY KEY,
    lab_order_id BIGINT NOT NULL REFERENCES lab_orders(id),
    raw_message_id BIGINT NOT NULL REFERENCES raw_hl7_messages(id),
    test_code VARCHAR(100) NOT NULL,
    value_text VARCHAR(255) NOT NULL,
    value_numeric NUMERIC,
    unit VARCHAR(100),
    reference_range VARCHAR(100),
    abnormal_flag VARCHAR(30),
    result_status VARCHAR(30),
    observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS test_catalog (
    id BIGSERIAL PRIMARY KEY,
    code VARCHAR(100) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    analyzer_code VARCHAR(100) NOT NULL DEFAULT 'ABBOTT_RUBY',
    active BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order INTEGER NOT NULL DEFAULT 100,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO test_catalog (code, name, analyzer_code, sort_order) VALUES
    ('WBC', 'Leucocitos', 'ABBOTT_RUBY', 10),
    ('RBC', 'Eritrocitos', 'ABBOTT_RUBY', 20),
    ('HGB', 'Hemoglobina', 'ABBOTT_RUBY', 30),
    ('HCT', 'Hematocrito', 'ABBOTT_RUBY', 40),
    ('PLT', 'Plaquetas', 'ABBOTT_RUBY', 50)
ON CONFLICT (code) DO NOTHING;

CREATE TABLE IF NOT EXISTS outbound_orders (
    id BIGSERIAL PRIMARY KEY,
    order_external_id VARCHAR(100) NOT NULL,
    analyzer_code VARCHAR(100) NOT NULL,
    patient_external_id VARCHAR(100) NOT NULL,
    patient_name VARCHAR(255),
    sample_id VARCHAR(100) NOT NULL,
    priority VARCHAR(30) NOT NULL DEFAULT 'ROUTINE',
    requested_tests JSONB NOT NULL,
    destination_host VARCHAR(255) NOT NULL,
    destination_port INTEGER NOT NULL,
    raw_message TEXT NOT NULL,
    ack_code VARCHAR(5),
    ack_message TEXT,
    status VARCHAR(30) NOT NULL,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_raw_hl7_received_at ON raw_hl7_messages (received_at DESC);
CREATE INDEX IF NOT EXISTS idx_lab_results_observed_at ON lab_results (observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_lab_orders_updated_at ON lab_orders (updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_outbound_orders_created_at ON outbound_orders (created_at DESC);
