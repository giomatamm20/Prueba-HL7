# Despliegue del simulador en un servidor externo

El despliegue independiente incluye la aplicación web, SQLite persistente y el
pool **5100–5119/TCP** para 20 equipos con puertos independientes. Conserva además
5001–5004, 5010–5011, 5020 y 9500 para las configuraciones existentes. No necesita
el host CodeIgniter ni PostgreSQL del laboratorio.

Preparado para un servidor Linux con Docker Engine y Docker Compose, accesible
por la LAN o VPN del hospital. La aplicación actual no incorpora autenticación
de usuarios: limitar el acceso a esa red y a los equipos autorizados. Una IP
pública abierta a Internet requiere resolver antes ese acceso protegido.

## 1. Copiar y configurar

Se puede usar el repositorio o el paquete de código
`dist/hl7-simulator-server.tar.gz` (solo código) o
`dist/hl7-simulator-server-completo.tar.gz` (**con las dos bases SQLite y la
configuración local**). El paquete completo es privado: incluye los datos e
integraciones existentes. No contiene una imagen Docker ya construida.
Para comprobar y extraer el paquete completo en Linux, copiar también su `.sha256`:

```bash
sha256sum -c hl7-simulator-server-completo.tar.gz.sha256
tar -xzf hl7-simulator-server-completo.tar.gz
cd hl7-simulator-server
sha256sum -c MANIFEST.sha256
```

Extraer siempre en una carpeta nueva para no sobrescribir una instalación en
uso. El paquete completo ya trae `data/simulator.db`, `data/anapath-simulator.db`
y `.env.server`: no hay que copiar bases por separado ni crear ese archivo otra
vez. Los respaldos se generan con SQLite Online Backup y se comprueban con
`PRAGMA integrity_check`; el detalle está en `docs/respaldo_sqlite.json`.
Son instantáneas independientes de cada base en el momento del empaquetado.

El paquete mantiene el historial, los equipos y los contratos guardados; no
inventa contratos faltantes. La base AnaPath existente carece de perfiles HL7
guardados: sus equipos seguirán en STANDBY hasta completar sus contratos, según
el comportamiento acordado.

Desde la raíz del repositorio o del paquete, crear el archivo de entorno solo
si todavía no existe:

```bash
test -e .env.server || cp .env.server.example .env.server
nano .env.server
```

Configurar estos valores:

| Variable | Valor que corresponde |
| --- | --- |
| `SIMULATOR_BIND_IP` | IP privada real asignada al servidor; el ejemplo usa `127.0.0.1` y solo permite acceso local hasta modificarla. |
| `ANALYZER_SIMULATOR_PORT` | Puerto web publicado; por defecto `8090`. |
| `SIMULATOR_DATA_DIR` | Directorio persistente; por defecto `./data`, relativo al Compose. Puede ser una ruta absoluta. |
| `SIMULATOR_DB_FILE` | `simulator.db`; para migrar AnaPath independiente, `anapath-simulator.db`. |

El directorio de datos debe permitir escritura al contenedor. No eliminarlo al
actualizar. `RESULT_DESTINATION_*` y `PACS_NOTIFICATION_*` son valores del perfil
PACS integrado; el destino de cada equipo HL7 se configura en la aplicación.
`host.docker.internal` apunta al propio servidor Docker: si Mirth/HIS está en
otra máquina, usar su dirección real en el equipo correspondiente.

## 2. Arrancar

```bash
docker compose --env-file .env.server -f docker-compose.server.yml config --quiet
docker compose --env-file .env.server -f docker-compose.server.yml up -d --build
docker compose --env-file .env.server -f docker-compose.server.yml ps
docker compose --env-file .env.server -f docker-compose.server.yml logs --tail 100
```

La primera construcción descarga la imagen Python y las dependencias. Mantener
una sola instancia del servicio y un solo worker, como establece el Dockerfile.
El motor y los listeners mantienen estado en ese proceso.

Abrir `http://IP_PRIVADA_DEL_SERVIDOR:8090` o el puerto web elegido. Comprobar:

```bash
curl --fail http://IP_PRIVADA_DEL_SERVIDOR:8090/api/health
curl --fail http://IP_PRIVADA_DEL_SERVIDOR:8090/api/ports
```

Sustituir `IP_PRIVADA_DEL_SERVIDOR` en estas direcciones. El healthcheck interno
consulta la salud de la aplicación. Docker marca un fallo como `unhealthy`;
la política de reinicio reinicia procesos que terminan, no un proceso que sigue
vivo pero está `unhealthy`. Consultar los logs si aparece ese estado.

## 3. Red y prueba funcional

Permitir en la red/firewall del servidor:

- `8090/TCP` (o puerto web elegido) desde los puestos de administración.
- `5100–5119/TCP` desde HIS/Mirth para las órdenes a nuevos equipos.
- Los puertos conservados `5001–5004`, `5010–5011`, `5020` y `9500`, solo desde
  los orígenes que los necesiten.
- Salida desde el contenedor hacia el host/puerto receptor de resultados de
  HIS/Mirth. Ese destino no requiere una publicación entrante en este Docker.

La publicación y las reglas de Docker deben quedar cubiertas por el control de
acceso de red del servidor. Los archivos no modifican automáticamente su firewall.
Evitar que otra aplicación ocupe alguno de los puertos publicados; Compose
fallará al arrancar si están ocupados o la IP indicada no pertenece al servidor.

Crear un equipo con un puerto libre del pool, por ejemplo `5100`. Hasta activar
su contrato completo de entrada y salida queda en **STANDBY**, sin recepción ni
envío. Docker reserva la publicación, pero el listener de la aplicación se abre
solo cuando el equipo tiene contrato y está habilitado.

Probar el circuito con datos sintéticos:

1. Configurar y activar ambos contratos en el editor dinámico.
2. Enviar una orden a `IP_PRIVADA_DEL_SERVIDOR:5100`; comprobar ACK y orden pendiente.
3. Confirmar que no se envió un resultado automáticamente.
4. Procesar y enviar manualmente; comprobar el HL7 recibido en HIS/Mirth.
5. Editar el contrato de salida y reenviar manualmente el mensaje corregido.
6. Reiniciar el contenedor y verificar equipos, contratos, órdenes e historial.

Agregar equipos y editar contratos dentro del pool no requiere reconstruir la
imagen ni recrear el contenedor. El primer despliegue con estas publicaciones sí
requiere recrearlo; `docker restart` no agrega puertos. Ampliar el pool en el
futuro requiere modificar las publicaciones y la lista permitida del Compose,
permitir los nuevos puertos en la red y recrear el servicio una vez.

Referencia: [publicaciones de puertos de Docker Compose](https://docs.docker.com/reference/compose-file/services/#ports).

## 4. Trasladar una base existente

**Este paso solo es necesario si se usa el paquete de código o si se desea una
copia más reciente que la incluida en el paquete completo.** El paquete de
código inicia una base vacía si no encuentra una existente. Para
conservar equipos, contratos e historial, trasladar el directorio `data` completo.
Usar una ventana de mantenimiento: detener el servicio de origen antes de
archivarlo, para conservar consistentemente SQLite y sus posibles archivos WAL.

Para una instalación anterior con el Compose principal, ejecutar en su raíz:

```bash
docker compose stop analyzer-simulator-api
tar -czf simulator-data.tar.gz -C services/analyzer-simulator-api data
```

Para AnaPath independiente, el comando de detención es
`docker compose --env-file .env.anapath -f docker-compose.anapath.yml stop anapath-simulator`.
Archivar después el mismo directorio. Si la instalación usa otras rutas o un
archivo de entorno distinto, utilizar sus rutas reales. Proteger la transferencia
y el archivo porque pueden contener datos de pacientes y credenciales de equipos.

En el destino, antes de su primer arranque, extraer el archivo en una carpeta de
despliegue cuyo `data` todavía no exista:

```bash
test ! -e data && tar -xzf simulator-data.tar.gz
```

Con `SIMULATOR_DATA_DIR=./data`, se utilizarán esos archivos. Para otro directorio,
restaurar allí y ajustar la variable. Elegir `SIMULATOR_DB_FILE` según la base
de origen; las bases general y AnaPath son independientes y no se fusionan.
Una vez trasladado, mantener el origen detenido para evitar dos simuladores
enviando al mismo sistema. No copiar únicamente el `.db` mientras esté en uso.

Referencia: [persistencia mediante directorios montados de Docker](https://docs.docker.com/engine/storage/bind-mounts/).

## 5. Copias y actualizaciones

Ejemplo de copia consistente en la instalación independiente con `./data`:

```bash
docker compose --env-file .env.server -f docker-compose.server.yml stop
tar -czf "simulator-backup-$(date +%Y%m%d-%H%M%S).tar.gz" data .env.server
docker compose --env-file .env.server -f docker-compose.server.yml start
```

Adaptar la ruta si se usa un directorio absoluto. Guardar la copia fuera del
servidor, con acceso restringido. Para actualizar, conservar `.env.server` y
`data`, hacer la copia, sustituir el código y ejecutar otra vez `up -d --build`.
Para recuperar una versión anterior, conservar también su código/imagen y la
copia de datos tomada antes de actualizar. Restaurar con el contenedor detenido.

Si el servidor no puede descargar imágenes o dependencias, construir en otra
máquina con Docker para la misma arquitectura Linux y trasladar la imagen:

```bash
docker compose --env-file .env.server -f docker-compose.server.yml build
docker save -o hl7-simulator-image.tar hl7-simulator:local
# En el servidor destino, con el paquete y .env.server ya configurados:
docker load -i hl7-simulator-image.tar
docker compose --env-file .env.server -f docker-compose.server.yml up -d --no-build --pull never
```

Si se cambia `SIMULATOR_IMAGE`, usar el mismo nombre en `docker save`. El paquete
de código entregado no incluye ese archivo de imagen.

## Evidencia y límite de la validación

`docs/validacion_docker.json` registra la validación real de Compose: pool,
publicaciones sin duplicados, persistencia, IP/puerto configurables, base AnaPath,
servicio independiente y worker único. Se reproduce desde el repositorio con
`python scripts/validate_docker.py`. El paquete se regenera desde el repositorio
con `python scripts/package_simulator.py`. Para generar el paquete completo,
preparar `.env.server` y ejecutar
`python scripts/package_simulator.py --include-data`; lee las bases originales
en modo de solo lectura y conserva los respaldos dentro del archivo comprimido.

La regresión funcional previa está en `docs/resultado_validacion_hl7.json`:
57 pruebas correctas con datos sintéticos. El daemon Docker local no estuvo
accesible, por lo que **la imagen no se ha construido ni arrancado aquí**. Falta
ejecutar los comandos de arranque y la prueba de comunicación contra el HIS/Mirth
del servidor destino antes de dar ese entorno por validado. No se ha desplegado
ni modificado ningún servidor externo.
