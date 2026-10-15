import base64
import binascii
import hmac
import html
import os
from datetime import datetime, timezone
from urllib.parse import parse_qsl, unquote

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .hl7_orders import HL7OrderError, hl7_path_value


PACS_ANALYZER_ID = "pacs-simulator"
LEGACY_PARAMETER_ORDER = [
    "integration_type",
    "generated_dt",
    "username",
    "account_no",
    "flag",
    "accession_no",
]


class PacsSecureLinkError(ValueError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def decrypt_legacy_secure_payload(key_code: str, payload: str) -> dict[str, str]:
    config = _legacy_link_config()
    if not hmac.compare_digest(str(key_code), config["keyCode"]):
        raise PacsSecureLinkError("Codigo de integracion PACS invalido", 403)

    try:
        encoded = unquote(payload)
        ciphertext = base64.b64decode(encoded, validate=True)
        if not ciphertext or len(ciphertext) % 16:
            raise ValueError("invalid AES block length")
        decryptor = Cipher(
            algorithms.AES(_legacy_aes_key(config["encryptionKey"])),
            modes.CBC(_legacy_iv(config["ivHex"])),
        ).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        plaintext = (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise PacsSecureLinkError("Payload PACS invalido o no se pudo descifrar") from exc

    try:
        pairs = parse_qsl(plaintext, keep_blank_values=True, strict_parsing=True)
    except ValueError as exc:
        raise PacsSecureLinkError("El payload PACS no contiene un query string valido") from exc
    names = [name for name, _ in pairs]
    if names != LEGACY_PARAMETER_ORDER:
        raise PacsSecureLinkError("Orden o conjunto de parametros PACS invalido")
    values = dict(pairs)

    if values["integration_type"] != "HIS":
        raise PacsSecureLinkError("integration_type PACS invalido", 403)
    if not hmac.compare_digest(values["username"], config["username"]):
        raise PacsSecureLinkError("Usuario de integracion PACS invalido", 403)
    if values["flag"] != "2":
        raise PacsSecureLinkError("Flag PACS invalido", 403)
    if not values["account_no"].strip():
        raise PacsSecureLinkError("account_no es obligatorio")
    if not values["accession_no"].strip():
        raise PacsSecureLinkError("accession_no es obligatorio")
    try:
        datetime.strptime(values["generated_dt"], "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise PacsSecureLinkError("generated_dt PACS invalido") from exc
    return values


def _legacy_link_config() -> dict[str, str]:
    config = {
        "keyCode": os.environ.get("PACS_LEGACY_KEY_CODE", ""),
        "encryptionKey": os.environ.get("PACS_LEGACY_KEY", ""),
        "ivHex": os.environ.get("PACS_LEGACY_IV_HEX", ""),
        "username": os.environ.get("PACS_LEGACY_USERNAME", ""),
    }
    missing = [name for name, value in config.items() if not value]
    if missing:
        raise PacsSecureLinkError("Integracion PACS segura no configurada", 503)
    _legacy_iv(config["ivHex"])
    return config


def _legacy_aes_key(value: str) -> bytes:
    return value.encode("utf-8")[:32].ljust(32, b"0")


def _legacy_iv(value: str) -> bytes:
    try:
        iv = bytes.fromhex(value)
    except ValueError as exc:
        raise PacsSecureLinkError("IV PACS configurado es invalido", 503) from exc
    if len(iv) != 16:
        raise PacsSecureLinkError("IV PACS debe contener exactamente 16 bytes", 503)
    return iv


def default_pacs_analyzer() -> dict:
    return {
        "id": PACS_ANALYZER_ID,
        "name": "PACS radiologia simulado",
        "vendor": "PACS Simulator",
        "protocol": "HL7",
        "transport": "MLLP",
        "listenPort": int(os.environ.get("PACS_ORDER_PORT", "5020")),
        "resultDestinationHost": os.environ.get(
            "PACS_NOTIFICATION_HOST",
            os.environ.get("RESULT_DESTINATION_HOST", "host.docker.internal"),
        ),
        "resultDestinationPort": int(
            os.environ.get(
                "PACS_NOTIFICATION_PORT",
                os.environ.get("RESULT_DESTINATION_PORT", "2575"),
            )
        ),
        "supportedTests": [],
        "profile": "PACS",
        "mode": "BIDIRECTIONAL",
        "behavior": {
            "acceptedMessageTypes": ["ORM^O01"],
            "requiredSegments": ["MSH", "PID", "PV1", "ORC", "OBR"],
            "requiredFields": [
                "MSH-10",
                "PID-3",
                "PID-5",
                "ORC-1",
                "ORC-2",
                "OBR-2",
                "OBR-3",
                "OBR-4.1",
                "OBR-4.2",
            ],
            "allowedOrderControls": ["NW", "CA"],
            "ackSuccessCode": "AA",
            "ackErrorCode": "AE",
            "ackSuccessText": "",
            "outboundEvents": ["IMAGE_READY"],
            "allowedScenarios": ["NORMAL", "IMAGE_GENERATION_ERROR", "NO_RESPONSE"],
            "reportStatuses": ["F"],
            "inboundMappings": {
                "patientId": "PID-3.1",
                "patientName": "PID-5",
                "orderControl": "ORC-1",
                "orderId": "ORC-2,OBR-2",
                "fillerOrderId": "ORC-3,OBR-3",
                "sampleId": "OBR-3",
                "procedureCode": "OBR-4.1",
                "procedureName": "OBR-4.2",
                "visitType": "PV1-2",
                "priority": "OBR-27,ORC-7.6",
                "service": "PV1-10,PV1-11",
                "modality": "OBR-24",
                "orderingProvider": "ORC-12",
            },
            "resultFormat": "IMAGE_READY",
            "outboundMappings": {},
        },
    }


def decorate_pacs_order(order: dict, raw_message: str) -> dict:
    accession_no = hl7_path_value(raw_message, "OBR-3")
    account_no = hl7_path_value(raw_message, "PID-3.1")
    if not account_no:
        raise HL7OrderError("Missing PID-3 account number")
    if not accession_no:
        raise HL7OrderError("Missing OBR-3 accession number")

    return {
        **order,
        "deviceType": "PACS",
        "status": "ORDER_RECEIVED",
        "accountNo": account_no,
        "accessionNo": accession_no,
        "modality": hl7_path_value(raw_message, "OBR-24") or "IMG",
        "service": hl7_path_value(raw_message, "PV1-10") or hl7_path_value(raw_message, "PV1-11"),
        "orderingProvider": hl7_path_value(raw_message, "ORC-12"),
        "sourceOrderStatus": hl7_path_value(raw_message, "ORC-5"),
        "imageReadyAt": None,
        "lastNotificationAt": None,
    }


def build_image_ready_oru(analyzer: dict, study: dict, sequence: int) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d%H%M%S")
    control_id = f"PACS-READY-{now.strftime('%Y%m%d%H%M%S%f')}-{sequence:06d}"
    accession = study["accessionNo"]
    order_id = study.get("orderId") or accession
    procedure_code = study.get("procedureCode") or "IMG"
    procedure_name = study.get("procedureName") or "ESTUDIO DE IMAGEN"
    patient_id = study["accountNo"]
    patient_name = study.get("patientName") or "PACIENTE^DESCONOCIDO"
    birth_date = study.get("patientBirthDate") or ""
    sex = study.get("patientSex") or ""
    visit_type = study.get("visitType") or "O"
    modality = study.get("modality") or "IMG"

    segments = [
        f"MSH|^~\\&|PACS_SIM|RADIOLOGIA|MIRTH|HIS|{timestamp}||ORU^R01|{control_id}|P|2.3.1",
        f"PID|||{patient_id}||{patient_name}||{birth_date}|{sex}",
        f"PV1||{visit_type}",
        f"ORC|RE|{order_id}|{accession}||CM",
        _obr_ready(order_id, accession, procedure_code, procedure_name, modality),
        "OBX|1|ST|IMAGE_STATUS^ESTADO DE IMAGEN||READY||||||F",
    ]
    return "\r".join(segments) + "\r", control_id


def render_study_svg(study: dict) -> str:
    def safe(value: object) -> str:
        return html.escape(str(value or "-"), quote=True)

    modality = safe(study.get("modality") or "IMG")
    title = safe(study.get("procedureName") or "ESTUDIO DE IMAGEN")
    rings = "".join(
        f'<ellipse cx="600" cy="{315 + index * 5}" rx="{235 - index * 24}" ry="{175 - index * 17}" '
        f'fill="none" stroke="#{70 + index * 18:02x}{82 + index * 15:02x}{90 + index * 12:02x}" stroke-width="{10 - index}"/>'
        for index in range(6)
    )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="760" viewBox="0 0 1200 760">
  <rect width="1200" height="760" fill="#081015"/>
  <rect x="25" y="25" width="1150" height="710" rx="12" fill="#101b21" stroke="#6d7f88" stroke-width="2"/>
  <text x="55" y="70" fill="#dce7eb" font-family="monospace" font-size="24">PACS SIMULADO · {modality}</text>
  <text x="55" y="105" fill="#8fa7b0" font-family="monospace" font-size="18">{title}</text>
  <g opacity="0.9">{rings}</g>
  <ellipse cx="540" cy="330" rx="38" ry="62" fill="#b8c3c8" opacity="0.45"/>
  <ellipse cx="660" cy="330" rx="38" ry="62" fill="#b8c3c8" opacity="0.45"/>
  <path d="M600 235 C565 275 570 390 600 430 C630 390 635 275 600 235Z" fill="#dbe2e5" opacity="0.3"/>
  <g fill="#dce7eb" font-family="monospace" font-size="18">
    <text x="55" y="560">PACIENTE: {safe(study.get('patientName'))}</text>
    <text x="55" y="592">DOCUMENTO: {safe(study.get('accountNo'))}</text>
    <text x="55" y="624">ACCESSION: {safe(study.get('accessionNo'))}</text>
    <text x="55" y="656">PROCEDIMIENTO: {safe(study.get('procedureCode'))} - {title}</text>
    <text x="55" y="688">GENERADA: {safe(study.get('imageReadyAt'))}</text>
  </g>
  <text x="600" y="715" text-anchor="middle" fill="#e3a84c" font-family="sans-serif" font-size="17" font-weight="bold">IMAGEN SIMULADA - NO DIAGNOSTICA</text>
</svg>'''


def render_viewer_html(study: dict, image_url: str) -> str:
    accession = html.escape(str(study.get("accessionNo") or ""), quote=True)
    patient = html.escape(str(study.get("patientName") or ""), quote=True)
    return f'''<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Estudio {accession}</title><style>
body{{margin:0;background:#081015;color:#e4edf0;font:14px Segoe UI,sans-serif}}header{{padding:14px 20px;background:#12232b;display:flex;justify-content:space-between}}main{{display:grid;place-items:center;padding:18px}}img{{width:min(1200px,96vw);height:auto;border:1px solid #526771}}small{{color:#9cb0b8}}
</style></head><body><header><div><strong>{patient}</strong><br><small>Accession {accession}</small></div><div>PACS SIMULADO</div></header>
<main><img src="{html.escape(image_url, quote=True)}" alt="Imagen simulada del estudio {accession}"></main></body></html>'''


def _obr_ready(order_id: str, accession: str, code: str, name: str, modality: str) -> str:
    fields = ["OBR", "", order_id, accession, f"{code}^{name}"]
    while len(fields) <= 24:
        fields.append("")
    fields[24] = modality
    while len(fields) <= 25:
        fields.append("")
    fields[25] = "F"
    return "|".join(fields)
