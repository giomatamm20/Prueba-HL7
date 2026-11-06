"""Verificación de aceptación por consultas GET; no modifica configuraciones."""
import argparse
import json
from pathlib import Path
import urllib.request

from mllp_qa import CASES

ROOT = Path(__file__).resolve().parent
EQUIPMENT = {'qa-hematologia': ['HEM_CBC', 'HEM_DIFF'],
             'qa-quimica-inmunologia': ['CHEM_BASIC', 'IMM_TSH']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://localhost:8090')
    parser.add_argument('--output', default='resultado.json')
    args = parser.parse_args()
    def get(path):
        with urllib.request.urlopen(args.url + path, timeout=30) as response:
            return json.load(response)
    checks = []
    def check(name, value, detail=None):
        checks.append({'prueba': name, 'pasa': bool(value), 'detalle': detail})
    orders = [o for o in get('/api/orders') if o['analyzerId'] in EQUIPMENT]
    messages = [m for m in get('/api/messages') if m.get('analyzerId') in EQUIPMENT]
    profiles = {}
    for equipment, codes in EQUIPMENT.items():
        profiles[equipment] = get(f'/api/analyzers/{equipment}/protocol-profiles')
        for code in codes:
            variants = [p for p in profiles[equipment] if [s['code'] for s in p.get('catalog', {}).get('studies', [])] == [code]]
            check(f'{code}: perfil completo persistido', variants and all(p.get('inbound', {}).get('schema') and p.get('outbound', {}).get('events') for p in variants))
            check(f'{code}: recepción permisiva', variants and all(p['inbound'].get('receptionMode') == 'PERMISSIVE' and p['inbound'].get('acceptUnknownTests') for p in variants))
            expected = {p[0]: p for p in CASES[code][1]}
            known = next((o for o in orders if o.get('orderId') == 'O-' + code), None)
            check(f'{code}: orden completada', known and known['status'] == 'COMPLETED')
            check(f'{code}: generadores y valores', known and {r['code'] for r in known['results']} == set(expected) and all(expected[r['code']][3] <= r['value'] <= expected[r['code']][4] and r['unit'] == expected[r['code']][2] for r in known['results']))
            successful = [m for m in messages if m['direction'] == 'OUT' and m.get('sampleId') == 'S-' + code and m.get('ackCode') == 'AA' and not m.get('error')]
            check(f'{code}: salida con ACK AA', successful)
            raw = successful[0]['rawMessage'] if successful else ''
            segments = [s.split('|') for s in raw.split('\r') if s]
            obx = [s for s in segments if s[0] == 'OBX']
            check(f'{code}: ORU y analitos correctos', segments and 'ORU^R01' in segments[0][8] and {s[3].split('^')[0] for s in obx} == set(expected))
            check(f'{code}: correlación ORC-4.1 devuelta en ORC-2/OBR-2', any(s[0] == 'ORC' and s[2] == 'O-' + code for s in segments) and any(s[0] == 'OBR' and s[2] == 'O-' + code for s in segments))
            check(f'{code}: segmento agregado desde UI', ['ZQA', code] in segments)
            check(f'{code}: plantilla del perfil asociado a la orden', successful and successful[0].get('profileId') == known.get('catalogProfileId'))
            generated = {r['code']: r for r in known['results']} if known else {}
            check(f'{code}: valores y unidades reales en OBX', bool(obx) and all(s[2] == 'NM' and s[5] == str(generated[s[3].split('^')[0]]['hl7Value']) and s[6] == generated[s[3].split('^')[0]]['unit'] for s in obx))
            capture = ROOT / 'capturas-receptor' / ((successful[0]['messageControlId'] if successful else 'missing') + '.hl7')
            check(f'{code}: captura TCP coincide con mensaje registrado', capture.exists() and capture.read_bytes().decode('utf-8') == raw)
            incoming = [m for m in messages if m['direction'] == 'IN' and m.get('messageControlId') == 'MSG-' + code]
            check(f'{code}: recepción MLLP AA', incoming and all(m.get('ackCode') == 'AA' for m in incoming))
            unknown = next((o for o in orders if o.get('orderId') == 'O-' + code + '-UNKNOWN'), None)
            check(f'{code}: desconocido recibido y pendiente', unknown and unknown.get('pendingCatalogStudies') == ['QA_DESCONOCIDO'] and not unknown['results'])
            unknown_ack = [m for m in messages if m['direction'] == 'IN' and m.get('messageControlId') == 'MSG-' + code + '-UNKNOWN']
            check(f'{code}: desconocido ACK AA', unknown_ack and all(m.get('ackCode') == 'AA' for m in unknown_ack))
        adt = [m for m in messages if m['analyzerId'] == equipment and m.get('messageType') == 'ADT^A01']
        check(f'{equipment}: otro tipo HL7 recibido', adt and all(m.get('ackCode') == 'AA' and not m.get('error') for m in adt))
    check('Listeners sin fallo', not get('/api/health').get('failedListeners'))
    before_path = ROOT / 'resultado-antes-reinicio.json'
    if args.output != before_path.name and before_path.exists():
        before = json.loads(before_path.read_text(encoding='utf-8'))
        same = True
        for equipment, old_profiles in before['perfiles'].items():
            for old in old_profiles:
                current = next((p for p in profiles[equipment] if p['id'] == old['id']), None)
                same = same and current is not None and all(current.get(k) == old.get(k) for k in ('name', 'inbound', 'outbound', 'catalog'))
        check('Perfiles completos sin cambios después de reconstruir Docker', same)
    report = {'pasa': all(c['pasa'] for c in checks), 'aprobadas': sum(c['pasa'] for c in checks),
              'total': len(checks), 'pruebas': checks,
              'ordenes': [{'orderId': o.get('orderId'), 'analyzerId': o['analyzerId'], 'status': o['status'], 'catalogProfileId': o.get('catalogProfileId'), 'resultados': o['results']} for o in orders],
              'perfiles': profiles, 'mensajes': messages}
    (ROOT / args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('pasa', 'aprobadas', 'total')}))
    for c in checks:
        if not c['pasa']:
            print('NO PASA: ' + c['prueba'])
    raise SystemExit(not report['pasa'])


if __name__ == '__main__':
    main()
