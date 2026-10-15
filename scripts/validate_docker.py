"""Validate Compose publications without starting containers or reading live .env."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
POOL = set(range(5100, 5120))
STANDARD = POOL | set(range(5001, 5005)) | {5010, 5011, 5020, 9500}


def compose(files, example, overrides=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith((
        'SIMULATOR_', 'ANALYZER_', 'ANAPATH_', 'ORDER_', 'RESULT_', 'PACS_',
        'COMPOSE_', 'HOST_MIDDLEWARE_'))}
    env.update(overrides or {})
    command = ['docker', 'compose', '--env-file', example]
    for file in files:
        command += ['-f', file]
    return subprocess.run(command + ['config', '--format', 'json'], cwd=ROOT,
                          env=env, capture_output=True, text=True, timeout=30)


def expand(spec):
    ports = set()
    for item in spec.split(','):
        bounds = [int(n) for n in item.split('-')]
        ports.update(range(bounds[0], bounds[-1] + 1))
    return ports


def validate(files, example, service, expected, overrides=None):
    result = compose(files, example, overrides)
    if result.returncode:
        raise RuntimeError(result.stderr)
    config = json.loads(result.stdout)
    app = config['services'][service]
    publications = app['ports']
    allowed = expand(app['environment']['SIMULATOR_ALLOWED_LISTEN_PORTS'])
    assert allowed == expected, (files, 'unexpected allowed ports')
    assert {p['target'] for p in publications} == expected | {8090}
    assert len(publications) == len(expected) + 1, 'duplicate publication'
    assert all(p['protocol'] == 'tcp' for p in publications)
    assert any(v['type'] == 'bind' and v['target'] == '/app/data'
               for v in app['volumes']), 'missing persistence'
    if files == ['docker-compose.server.yml']:
        assert set(config['services']) == {service}, 'unexpected companion service'
        ip = (overrides or {}).get('SIMULATOR_BIND_IP', '127.0.0.1')
        ui_port = int((overrides or {}).get('ANALYZER_SIMULATOR_PORT', 8090))
        assert all(p['host_ip'] == ip for p in publications)
        assert all(int(p['published']) == (ui_port if p['target'] == 8090 else p['target'])
                   for p in publications)
        assert app['environment']['ORDER_LISTENERS_ENABLED'] == 'true'
        assert app['environment']['SIMULATOR_DB_PATH'] == '/app/data/' + (
            overrides or {}).get('SIMULATOR_DB_FILE', 'simulator.db')
        assert app['restart'] == 'unless-stopped'
        assert app['healthcheck']['retries'] == 3
        assert app['logging']['options'] == {'max-size': '10m', 'max-file': '3'}
    return {'files': files, 'status': 'PASS', 'listenerPorts': sorted(expected),
            'publishedPorts': len(publications), 'overrides': overrides or {}}


def main():
    records = [
        validate(['docker-compose.yml'], '.env.example', 'analyzer-simulator-api', STANDARD),
        validate(['docker-compose.yml', 'docker-compose.port-pool.yml'], '.env.example',
                 'analyzer-simulator-api', STANDARD),
        validate(['docker-compose.anapath.yml'], '.env.anapath.example',
                 'anapath-simulator', POOL | {9500}),
        validate(['docker-compose.server.yml'], '.env.server.example',
                 'analyzer-simulator-api', STANDARD),
        validate(['docker-compose.server.yml'], '.env.server.example',
                 'analyzer-simulator-api', STANDARD,
                 {'SIMULATOR_BIND_IP': '192.0.2.10', 'ANALYZER_SIMULATOR_PORT': '18090',
                  'SIMULATOR_DB_FILE': 'anapath-simulator.db'}),
    ]
    missing_ip = compose(['docker-compose.server.yml'], '.env.server.example',
                         {'SIMULATOR_BIND_IP': ''})
    assert missing_ip.returncode != 0 and 'SIMULATOR_BIND_IP' in missing_ip.stderr
    records.append({'case': 'empty bind IP rejected', 'status': 'PASS'})
    dockerfile = (ROOT / 'services/analyzer-simulator-api/Dockerfile').read_text()
    assert '"--workers", "1"' in dockerfile
    records.append({'case': 'one uvicorn worker', 'status': 'PASS'})
    report = {'generatedAt': datetime.now(timezone.utc).isoformat(),
              'scope': 'Compose configuration only; no containers started',
              'containerBuildVerified': False, 'containerRuntimeVerified': False,
              'checks': records}
    output = ROOT / 'docs/validacion_docker.json'
    output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(f'{len(records)} deployment checks passed; {output.name}')


if __name__ == '__main__':
    main()
