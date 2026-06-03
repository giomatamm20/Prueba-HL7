<?php

namespace App\Models;

use PDO;

class CrmStore
{
    private PDO $pdo;

    public function __construct(?PDO $pdo = null)
    {
        $this->pdo = $pdo ?: $this->connect();
    }

    public function initialize(): void
    {
        $schemaFile = dirname(__DIR__, 4) . DIRECTORY_SEPARATOR . 'database' . DIRECTORY_SEPARATOR . 'init.sql';
        $schema = file_get_contents($schemaFile);
        if ($schema !== false) {
            $this->pdo->exec($schema);
        }
        $this->pdo->exec(
            "CREATE TABLE IF NOT EXISTS listener_state (
                id SMALLINT PRIMARY KEY DEFAULT 1,
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                last_connection_at TIMESTAMPTZ,
                last_source_ip VARCHAR(100),
                last_ack_code VARCHAR(5),
                last_message_control_id VARCHAR(120)
            )"
        );
        $this->pdo->exec("INSERT INTO listener_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING");
    }

    public function saveReceived(string $rawMessage, string $sourceIp, array $parsed, string $ack): int
    {
        $this->pdo->beginTransaction();
        try {
            $rawId = $this->insertRawMessage($sourceIp, $parsed, $rawMessage, $ack);
            $patientId = $this->upsertPatient($parsed['patient']);
            $orderId = $this->upsertOrder($parsed, $patientId);
            foreach ($parsed['tests'] as $test) {
                $this->insertResult($orderId, $rawId, $test);
            }
            $this->pdo->commit();
            return $rawId;
        } catch (\Throwable $error) {
            $this->pdo->rollBack();
            throw $error;
        }
    }

    public function saveRejected(
        string $rawMessage,
        string $sourceIp,
        ?string $analyzerCode,
        ?string $messageType,
        ?string $messageControlId,
        string $ack,
        string $error
    ): int {
        $statement = $this->pdo->prepare(
            "INSERT INTO raw_hl7_messages (
                source_ip, analyzer_code, message_type, message_control_id,
                processing_status, raw_message, ack_code, ack_message, parse_error
            )
            VALUES (:source_ip, :analyzer_code, :message_type, :message_control_id,
                'REJECTED', :raw_message, 'AE', :ack_message, :parse_error)
            RETURNING id"
        );
        $statement->execute([
            'source_ip' => $sourceIp,
            'analyzer_code' => $analyzerCode,
            'message_type' => $messageType,
            'message_control_id' => $messageControlId,
            'raw_message' => $rawMessage,
            'ack_message' => $ack,
            'parse_error' => $error,
        ]);
        return (int) $statement->fetchColumn();
    }

    public function saveListenerState(string $sourceIp, string $ackCode, ?string $messageControlId): void
    {
        $statement = $this->pdo->prepare(
            "UPDATE listener_state
             SET last_connection_at = NOW(),
                 last_source_ip = :source_ip,
                 last_ack_code = :ack_code,
                 last_message_control_id = :message_control_id
             WHERE id = 1"
        );
        $statement->execute([
            'source_ip' => $sourceIp,
            'ack_code' => $ackCode,
            'message_control_id' => $messageControlId,
        ]);
    }

    public function listenerSnapshot(): array
    {
        $row = $this->row(
            "SELECT started_at, last_connection_at, last_source_ip, last_ack_code, last_message_control_id
             FROM listener_state WHERE id = 1"
        ) ?: [];
        return [
            'startedAt' => $row['started_at'] ?? null,
            'lastConnectionAt' => $row['last_connection_at'] ?? null,
            'lastSourceIp' => $row['last_source_ip'] ?? null,
            'lastAckCode' => $row['last_ack_code'] ?? null,
            'lastMessageControlId' => $row['last_message_control_id'] ?? null,
        ];
    }

    public function statusSummary(): array
    {
        return $this->row(
            "SELECT
                COUNT(*) AS message_count,
                COUNT(*) FILTER (WHERE processing_status = 'PROCESSED') AS processed_count,
                COUNT(*) FILTER (WHERE processing_status = 'REJECTED') AS rejected_count,
                MAX(received_at) AS last_received_at
             FROM raw_hl7_messages"
        );
    }

    public function messages(int $limit = 25): array
    {
        return $this->rows(
            "SELECT id, received_at, source_ip, analyzer_code, message_type,
                   message_control_id, processing_status, raw_message, parsed_json,
                   ack_code, ack_message, parse_error
             FROM raw_hl7_messages
             ORDER BY received_at DESC, id DESC LIMIT :limit",
            ['limit' => $limit]
        );
    }

    public function patients(): array
    {
        return $this->rows(
            "SELECT patient_external_id, full_name, first_seen_at, updated_at
             FROM patients ORDER BY updated_at DESC"
        );
    }

    public function orders(): array
    {
        return $this->rows(
            "SELECT o.order_external_id, o.analyzer_code, p.patient_external_id,
                    o.panel_code, o.panel_name, o.status, o.last_message_type, o.updated_at
             FROM lab_orders o JOIN patients p ON p.id = o.patient_id
             ORDER BY o.updated_at DESC"
        );
    }

    public function results(int $limit = 100): array
    {
        return $this->rows(
            "SELECT r.id, o.order_external_id, p.patient_external_id, r.test_code,
                    r.value_text, r.value_numeric, r.unit, r.reference_range,
                    r.abnormal_flag, r.result_status, r.observed_at
             FROM lab_results r
             JOIN lab_orders o ON o.id = r.lab_order_id
             JOIN patients p ON p.id = o.patient_id
             ORDER BY r.observed_at DESC, r.id DESC LIMIT :limit",
            ['limit' => $limit]
        );
    }

    private function insertRawMessage(string $sourceIp, array $parsed, string $rawMessage, string $ack): int
    {
        $statement = $this->pdo->prepare(
            "INSERT INTO raw_hl7_messages (
                source_ip, analyzer_code, message_type, message_control_id,
                processing_status, raw_message, parsed_json, ack_code, ack_message
            )
            VALUES (:source_ip, :analyzer_code, :message_type, :message_control_id,
                'PROCESSED', :raw_message, CAST(:parsed_json AS jsonb), 'AA', :ack_message)
            RETURNING id"
        );
        $statement->execute([
            'source_ip' => $sourceIp,
            'analyzer_code' => $parsed['analyzerCode'],
            'message_type' => $parsed['messageType'],
            'message_control_id' => $parsed['messageControlId'],
            'raw_message' => $rawMessage,
            'parsed_json' => json_encode($parsed, JSON_THROW_ON_ERROR),
            'ack_message' => $ack,
        ]);
        return (int) $statement->fetchColumn();
    }

    private function upsertPatient(array $patient): int
    {
        $statement = $this->pdo->prepare(
            "INSERT INTO patients (patient_external_id, full_name)
             VALUES (:patient_external_id, :full_name)
             ON CONFLICT (patient_external_id) DO UPDATE SET
                full_name = COALESCE(EXCLUDED.full_name, patients.full_name),
                updated_at = NOW()
             RETURNING id"
        );
        $statement->execute([
            'patient_external_id' => $patient['id'],
            'full_name' => $patient['name'] ?? null,
        ]);
        return (int) $statement->fetchColumn();
    }

    private function upsertOrder(array $parsed, int $patientId): int
    {
        $order = $parsed['order'];
        $statement = $this->pdo->prepare(
            "INSERT INTO lab_orders (
                order_external_id, analyzer_code, patient_id, panel_code,
                panel_name, status, last_message_type
            )
            VALUES (:order_external_id, :analyzer_code, :patient_id, :panel_code,
                :panel_name, :status, :last_message_type)
            ON CONFLICT (analyzer_code, order_external_id) DO UPDATE SET
                patient_id = EXCLUDED.patient_id,
                panel_code = COALESCE(EXCLUDED.panel_code, lab_orders.panel_code),
                panel_name = COALESCE(EXCLUDED.panel_name, lab_orders.panel_name),
                status = EXCLUDED.status,
                last_message_type = EXCLUDED.last_message_type,
                updated_at = NOW()
            RETURNING id"
        );
        $statement->execute([
            'order_external_id' => $order['id'],
            'analyzer_code' => $parsed['analyzerCode'],
            'patient_id' => $patientId,
            'panel_code' => $order['panelCode'] ?? null,
            'panel_name' => $order['panelName'] ?? null,
            'status' => $order['status'] ?? null,
            'last_message_type' => $parsed['messageType'],
        ]);
        return (int) $statement->fetchColumn();
    }

    private function insertResult(int $orderId, int $rawId, array $test): void
    {
        $statement = $this->pdo->prepare(
            "INSERT INTO lab_results (
                lab_order_id, raw_message_id, test_code, value_text,
                value_numeric, unit, reference_range, abnormal_flag, result_status
            )
            VALUES (:lab_order_id, :raw_message_id, :test_code, :value_text,
                :value_numeric, :unit, :reference_range, :abnormal_flag, :result_status)"
        );
        $statement->execute([
            'lab_order_id' => $orderId,
            'raw_message_id' => $rawId,
            'test_code' => $test['code'],
            'value_text' => (string) $test['value'],
            'value_numeric' => is_numeric($test['value']) ? $test['value'] : null,
            'unit' => $test['unit'] ?? null,
            'reference_range' => $test['referenceRange'] ?? null,
            'abnormal_flag' => $test['abnormalFlag'] ?? null,
            'result_status' => $test['status'] ?? null,
        ]);
    }

    private function rows(string $sql, array $parameters = []): array
    {
        $statement = $this->pdo->prepare($sql);
        foreach ($parameters as $name => $value) {
            $statement->bindValue(':' . $name, $value, is_int($value) ? PDO::PARAM_INT : PDO::PARAM_STR);
        }
        $statement->execute();
        return $statement->fetchAll(PDO::FETCH_ASSOC);
    }

    private function row(string $sql): array
    {
        $statement = $this->pdo->query($sql);
        return $statement ? ($statement->fetch(PDO::FETCH_ASSOC) ?: []) : [];
    }

    private function connect(): PDO
    {
        $parts = parse_url((string) env('DATABASE_URL', 'postgresql://hl7host:hl7password@localhost:5432/hl7crm'));
        $host = $parts['host'] ?? 'localhost';
        $port = $parts['port'] ?? 5432;
        $database = ltrim($parts['path'] ?? '/hl7crm', '/');
        $user = $parts['user'] ?? 'hl7host';
        $password = $parts['pass'] ?? 'hl7password';
        $pdo = new PDO("pgsql:host={$host};port={$port};dbname={$database}", $user, $password);
        $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        return $pdo;
    }
}
