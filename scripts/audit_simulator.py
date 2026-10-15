"""Run the current synthetic regression suite and save reproducible evidence.

The original audit remains in docs/resultado_auditoria_simulador.json.
Run from the repository root with the application dev dependencies installed.
"""
import argparse
import contextlib
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import platform
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / 'services/analyzer-simulator-api'


class RecordedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.records = []

    def startTest(self, test):
        super().startTest(test)
        self.started = time.monotonic()
        self.current = {'test': test.id(), 'status': 'PASS', 'subtests': []}

    def addFailure(self, test, error):
        self.current['status'] = 'FAIL'
        super().addFailure(test, error)

    def addError(self, test, error):
        self.current['status'] = 'ERROR'
        super().addError(test, error)

    def addSkip(self, test, reason):
        self.current.update(status='SKIP', reason=reason)
        super().addSkip(test, reason)

    def addSubTest(self, test, subtest, error):
        self.current['subtests'].append({'case': str(subtest.params), 'status': 'FAIL' if error else 'PASS'})
        if error:
            self.current['status'] = 'FAIL'
        super().addSubTest(test, subtest, error)

    def stopTest(self, test):
        self.current['seconds'] = round(time.monotonic() - self.started, 4)
        self.records.append(self.current)
        super().stopTest(test)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT / 'docs/resultado_validacion_hl7.json'))
    args = parser.parse_args()
    os.chdir(SERVICE)
    sys.path.insert(0, str(SERVICE))
    suite = unittest.defaultTestLoader.discover(str(SERVICE / 'tests'))
    log = io.StringIO()
    with contextlib.redirect_stderr(log):
        result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=RecordedResult).run(suite)
    target = Path(args.output).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.with_suffix('.log').write_text(log.getvalue(), encoding='utf-8')
    evidence = {
        'scope': 'HL7 configurable: standby, ACK, manual processing, configured results, corrected-contract resend and published ports',
        'timestampUtc': datetime.now(timezone.utc).isoformat(),
        'python': platform.python_version(),
        'testsRun': result.testsRun, 'successful': result.wasSuccessful(),
        'failures': len(result.failures), 'errors': len(result.errors), 'skipped': len(result.skipped),
        'hospitalServicesUsed': False, 'dockerDeploymentPerformed': False,
        'tests': result.records,
    }
    target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Tests: {result.testsRun}; failures: {len(result.failures)}; errors: {len(result.errors)}; skipped: {len(result.skipped)}')
    print(f'Evidence: {target}')
    if not result.wasSuccessful():
        print(log.getvalue())
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
