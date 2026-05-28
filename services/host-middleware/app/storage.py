import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


class CrmStore:
    def __init__(self, database_url: str, schema_file: Path):
        self.database_url = database_url
        self.schema_file = schema_file

    def initialize(self) -> None:
        schema = self.schema_file.read_text(encoding="utf-8")
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(schema)
            connection.commit()

    @staticmethod
    def numeric_value(value: float | str) -> Decimal | None:
        try:
            return Decimal(str(value))
        except (InvalidOperation, ValueError):
            return None

    def save_received(
        self,
        *,
        raw_message: str,
        source_ip: str,
        parsed: dict,
        ack: str,
    ) -> int:
        analyzer_code = parsed["analyzerCode"]
        order = parsed["order"]
        patient = parsed["patient"]
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO raw_hl7_messages (
                        source_ip, analyzer_code, message_type, message_control_id,
                        processing_status, raw_message, parsed_json, ack_code, ack_message
                    )
                    VALUES (%s, %s, %s, %s, 'PROCESSED', %s, %s::jsonb, 'AA', %s)
                    RETURNING id
                    """,
                    (
                        source_ip,
                        analyzer_code,
                        parsed["messageType"],
                        parsed["messageControlId"],
                        raw_message,
                        json.dumps(parsed),
                        ack,
                    ),
                )
                raw_id = cursor.fetchone()["id"]
                cursor.execute(
                    """
                    INSERT INTO patients (patient_external_id, full_name)
                    VALUES (%s, %s)
                    ON CONFLICT (patient_external_id) DO UPDATE SET
                        full_name = COALESCE(EXCLUDED.full_name, patients.full_name),
                        updated_at = NOW()
                    RETURNING id
                    """,
                    (patient["id"], patient.get("name")),
                )
                patient_id = cursor.fetchone()["id"]
                cursor.execute(
                    """
                    INSERT INTO lab_orders (
                        order_external_id, analyzer_code, patient_id, panel_code,
                        panel_name, status, last_message_type
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (analyzer_code, order_external_id) DO UPDATE SET
                        patient_id = EXCLUDED.patient_id,
                        panel_code = COALESCE(EXCLUDED.panel_code, lab_orders.panel_code),
                        panel_name = COALESCE(EXCLUDED.panel_name, lab_orders.panel_name),
                        status = EXCLUDED.status,
                        last_message_type = EXCLUDED.last_message_type,
                        updated_at = NOW()
                    RETURNING id
                    """,
                    (
                        order["id"],
                        analyzer_code,
                        patient_id,
                        order.get("panelCode"),
                        order.get("panelName"),
                        order.get("status"),
                        parsed["messageType"],
                    ),
                )
                order_id = cursor.fetchone()["id"]
                for test in parsed["tests"]:
                    cursor.execute(
                        """
                        INSERT INTO lab_results (
                            lab_order_id, raw_message_id, test_code, value_text,
                            value_numeric, unit, reference_range, abnormal_flag, result_status
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            order_id,
                            raw_id,
                            test["code"],
                            str(test["value"]),
                            self.numeric_value(test["value"]),
                            test.get("unit"),
                            test.get("referenceRange"),
                            test.get("abnormalFlag"),
                            test.get("status"),
                        ),
                    )
            connection.commit()
        return raw_id

    def save_rejected(
        self,
        *,
        raw_message: str,
        source_ip: str,
        analyzer_code: str | None,
        message_type: str | None,
        message_control_id: str | None,
        ack: str,
        error: str,
    ) -> int:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO raw_hl7_messages (
                        source_ip, analyzer_code, message_type, message_control_id,
                        processing_status, raw_message, ack_code, ack_message, parse_error
                    )
                    VALUES (%s, %s, %s, %s, 'REJECTED', %s, 'AE', %s, %s)
                    RETURNING id
                    """,
                    (
                        source_ip,
                        analyzer_code,
                        message_type,
                        message_control_id,
                        raw_message,
                        ack,
                        error,
                    ),
                )
                raw_id = cursor.fetchone()["id"]
            connection.commit()
        return raw_id

    def status_summary(self) -> dict:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        COUNT(*) AS message_count,
                        COUNT(*) FILTER (WHERE processing_status = 'PROCESSED') AS processed_count,
                        COUNT(*) FILTER (WHERE processing_status = 'REJECTED') AS rejected_count,
                        MAX(received_at) AS last_received_at
                    FROM raw_hl7_messages
                    """
                )
                return dict(cursor.fetchone())

    def messages(self, limit: int = 25) -> list[dict]:
        return self._rows(
            """
            SELECT id, received_at, source_ip, analyzer_code, message_type,
                   message_control_id, processing_status, raw_message, parsed_json,
                   ack_code, ack_message, parse_error
            FROM raw_hl7_messages
            ORDER BY received_at DESC, id DESC LIMIT %s
            """,
            (limit,),
        )

    def patients(self) -> list[dict]:
        return self._rows(
            """
            SELECT patient_external_id, full_name, first_seen_at, updated_at
            FROM patients ORDER BY updated_at DESC
            """
        )

    def orders(self) -> list[dict]:
        return self._rows(
            """
            SELECT o.order_external_id, o.analyzer_code, p.patient_external_id,
                   o.panel_code, o.panel_name, o.status, o.last_message_type, o.updated_at
            FROM lab_orders o JOIN patients p ON p.id = o.patient_id
            ORDER BY o.updated_at DESC
            """
        )

    def results(self, limit: int = 100) -> list[dict]:
        return self._rows(
            """
            SELECT r.id, o.order_external_id, p.patient_external_id, r.test_code,
                   r.value_text, r.value_numeric, r.unit, r.reference_range,
                   r.abnormal_flag, r.result_status, r.observed_at
            FROM lab_results r
            JOIN lab_orders o ON o.id = r.lab_order_id
            JOIN patients p ON p.id = o.patient_id
            ORDER BY r.observed_at DESC, r.id DESC LIMIT %s
            """,
            (limit,),
        )

    def _rows(self, sql: str, parameters: tuple = ()) -> list[dict]:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql, parameters)
                return [dict(row) for row in cursor.fetchall()]

