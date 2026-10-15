# Equipos HL7: configurar puerto, entrada y salida

Este es el alcance acordado: cada equipo recibe HL7 por MLLP y, cuando el usuario
lo solicita, envía el resultado definido en su editor. No hay envío automático de
resultados al recibir una orden.

## Flujo de uso

1. Crear el equipo, elegir protocolo HL7, modo bidireccional, puerto de recepción
   y host/puerto de destino de resultados. En dispositivos genéricos
   bidireccionales se puede dejar la lista de pruebas vacía.
2. El equipo queda en **STANDBY**, sin listener, hasta completar y activar ambos
   contratos en el editor: entrada y salida. Una definición incompleta se guarda
   como **DRAFT**; no activa un equipo nuevo.
3. Con el contrato completo y activo, se abre únicamente el listener de ese
   equipo. Revisar `contractReady`, `listenerActive` y `listenerError` en la API o
   el estado mostrado en la pantalla.
4. Enviar una orden desde HIS/Mirth. Se valida la entrada y se devuelve su ACK.
   La orden aceptada queda pendiente; todavía no se envía ningún resultado.
5. Desde la aplicación, cargar y procesar la muestra. Después usar **Enviar
   informe**, o la acción manual **Todo**, que ya procesa y envía.
6. Si hace falta corregir el resultado, editar y guardar el contrato de salida
   y usar **Reenviar resultado**. Se construye un mensaje nuevo con el contrato
   activo corregido. No hace falta volver a recibir la orden ni reconstruir Docker.

Las modificaciones de contratos no reinician el listener. Una edición de nombre
o destino tampoco reinicia sockets. Cambiar el puerto o protocolo afecta solo al
equipo editado. Los cambios parciales conservan los valores que no se enviaron.

Si se retira el contrato activo y no queda otro activo, el equipo vuelve a
STANDBY y cierra su listener. Las revisiones retiradas se conservan para la
trazabilidad de mensajes y órdenes. Guardar un nuevo borrador incompleto no
reemplaza el contrato completo que otro equipo o esa misma máquina ya utiliza.

## Puertos preparados una sola vez

El Compose principal y el despliegue independiente ya incluyen **5100–5119/TCP**,
conservando las publicaciones existentes. El pool aparece como sugerencias en el campo de
puerto y en `GET /api/ports`, indicando los puertos ya asignados. El backend
rechaza duplicados, puertos fuera de rango, 8090 reservado y puertos que no estén
en el pool configurado. `0` significa listener deshabilitado.

Aplicar inicialmente en el servidor de pruebas, con su `.env` completo:

```bash
docker compose -f docker-compose.yml config --quiet
docker compose -f docker-compose.yml up -d --build --no-deps analyzer-simulator-api
```

Esto requiere una recreación inicial para publicar los puertos. Después, agregar
equipos, modificar contratos o cambiar destinos se hace desde la aplicación.
El overlay `docker-compose.port-pool.yml` se conserva por compatibilidad y ya no
es necesario para obtener el pool. No basta con
`docker restart` para agregar publicaciones. El firewall del servidor debe
permitir los puertos del laboratorio desde HIS/Mirth.

`SIMULATOR_BIND_IP` permite elegir la IP privada del servidor; por compatibilidad
su valor por defecto conserva el enlace en todas las interfaces. El uso previsto
es la red interna del hospital. `SIMULATOR_ALLOWED_LISTEN_PORTS` se obtiene del
Compose elegido; si se configura manualmente, debe coincidir con lo publicado.
Este listado representa la configuración declarada: la aplicación no consulta
el daemon Docker para descubrir publicaciones.

El Compose exclusivo de AnaPath publica 9500 y también el pool 5100–5119.
Los puertos del equipo son los
internos del contenedor: por ejemplo `15001:5001` implica configurar `listenPort`
5001 y conectar desde fuera al puerto 15001.

Para instalar en un servidor nuevo sin los demás servicios del laboratorio,
seguir [Despliegue en servidor](DESPLIEGUE_SERVIDOR.md). Su Compose exige una IP
de enlace y el ejemplo usa localhost hasta configurar la IP privada real.

Referencia: [publicación de puertos en Compose](https://docs.docker.com/reference/compose-file/services/#ports).

## Qué utiliza el editor

La definición del equipo genérico se obtiene de su contrato; no es necesario
programar un fabricante nuevo. Se conservan los comportamientos específicos de
los perfiles existentes y las reglas adicionales configuradas expresamente.

- La validación **estricta** rechaza diferencias del contrato con ACK negativo y
  no crea la orden. En modo de **advertencias**, las diferencias estructurales se
  registran y pueden acompañar un ACK positivo; los errores técnicos que impiden
  leer la orden siguen siendo rechazos.
- El editor conserva el orden de segmentos del contrato. Los valores fijos
  editados visualmente se guardan también en el esquema utilizado para generar
  la salida. El backend valida el resultado antes de enviarlo.
- Los datos variables se obtienen de la orden y de los resultados simulados. Una
  definición avanzada puede copiar un campo recibido con, por ejemplo,
  `"source": "inbound.ZXX-1"`; un valor fijo se expresa con `"const": "READY"`
  en la regla del árbol del editor. No se usan identificadores de paciente de la
  muestra inicial como sustitutos silenciosos de datos faltantes.
- Cada edición crea una revisión nueva. La orden registra el contrato que la
  recibió y cada envío registra la revisión que generó su resultado.
- Reintentar un envío fallido **sin cambiar el contrato** conserva sus bytes e
  identificador para recuperarse de un ACK perdido. **Después de activar una
  corrección**, el reenvío reconstruye el mensaje y genera un identificador nuevo.
- Un reenvío idéntico de la orden de entrada recupera su ACK y no crea otra orden.
  Reutilizar el mismo identificador con contenido distinto se rechaza. Cambiar
  legítimamente una orden requiere un mensaje nuevo y un control configurado;
  no se interpreta una repetición `NW` como actualización silenciosa.

La recepción y el envío usan UTF-8; ASTM conserva su variante TCP simplificada y
ASCII. El simulador PACS continúa siendo un flujo HL7 con imagen de prueba, sin
declaración de conformidad DICOM. El estado STANDBY por contrato completo se
aplica a los equipos HL7, incluido un perfil especializado si carece de contrato.

## Persistencia y comprobación

Equipos, revisiones, órdenes, mensajes y ACK se conservan en SQLite dentro del
directorio `data` montado por Docker. El perfil AnaPath independiente usa otra
base. Una instalación vacía crea el perfil PACS base, que ahora también espera
su contrato; los demás equipos se agregan desde la aplicación. No se modifica
ninguna base existente durante la ejecución de las pruebas.

Para repetir las pruebas, desde la raíz del repositorio y con Python 3.12:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r services/analyzer-simulator-api/requirements-dev.txt
.\.venv\Scripts\python.exe scripts/audit_simulator.py
```

La evidencia actual se guarda en `docs/resultado_validacion_hl7.json`, con el log
completo en el archivo del mismo nombre y extensión `.log`. La prueba de
conversión del editor ejecuta su JavaScript real si Node está instalado; si no,
queda identificada como omitida. Todas las conexiones de prueba son locales y
los datos son sintéticos.

Resultado de la ejecución final: **57 pruebas, 0 fallos, 0 errores y 0 omitidas**.

| Caso | Evidencia reproducible |
| --- | --- |
| Equipo sin contrato / contrato solo de entrada | API: STANDBY, listener cerrado, activación incompleta rechazada |
| Equipo nuevo sin catálogo de fabricante | Alta por API, contrato desde editor, orden por TCP, ACK y resultado configurado |
| ACK separado del resultado | El receptor de resultados permanece vacío hasta procesar/enviar manualmente |
| Corrección con equipo online | Nueva revisión, reenvío corregido, listener conservado, una sola orden |
| Pool y edición de puertos | Duplicados, puerto ocupado y fuera de rango rechazados; otros listeners conservados |
| Contratos y retransmisiones | Estricto/advertencias, campos requeridos, revisión independiente, concurrencia y ACK perdido |
| Plantillas existentes | Circuitos con sockets para HL7 genérico, Ruby, XN, ARCHITECT, AnaPath; regresiones ASTM y PACS |
| Persistencia | Nueva instancia del motor recupera órdenes, perfiles, ACK y operaciones interrumpidas sin reenviar automáticamente |

Las pruebas por nombre de equipo corresponden a las plantillas del simulador;
no prueban conformidad de un dispositivo físico. El despliegue Docker real y la
prueba contra Mirth/HIS del hospital siguen pendientes: el daemon Docker local
no estuvo accesible. La validación de los Compose y del overlay se registra en
`docs/validacion_docker.json`.

El documento de auditoría anterior y su JSON son evidencia histórica. Esta guía
describe el alcance final acordado: configuración de puertos y mensajes, flujo
manual existente y espera hasta completar el contrato.
