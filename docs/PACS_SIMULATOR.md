# Guia del simulador PACS

Esta seccion simula la integracion basica entre Mirth, un PACS y el HIS sin
implementar DICOM. El simulador recibe una orden HL7, genera una imagen SVG no
diagnostica y avisa a Mirth cuando el estudio esta disponible.

## Alcance

Incluye:

- listener MLLP para `ORM^O01` en el puerto `5020`;
- ACK/NACK inmediato;
- worklist PACS persistida en SQLite;
- busqueda interna por `account_no` y `accession_no`;
- imagen SVG dinamica con datos de la orden;
- visor web mediante el contrato cifrado del PACS legado;
- notificacion `ORU^R01` con `IMAGE_STATUS=READY`;
- ACK de Mirth, reintentos y trazabilidad.

No incluye DICOM, C-STORE, MWL, series ni archivos radiologicos reales.

## Flujo

```text
Mirth --ORM^O01/MLLP--> PACS simulado
Mirth <--ACK AA/AE----- PACS simulado
                           |
                           +-- registra PID-3 + OBR-3
                           +-- genera imagen SVG
                           +-- publica visor de prueba
                           |
Mirth <--ORU^R01 READY-- PACS simulado
Mirth --ACK AA/AE------> PACS simulado
```

Los identificadores son:

```text
account_no   = PID-3.1
accession_no = OBR-3
```

`OBR-3` es la llave principal del estudio. `PID-3` valida que el estudio
corresponda al paciente solicitado.

## Configuracion

Variables disponibles:

```env
PACS_ORDER_PORT=5020
PACS_NOTIFICATION_HOST=host.docker.internal
PACS_NOTIFICATION_PORT=2575
PACS_LEGACY_KEY_CODE=RIS_LOCAL
PACS_LEGACY_KEY=local-pacs-integration-key-change-me
PACS_LEGACY_IV_HEX=00112233445566778899aabbccddeeff
PACS_LEGACY_USERNAME=ris_local
```

- `PACS_ORDER_PORT`: puerto publicado para las ordenes de Mirth.
- `PACS_NOTIFICATION_HOST`: host MLLP de Mirth que recibe `IMAGE_READY`.
- `PACS_NOTIFICATION_PORT`: puerto MLLP de ese canal destino.
- `PACS_LEGACY_KEY_CODE`: codigo visible en la ruta segura.
- `PACS_LEGACY_KEY`: llave de cifrado; se toman sus primeros 32 bytes y se
  completa con `0` si es mas corta.
- `PACS_LEGACY_IV_HEX`: IV hexadecimal de exactamente 16 bytes.
- `PACS_LEGACY_USERNAME`: usuario que debe contener el payload.

Los valores del ejemplo son exclusivamente locales. Cada ambiente debe definir
sus propias credenciales, sin copiarlas al codigo ni compartir las de
produccion con el simulador.

Arranque:

```powershell
docker compose up --build analyzer-simulator-api
```

Interfaz:

```text
http://localhost:8090
```

En el menu lateral seleccionar **PACS radiologia**.

## Canal de entrada en Mirth

Configurar un destino TCP Sender con:

```text
Host: direccion del servidor del simulador
Puerto: 5020
Transmission Mode: MLLP
```

El mensaje debe contener `MSH`, `PID`, `PV1`, `ORC` y `OBR`. Los campos
obligatorios son:

```text
MSH-10
PID-3
PID-5
ORC-1
ORC-2
OBR-2
OBR-3
OBR-4.1
OBR-4.2
```

Ejemplo disponible en `samples/hl7/pacs_order_001.hl7`.

## Operacion desde la interfaz

1. Enviar el `ORM^O01` desde Mirth al puerto `5020`.
2. Confirmar el `ACK AA` en la seccion **Mensajes**.
3. Abrir **PACS radiologia**.
4. Confirmar `account_no`, `accession_no`, procedimiento y modalidad.
5. Pulsar **Generar imagen** para dejar el estudio en `IMAGE_READY`.
6. Pulsar **Ver imagen** para abrir el visor simulado.
7. Pulsar **Notificar READY** para enviar el `ORU^R01` a Mirth.
8. Confirmar el ACK del canal receptor. Con `AA`, el estudio queda
   `COMPLETED`.

Tambien se puede usar **Generar y notificar** para ejecutar los pasos 5 y 7
en una sola accion.

## Estados

```text
ORDER_RECEIVED
GENERATING_IMAGE
IMAGE_READY
NOTIFYING_MIRTH
COMPLETED
```

Estados de error:

```text
IMAGE_GENERATION_ERROR
NOTIFICATION_ERROR
```

Una notificacion fallida se puede reenviar desde la misma tarjeta del estudio.

## Notificacion enviada a Mirth

El mensaje de salida conserva paciente, orden y accession number:

```hl7
MSH|^~\&|PACS_SIM|RADIOLOGIA|MIRTH|HIS|...||ORU^R01|...|P|2.3.1
PID|||PAT-PACS-001||PRUEBA^SINTETICA||19800101|F
PV1||O
ORC|RE|PACS-TEST-0001|PACS-TEST-0001||CM
OBR||PACS-TEST-0001|PACS-TEST-0001|IMGS_0091^TC CEREBRAL SIMPLE|...|CT|F
OBX|1|ST|IMAGE_STATUS^ESTADO DE IMAGEN||READY||||||F
```

No se envia la imagen, Base64, URL, ruta fisica ni informacion DICOM.

## API del PACS simulado

| Endpoint | Uso |
| --- | --- |
| `GET /api/pacs/studies` | Lista la worklist PACS. |
| `GET /api/pacs/studies/{id}` | Consulta un estudio. |
| `POST /api/pacs/studies/{id}/process` | Genera la imagen. |
| `POST /api/pacs/studies/{id}/notify` | Envia `IMAGE_READY` a Mirth. |
| `GET /api/pacs/studies/{id}/image.svg` | Devuelve la imagen generada. |
| `GET /api/pacs/studies/{id}/viewer` | Abre el visor simulado. |
| `GET /secure_link/code/{key_code}/payload/{payload}` | Entrada que debe usar el RIS; valida y abre el visor. |
| `GET /api/pacs/viewer?account_no=...&accession_no=...` | Entrada interna sin cifrado, reservada para diagnostico local. |

Para generar y notificar en una sola llamada:

```http
POST /api/pacs/studies/{id}/process
Content-Type: application/json

{"sendNotification":true}
```

## Enlace del RIS al PACS

El RIS usa el mismo contrato en simulacion y produccion:

```text
GET /secure_link/code/{key_code}/payload/{payload}
```

La cadena original debe conservar exactamente este orden:

```text
integration_type=HIS
&generated_dt=2026-10-07 14:31:00
&username=ris_local
&account_no=PAT-PACS-001
&flag=2
&accession_no=PACS-TEST-0001
```

`generated_dt` lo genera el RIS con la hora UTC actual mas un minuto y formato
`Y-m-d H:i:s`. La cadena se procesa con `http_build_query`, AES-256-CBC con
padding PKCS#7, Base64 y URL encode. Como Base64 puede producir `/`, el payload
se captura como una ruta completa.

El simulador valida el codigo, descifra el payload, comprueba el orden de los
parametros, `integration_type=HIS`, el usuario configurado, `flag=2` y la
presencia de ambos identificadores. Luego busca el estudio por la combinacion
`account_no + accession_no`. Solo abre el visor si la imagen esta lista.

Respuestas controladas:

| Estado HTTP | Significado |
| --- | --- |
| `200` | Enlace valido e imagen lista. |
| `400` | Payload, cifrado, formato u orden de parametros invalido. |
| `403` | Codigo, usuario, tipo de integracion o flag invalido. |
| `404` | No existe el estudio para ambos identificadores. |
| `409` | El estudio existe, pero su imagen aun no esta lista. |
| `503` | Faltan credenciales o el IV local es invalido. |

## Escenarios de prueba

En la tarjeta del equipo `pacs-simulator` se puede seleccionar:

- `NORMAL`: procesamiento y notificacion normales;
- `IMAGE_GENERATION_ERROR`: falla al generar la imagen;
- `NO_RESPONSE`: simula que Mirth no responde al `ORU^R01`.

Tambien se rechaza una segunda orden activa con el mismo `OBR-3` para evitar
dos estudios con el mismo accession number.
