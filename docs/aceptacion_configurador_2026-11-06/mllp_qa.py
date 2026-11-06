"""Peer MLLP de aceptación. No configura el simulador ni escribe en su base.

python mllp_qa.py fixtures
python mllp_qa.py listen --port 5125 --folder /app/data/acceptance-mllp
python mllp_qa.py send --port 5110 --file HEM_CBC-entrada.hl7
"""
import argparse
import json
from pathlib import Path
import socket
import socketserver
import threading

ROOT = Path(__file__).resolve().parent
CASES = {
    'HEM_CBC': ('Hemograma', [('WBC', 'Leucocitos', '10*3/uL', 4, 10), ('HGB', 'Hemoglobina', 'g/dL', 12, 17)]),
    'HEM_DIFF': ('Diferencial', [('NEUT', 'Neutrofilos', '%', 40, 70), ('LYMP', 'Linfocitos', '%', 20, 45)]),
    'CHEM_BASIC': ('Quimica basica', [('GLU', 'Glucosa', 'mg/dL', 70, 100), ('CREA', 'Creatinina', 'mg/dL', 0.6, 1.2)]),
    'IMM_TSH': ('Perfil tiroideo', [('TSH', 'Tirotropina', 'mIU/L', 0.4, 4), ('FT4', 'Tiroxina libre', 'ng/dL', 0.8, 1.8)]),
}


def msh(kind, control):
    return f'MSH|^~\\&|QA-LIS|QA|QA-SIM|QA|20261106120000||{kind}|{control}|T|2.5.1'


def incoming(code, suffix='', unknown=False):
    key = code + suffix
    return '\r'.join([msh('ORM^O01', 'MSG-' + key),
        f'PID|||QA-P-{key}||SINTETICO^PRUEBA||20000101|U',
        f'ORC|NW|P-{key}||O-{key}^QA',
        f'OBR|1|P-{key}||{"QA_DESCONOCIDO" if unknown else code}^{CASES[code][0]}^L',
        f'SPM|1|S-{key}||BLD^Sangre^L', 'ZIN|SEGMENTO-ADICIONAL|ACEPTACION']) + '\r'


def outgoing(code):
    name, parameters = CASES[code]
    lines = [msh('ORU^R01', 'EXAMPLE-' + code),
        'PID|||QA-EJEMPLO||SINTETICO^EJEMPLO', 'ORC|RE|P-EJEMPLO|F-EJEMPLO^QA',
        f'OBR|1|P-EJEMPLO|F-EJEMPLO^QA|{code}^{name}^L']
    lines.extend(f'OBX|{i}|NM|{p[0]}^{p[1]}^L||{p[3]}|{p[2]}' for i, p in enumerate(parameters, 1))
    return '\r'.join(lines) + '\r'


def read_frame(connection):
    data = b''
    while b'\x1c\r' not in data:
        chunk = connection.recv(8192)
        if not chunk:
            raise ConnectionError('Trama MLLP incompleta')
        data += chunk
    return data[1:data.index(b'\x1c')].decode('utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['fixtures', 'listen', 'send'])
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=5125)
    parser.add_argument('--file', type=Path)
    parser.add_argument('--folder', type=Path, default=ROOT / 'evidencia-mllp')
    args = parser.parse_args()
    if args.mode == 'fixtures':
        for code in CASES:
            for kind, message in [('entrada', incoming(code)), ('salida', outgoing(code)),
                                  ('desconocido', incoming(code, '-UNKNOWN', True))]:
                (ROOT / f'{code}-{kind}.hl7').write_bytes(message.encode())
        (ROOT / 'ADT-entrada.hl7').write_bytes((msh('ADT^A01', 'QA-ADT-1') + '\rPID|||QA-ADT||SINTETICO^ADT\rZEX|EXTRA\r').encode())
        print('Ejemplos sinteticos generados')
    elif args.mode == 'send':
        raw = args.file.read_bytes()
        with socket.create_connection((args.host, args.port), timeout=10) as connection:
            connection.settimeout(10)
            connection.sendall(b'\x0b' + raw + b'\x1c\r')
            response = read_frame(connection)
        args.folder.mkdir(parents=True, exist_ok=True)
        (args.folder / (args.file.stem + '-ack.hl7')).write_bytes(response.encode())
        print(json.dumps({'file': args.file.name, 'port': args.port, 'ack': response}))
        if 'MSA|AA|' not in response:
            raise SystemExit(1)
    else:
        args.folder.mkdir(parents=True, exist_ok=True)
        lock = threading.Lock()
        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(10)
                raw = read_frame(self.request)
                control = raw.split('\r')[0].split('|')[9]
                with lock:
                    path = args.folder / (control.replace('/', '_') + '.hl7')
                    path.write_bytes(raw.encode())
                ack = msh('ACK^R01', 'ACK-' + control) + '\rMSA|AA|' + control + '\r'
                self.request.sendall(b'\x0b' + ack.encode() + b'\x1c\r')
                print(json.dumps({'received': control, 'file': str(path), 'ack': 'AA'}), flush=True)
        class Server(socketserver.ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True
        with Server((args.host, args.port), Handler) as server:
            print(f'QA MLLP {args.host}:{args.port}', flush=True)
            server.serve_forever()


if __name__ == '__main__':
    main()
