"""Join reviewed design mappings to executed JUnit evidence; never infer coverage."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def read_results(paths):
    results = []
    for path in paths:
        for test in ET.parse(path).getroot().iter('testcase'):
            parts = test.get('classname', '').split('.')
            module_end = next((i for i, part in enumerate(parts) if part.startswith('test_')), len(parts)-1)
            module = '/'.join(parts[:module_end+1]) + '.py'
            if parts[module_end+1:]:
                module += '::' + '::'.join(parts[module_end+1:])
            name = test.get('name', '')
            outcome = ('FAILED' if test.find('failure') is not None or test.find('error') is not None
                       else 'SKIPPED' if test.find('skipped') is not None else 'PASSED')
            results.append({'test': module + '::' + name, 'status': outcome, 'report': str(path)})
    return results


def matches(selector, test):
    return selector == test or ('[' not in selector and test.startswith(selector + '['))


def build_report(cases, mappings, junit_paths):
    ids = [case['id'] for case in cases]
    mapped = [row['id'] for row in mappings]
    if len(set(ids)) != len(ids) or len(set(mapped)) != len(mapped) or set(ids) != set(mapped):
        raise ValueError('Design and mapping IDs must match exactly, without duplicates: '
                         f'missing={sorted(set(ids)-set(mapped))}, extra={sorted(set(mapped)-set(ids))}')
    results = read_results(junit_paths)
    by_id = {row['id']: row for row in mappings}
    rows = []
    for case in cases:
        row = by_id[case['id']]
        coverage = row['coverage']
        if coverage not in {'full', 'partial', 'manual', 'superseded'} or not row.get('rationale', '').strip():
            raise ValueError(f"Invalid reviewed coverage for {case['id']}")
        if coverage == 'full' and row.get('remaining'):
            raise ValueError(f"Full coverage still has missing oracles: {case['id']}")
        selectors = row.get('tests', [])
        if not isinstance(selectors, list) or any(not isinstance(s, str) or '::' not in s for s in selectors):
            raise ValueError(f"Invalid test selectors for {case['id']}")
        evidence = [r for r in results if any(matches(s, r['test']) for s in selectors)]
        missing = [s for s in selectors if not any(matches(s, r['test']) for r in results)]
        # Different fixture jobs may skip then execute the same test instance.
        # Keep all evidence; a genuine failure is never hidden by a later pass.
        passed_instances = {r['test'] for r in evidence if r['status'] == 'PASSED'}
        unresolved_skips = [r for r in evidence if r['status'] == 'SKIPPED' and r['test'] not in passed_instances]
        if coverage == 'superseded':
            status = 'SUPERSEDED'
        elif any(r['status'] == 'FAILED' for r in evidence):
            status = 'FAILED'
        elif coverage == 'manual':
            status = 'BLOCKED'
        elif coverage == 'partial':
            status = 'PARTIAL'
        elif missing or not evidence:
            status = 'NOT_RUN'
        elif unresolved_skips:
            status = 'SKIPPED'
        else:
            status = 'PASSED'
        rows.append({**case, **row, 'status': status, 'missing_tests': missing, 'evidence': evidence})
    summary = dict(Counter(row['status'] for row in rows))
    regression_failed = any(r['status'] == 'FAILED' for r in results)
    return {'generated_at': datetime.now(timezone.utc).isoformat(), 'summary': summary,
            'regression_failed': regression_failed,
            'release_ready': not regression_failed and bool(summary.get('PASSED')) and all(r['status'] in {'PASSED', 'SUPERSEDED'} for r in rows),
            'cases': rows}


def markdown(report):
    lines = ['# Case-by-case acceptance results', '', 'Release ready: **' + str(report['release_ready']) + '**', '',
             'Generated: ' + report['generated_at'], '',
             'Counts: ' + ', '.join(f'{key}={value}' for key, value in report['summary'].items()), '',
             'Passing tests establish only the explicitly reviewed oracles. Partial coverage and hardware fixtures are not full acceptance.', '',
             '| Case | Status | Reviewed evidence / remaining work |', '| --- | --- | --- |']
    for row in report['cases']:
        detail = row['rationale'] + (' Remaining: ' + '; '.join(row['remaining']) if row.get('remaining') else '')
        if row['missing_tests']:
            detail += ' Not executed: ' + ', '.join(row['missing_tests'])
        lines.append('| ' + row['id'] + ' | ' + row['status'] + ' | ' + detail.replace('|', '\\|').replace('\n', ' ') + ' |')
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, action='append', required=True)
    parser.add_argument('--mapping', type=Path, action='append', required=True)
    parser.add_argument('--junit', type=Path, action='append', required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--require-complete', action='store_true')
    args = parser.parse_args()
    cases = [row for path in args.cases for row in json.loads(path.read_text())]
    mappings = [row for path in args.mapping for row in json.loads(path.read_text())]
    report = build_report(cases, mappings, args.junit)
    report['evidence_files'] = [{'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                                for path in args.cases + args.mapping + args.junit]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / 'case-results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    (args.output_dir / 'case-results.md').write_text(markdown(report))
    print(json.dumps({'summary': report['summary'], 'release_ready': report['release_ready']}))
    return 1 if args.require_complete and not report['release_ready'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
