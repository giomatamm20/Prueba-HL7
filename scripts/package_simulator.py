"""Package server source; --include-data adds consistent SQLite backups and .env.server."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'hl7-simulator-server'
SERVICE = 'services/analyzer-simulator-api'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--include-data', action='store_true',
                        help='Include private .env.server and SQLite online backups')
    args = parser.parse_args()
    names = ['docker-compose.server.yml', '.env.server.example',
             'docs/DESPLIEGUE_SERVIDOR.md', 'docs/CONFIGURACION_EQUIPOS_HL7.md',
             'docs/validacion_docker.json', 'docs/resultado_validacion_hl7.json',
             f'{SERVICE}/Dockerfile', f'{SERVICE}/requirements.txt',
             f'{SERVICE}/.dockerignore']
    suffixes = {'.py', '.html', '.js', '.css', '.json', '.svg', '.png', '.jpg',
                '.jpeg', '.gif', '.ico', '.woff', '.woff2', '.ttf'}
    for path in sorted((ROOT / SERVICE / 'app').rglob('*')):
        relative = path.relative_to(ROOT)
        if path.is_file() and not path.is_symlink() and path.suffix in suffixes:
            if not any(part.startswith('.') or part == '__pycache__' for part in relative.parts):
                names.append(relative.as_posix())
    content = {name: (ROOT / name).read_bytes() for name in names}
    output_dir = ROOT / 'dist'
    output_dir.mkdir(exist_ok=True)
    if args.include_data:
        # This is an explicitly requested private migration bundle, not a public release.
        content['.env.server'] = (ROOT / '.env.server').read_bytes()
        source_dir = ROOT / SERVICE / 'data'
        databases = sorted(p for p in source_dir.rglob('*') if p.is_file()
                           and p.suffix.lower() in {'.db', '.sqlite', '.sqlite3'})
        if not databases:
            raise RuntimeError('No SQLite databases found; refusing an empty data bundle')
        report = {'createdAtUtc': datetime.now(timezone.utc).isoformat(),
                  'method': 'SQLite online backup from read-only source connections',
                  'databases': []}
        with tempfile.TemporaryDirectory(prefix='sqlite-backup-', dir=output_dir) as folder:
            backup_dir = Path(folder).resolve()
            assert backup_dir.is_relative_to(output_dir.resolve())
            for number, source in enumerate(databases):
                if source.is_symlink():
                    raise RuntimeError('Refusing a symbolic-link database')
                backup = backup_dir / f'{number}.db'
                started = time.monotonic()

                def progress(status, remaining, total):
                    if time.monotonic() - started > 60:
                        raise TimeoutError('SQLite backup exceeded 60 seconds')

                with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as origin:
                    with closing(sqlite3.connect(backup)) as destination:
                        origin.backup(destination, pages=256, progress=progress)
                        integrity = [row[0] for row in destination.execute('PRAGMA integrity_check')]
                        if integrity != ['ok']:
                            raise RuntimeError(f'SQLite integrity failed for {source.name}')
                        tables = [r[0] for r in destination.execute(
                            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
                        counts = {table: destination.execute(
                            'SELECT COUNT(*) FROM "' + table.replace('"', '""') + '"'
                        ).fetchone()[0] for table in tables}
                relative = source.relative_to(source_dir).as_posix()
                data = backup.read_bytes()
                content['data/' + relative] = data
                report['databases'].append({'file': 'data/' + relative,
                    'integrity': 'ok', 'tableRows': counts, 'bytes': len(data),
                    'sha256': hashlib.sha256(data).hexdigest()})
            # Include other persisted assets, excluding transient SQLite sidecars.
            for path in sorted(source_dir.rglob('*')):
                if path.is_file() and path not in databases:
                    if path.is_symlink() or path.name.endswith(('-wal', '-shm', '-journal')):
                        continue
                    content['data/' + path.relative_to(source_dir).as_posix()] = path.read_bytes()
        content['docs/respaldo_sqlite.json'] = (json.dumps(report, indent=2) + '\n').encode()
        content['LEEME_DESPLIEGUE.txt'] = (
            'PAQUETE PRIVADO COMPLETO: contiene bases de datos y configuracion local.\n'
            '1. Extraer en una carpeta nueva; no sobrescribir una instalacion existente.\n'
            '2. Editar .env.server: SIMULATOR_BIND_IP debe ser la IP privada del servidor.\n'
            '3. SIMULATOR_DB_FILE=simulator.db usa la base general; para AnaPath usar\n'
            '   SIMULATOR_DB_FILE=anapath-simulator.db. No se fusionan las bases.\n'
            '4. Revisar destinos HIS/Mirth; las direcciones actuales se conservan.\n'
            '5. docker compose --env-file .env.server -f docker-compose.server.yml up -d --build\n'
            '6. Abrir http://IP_PRIVADA:8090 (o puerto configurado).\n'
            'Sin contrato HL7 completo el equipo permanece en STANDBY.\n'
            'Guia completa: docs/DESPLIEGUE_SERVIDOR.md\n'
            'Imagen Docker no incluida: la construccion requiere acceso a las dependencias.\n'
        ).encode()
    manifest = ''.join(f'{hashlib.sha256(data).hexdigest()}  {name}\n'
                       for name, data in sorted(content.items())).encode()
    content['MANIFEST.sha256'] = manifest
    filename = ('hl7-simulator-server-completo.tar.gz' if args.include_data
                else 'hl7-simulator-server.tar.gz')
    output = output_dir / filename
    with tarfile.open(output, 'w:gz', format=tarfile.PAX_FORMAT) as archive:
        for name, data in sorted(content.items()):
            info = tarfile.TarInfo(f'{PREFIX}/{name}')
            private = name == '.env.server' or name.startswith('data/')
            info.size, info.mode, info.mtime = len(data), (0o600 if private else 0o644), 0
            archive.addfile(info, io.BytesIO(data))
    with tarfile.open(output, 'r:gz') as archive:
        assert len(archive.getmembers()) == len(content)
        for name, data in content.items():
            assert archive.extractfile(f'{PREFIX}/{name}').read() == data
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_name(output.name + '.sha256').write_text(
        f'{digest}  {output.name}\n', encoding='ascii')
    print(f'{output.name}: {len(content)} verified files, {output.stat().st_size} bytes')
    print(f'SHA256: {digest}')


if __name__ == '__main__':
    main()
