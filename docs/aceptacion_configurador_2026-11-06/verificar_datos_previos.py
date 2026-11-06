"""Comprueba solo los registros existentes antes del pase (SQLite de lectura)."""
import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--manifest', default='/app/data/acceptance-before-20261106.json')
parser.add_argument('--database', default='/app/data/simulator.db')
args = parser.parse_args()
baseline = json.loads(Path(args.manifest).read_text(encoding='utf-8'))['liveBefore']
keys = ('id', 'name', 'vendor', 'protocol', 'transport', 'profile', 'listenPort', 'mode',
        'resultDestinationHost', 'resultDestinationPort', 'supportedTests', 'behavior',
        'catalogRevisionId', 'unknownStudyPolicy', 'genericStudyCode', 'acceptUnknownTests')
checks = {}
with sqlite3.connect(f'file:{args.database}?mode=ro', uri=True) as connection:
    for table, originals in baseline.items():
        valid = True
        for identity, expected in originals.items():
            row = connection.execute(f'SELECT payload FROM {table} WHERE id=?', (identity,)).fetchone()
            if row is None:
                valid = False
                continue
            payload = json.loads(row[0])
            if table == 'analyzers':
                payload = {key: payload.get(key) for key in keys}
            digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            valid = valid and digest == expected
        checks[table] = {'pasa': valid, 'registros_previos': len(originals)}
print(json.dumps({'pasa': all(v['pasa'] for v in checks.values()), 'tablas': checks}, indent=2))
raise SystemExit(not all(v['pasa'] for v in checks.values()))
