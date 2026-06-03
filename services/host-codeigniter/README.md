# HL7 Host Middleware en CodeIgniter 4

Este servicio reemplaza `services/host-middleware` con PHP y CodeIgniter 4.
Mantiene los mismos endpoints REST, el dashboard y el listener HL7 MLLP.

## Instalar

```powershell
Push-Location services\host-codeigniter
composer install
Copy-Item .env.example .env
Pop-Location
```

Configura `.env` con la misma base PostgreSQL local:

```env
DATABASE_URL=postgresql://hl7host:hl7password@localhost:5432/hl7crm
MLLP_HOST=0.0.0.0
MLLP_PORT=2575
WEB_HOST=0.0.0.0
WEB_PORT=8088
```

## Arrancar

Terminal 1, API y dashboard:

```powershell
Push-Location services\host-codeigniter
php spark serve --host 0.0.0.0 --port 8088
```

Terminal 2, listener MLLP:

```powershell
Push-Location services\host-codeigniter
php spark hl7:mllp-listen
```

Dashboard:

```text
http://localhost:8088
```

## Endpoints

- `GET /api/status`
- `GET /api/messages?limit=25`
- `GET /api/patients`
- `GET /api/orders`
- `GET /api/results?limit=100`

## Nota

CodeIgniter sirve HTTP, pero el socket MLLP necesita correr como proceso CLI
persistente. Por eso el reemplazo se divide en `php spark serve` para web/API y
`php spark hl7:mllp-listen` para recibir mensajes HL7 por TCP.
