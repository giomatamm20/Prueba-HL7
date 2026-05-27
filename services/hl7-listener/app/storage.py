import json

import psycopg


class PostgresMessageStore:
    def __init__(self, database_url: str):
        self.database_url = database_url

    def save(
        self,
        *,
        raw_message: str,
        source_ip: str,
        message_control_id: str | None,
        ack_message: str,
        parsed_json: dict | None,
        parse_error: str | None,
    ) -> None:
        with psycopg.connect(self.database_url) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO received_messages (
                        source_ip, message_control_id, raw_message,
                        ack_message, parsed_json, parse_error
                    )
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                    """,
                    (
                        source_ip,
                        message_control_id,
                        raw_message,
                        ack_message,
                        json.dumps(parsed_json) if parsed_json is not None else None,
                        parse_error,
                    ),
                )
            connection.commit()
