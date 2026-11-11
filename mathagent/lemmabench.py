# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
# Copyright (c) 2026 Vincent Boulard, Louis Carillo, and contributors

"""Offline LemmaBench preparation and lossless answer export; never run a model.

Only the statement-only dataset is solver input. Original records, including
reference-bearing fields, remain in a separate evaluation directory. Empty or
unfinished answers go to the failure ledger, never an empty proof_steps list.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import random
import re

from .benchmark import load_manifest

MAX_BYTES = 64 * 1024 * 1024
FIELDS = ('lemma', 'assumptions', 'filename', 'lemma_id', 'mode', 'extract_model',
          'domain', 'judge_verdicts')
FAILURE_STATUSES = {'error', 'timeout', 'interrupted', 'cancelled', 'running', 'paused',
                    'needs_recovery', 'budget_violation', 'empty_output', 'truncated'}


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _bytes(path):
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError(f'Expected a regular non-symlink file up to {MAX_BYTES} bytes: {path.name}')
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        raise ValueError(f'File grew beyond the size limit: {path.name}')
    return raw


def _decode(text):
    # Dataset mathematics must not pass through model-output TeX repair.
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key: ' + key)
            result[key] = value
        return result
    def finite(value):
        raise ValueError('Nonfinite JSON value: ' + value)
    def number(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            finite(value)
        return parsed
    return json.loads(text, object_pairs_hook=unique, parse_constant=finite, parse_float=number)


def _rows(raw):
    rows = []
    for number, line in enumerate(raw.decode('utf-8').splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = _decode(line)
        except (ValueError, RecursionError) as exc:
            raise ValueError(f'Invalid JSONL at source line {number}: {exc}') from exc
        if not isinstance(value, dict):
            raise ValueError(f'Expected an object at source line {number}')
        rows.append((number, value))
    return rows


def _json(raw):
    return (json.dumps(raw, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode('utf-8')


def _jsonl(rows):
    return ''.join(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n' for row in rows).encode('utf-8')


def _new_directory(path):
    path = Path(path).absolute()
    if path.exists() or path.is_symlink():
        raise ValueError('Output already exists; choose a new directory')
    package = Path(__file__).resolve().parent.parent
    if (path.parent.resolve() / path.name).is_relative_to(package):
        raise ValueError('Keep benchmark datasets and exports outside the repository')
    path.mkdir(parents=True, mode=0o700, exist_ok=False)
    return path


def _write(path, raw):
    with path.open('xb') as stream:
        stream.write(raw)
    path.chmod(0o600)


def _inside(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Artifact path escapes its owning directory')
    path = root
    for part in relative.parts:
        path /= part
        if path.is_symlink():
            raise ValueError('Symlink artifact refused')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Artifact path escapes its owning directory')
    return path


def _statement(row):
    if row['assumptions'] in row['lemma']:
        return row['lemma'], 'lemma_only'
    return ('ASSUMPTIONS:\n' + row['assumptions'] + '\n\nSTATEMENT:\n' + row['lemma'],
            'assumptions_and_lemma')


def _identity(row):
    identity = json.dumps([row['filename'], row['lemma_id'], row['mode']], ensure_ascii=False)
    return 'L' + _sha(identity.encode('utf-8'))


def prepare(input_path, output, *, selection, exclude_lines=(), name='LemmaBench'):
    """Freeze canonical input and an explicitly selected, statement-only manifest."""
    if selection not in {'all-accepted', 'all'}:
        raise ValueError('Choose selection all-accepted or all explicitly')
    raw = _bytes(input_path)
    rows = _rows(raw)
    if not rows or len(rows) > 1000:
        raise ValueError('Provide 1–1000 LemmaBench records')
    exclusions = set(exclude_lines)
    if any(type(i) is not int or i < 1 for i in exclusions) or not exclusions <= {n for n, _ in rows}:
        raise ValueError('Each excluded source line must identify an existing JSONL record')
    metadata, statements, decisions, identities = [], {}, [], set()
    compositions = Counter()
    for number, row in rows:
        for field in FIELDS:
            if field not in row:
                raise ValueError(f'Missing {field} at source line {number}')
        if any(not isinstance(row[k], str) or '\x00' in row[k] for k in FIELDS if k not in {'lemma_id', 'judge_verdicts'}):
            raise ValueError(f'Expected text fields without NUL at source line {number}')
        if not row['lemma'].strip() or not row['filename'] or not row['mode']:
            raise ValueError(f'Empty lemma, filename or mode at source line {number}')
        if type(row['lemma_id']) not in (int, str):
            raise ValueError(f'Invalid lemma_id at source line {number}')
        verdicts = row['judge_verdicts']
        if not isinstance(verdicts, dict) or any(type(v) is not bool for v in verdicts.values()):
            raise ValueError(f'Expected boolean self-containedness verdicts at source line {number}')
        problem_id = _identity(row)
        if problem_id in identities:
            raise ValueError(f'Duplicate source identity at source line {number}')
        identities.add(problem_id)
        accepted = bool(verdicts) and all(verdicts.values())
        reason = ('operator_excluded' if number in exclusions else
                  'self_containedness_not_all_true' if selection == 'all-accepted' and not accepted else None)
        decisions.append({'source_line': number, 'problem_id': problem_id, 'accepted': accepted,
                          'selected': reason is None, 'exclusion_reason': reason})
        if reason:
            continue
        if any(re.search(r'\\begin\s*\{\s*proof\*?\s*\}', row[k]) for k in ('lemma', 'assumptions')):
            raise ValueError(f'Proof environment in solver input at source line {number} ({problem_id}); review it or explicitly use --exclude-line {number}. No text was stripped.')
        statement, composition = _statement(row)
        encoded = statement.encode('utf-8')
        if len(statement) > 59000 or len(encoded) > 240000:
            raise ValueError(f'Statement exceeds benchmark limits at source line {number}')
        statements[problem_id] = encoded
        compositions[composition] += 1
        metadata.append({'problem_id': problem_id, 'source_line': number,
                         'statement_sha256': _sha(encoded), 'composition': composition, 'record': row})
    if not statements:
        raise ValueError('The explicit selection contains no problems')
    manifest = {'version': 1, 'name': name, 'problems': [
        {'id': item['problem_id'], 'statement': 'problems/' + item['problem_id'] + '.md',
         'sha256': item['statement_sha256']} for item in metadata]}
    manifest_raw, metadata_raw = _json(manifest), _jsonl(metadata)
    report = {'version': 1, 'source_sha256': _sha(raw), 'manifest_sha256': _sha(manifest_raw),
              'metadata_sha256': _sha(metadata_raw), 'selection': selection,
              'input_records': len(rows), 'selected_records': len(metadata),
              'excluded_records': len(rows) - len(metadata), 'composition_counts': dict(compositions),
              'decisions': decisions}
    destination = _new_directory(output)
    (destination / 'dataset' / 'problems').mkdir(parents=True, mode=0o700)
    (destination / 'evaluation').mkdir(mode=0o700)
    for identifier, statement in statements.items():
        _write(destination / 'dataset' / 'problems' / (identifier + '.md'), statement)
    _write(destination / 'dataset' / 'manifest.json', manifest_raw)
    _write(destination / 'evaluation' / 'source.jsonl', raw)
    _write(destination / 'evaluation' / 'metadata.jsonl', metadata_raw)
    _write(destination / 'preparation.json', _json(report))
    return report


def _prepared(directory):
    root = Path(directory).resolve(strict=True)
    report = _decode(_bytes(_inside(root, 'preparation.json')).decode('utf-8'))
    for relative, key in (('evaluation/source.jsonl', 'source_sha256'),
                          ('evaluation/metadata.jsonl', 'metadata_sha256'),
                          ('dataset/manifest.json', 'manifest_sha256')):
        if _sha(_bytes(_inside(root, relative))) != report[key]:
            raise ValueError('Prepared input digest changed: ' + relative)
    data = load_manifest(_inside(root, 'dataset/manifest.json'))
    source = dict(_rows(_bytes(_inside(root, 'evaluation/source.jsonl'))))
    metadata = {}
    for _, item in _rows(_bytes(_inside(root, 'evaluation/metadata.jsonl'))):
        identifier = item['problem_id']
        if identifier in metadata:
            raise ValueError('Duplicate prepared problem identity')
        original = source.get(item['source_line'])
        if not isinstance(original, dict) or original != item['record'] or _identity(original) != identifier:
            raise ValueError('Prepared metadata differs from the canonical source record')
        metadata[identifier] = item
    if set(metadata) != {p['id'] for p in data['problems']}:
        raise ValueError('Prepared metadata does not match the statement manifest')
    for problem in data['problems']:
        item = metadata[problem['id']]
        if (problem['text'] != _statement(item['record'])[0]
                or problem['sha256'] != item['statement_sha256']):
            raise ValueError('Prepared statement differs from its canonical mathematics')
    return report, data, metadata


def _candidate_answer(run, job_id, record, stage, goal, model):
    name = 'initial_candidate' if stage == 'proof-initial' else 'selected_candidate'
    candidate = record.get(name)
    if not candidate:
        return '', False, name + '_missing'
    proofs = _inside(run, f'jobs/{job_id}/workspace/.mathagent/proofs')
    directories = list(proofs.iterdir()) if proofs.is_dir() else []
    if len(directories) != 1 or directories[0].is_symlink():
        raise ValueError('Expected one immutable Proof job for ' + job_id)
    state = _decode(_bytes(_inside(directories[0], 'state.json')).decode('utf-8'))
    if state.get('goal') != goal:
        raise ValueError('Proof state belongs to a different mathematical statement: ' + job_id)
    if state.get('settings', {}).get('model') != model:
        raise ValueError('Proof state belongs to a different model: ' + job_id)
    saved = next((c for c in state['candidates'] if c['id'] == state[name]), None)
    if not saved or any(saved.get(k) != candidate.get(k) for k in ('id', 'artifact', 'sha256', 'transport_complete')):
        raise ValueError('Candidate selection differs from its saved Proof state: ' + job_id)
    raw = _bytes(_inside(directories[0], 'artifacts/' + candidate['artifact']))
    if _sha(raw) != candidate['sha256']:
        raise ValueError('Candidate digest changed: ' + job_id)
    return raw.decode('utf-8'), candidate.get('transport_complete') is True, name + '_unfinished'


def export(run, prepared, output, *, include_proof_initial=False, blind=False):
    """Export selected written answers, with complete planned-job failure coverage."""
    report, data, metadata = _prepared(prepared)
    run = Path(run).resolve(strict=True)
    plan = _decode(_bytes(_inside(run, 'plan.json')).decode('utf-8'))
    if plan['manifest']['sha256'] != report['manifest_sha256']:
        raise ValueError('Run uses a different prepared manifest')
    if {p['id']: p['sha256'] for p in plan['problems']} != {p['id']: p['sha256'] for p in data['problems']}:
        raise ValueError('Run statement hashes do not match prepared inputs')
    settings = plan['settings']
    arms, replicates = settings['arms'], settings['replicates']
    if (not isinstance(arms, list) or not arms or len(set(arms)) != len(arms)
            or any(a not in {'raw-single', 'proof'} for a in arms)
            or type(replicates) is not int or not 1 <= replicates <= 100):
        raise ValueError('Invalid planned arms or replicate count')
    expected = {f'{p["id"]}--r{rep}--{arm}': (p['id'], arm, rep)
                for p in data['problems'] for rep in range(1, replicates + 1) for arm in arms}
    results_path = _inside(run, 'outputs.jsonl')
    records = {}
    for _, record in _rows(_bytes(results_path)) if results_path.exists() else []:
        if record.get('id') not in expected or record['id'] in records:
            raise ValueError('Unexpected or duplicate benchmark result identity')
        records[record['id']] = record
    rows, coverage, failures, key = [], [], [], {}
    for job_id, (problem_id, arm, replicate) in expected.items():
        record = records.get(job_id)
        goal = _statement(metadata[problem_id]['record'])[0]
        if record and (record.get('problem_id'), record.get('arm'), record.get('replicate')) != (problem_id, arm, replicate):
            raise ValueError('Result fields contradict planned job identity')
        if record and record.get('statement_sha256') != metadata[problem_id]['statement_sha256']:
            raise ValueError('Result statement digest differs from prepared input')
        stages = [arm] + (['proof-initial'] if arm == 'proof' and include_proof_initial else [])
        for stage in stages:
            status = record.get('status', 'missing_result') if record else 'missing_result'
            text, complete, reason = '', False, 'missing_result'
            if record:
                if stage == 'proof-initial':
                    text, complete, reason = _candidate_answer(run, job_id, record, stage, goal, settings['model'])
                else:
                    path = _inside(run, f'jobs/{job_id}/answer.md')
                    if path.is_file():
                        raw = _bytes(path)
                        if _sha(raw) != record.get('answer_sha256'):
                            raise ValueError('Written answer digest changed: ' + job_id)
                        text, complete = raw.decode('utf-8'), record.get('transport_complete') is True
                        reason = 'empty_answer' if not text.strip() else 'unfinished_answer'
                        if arm == 'proof' and text.strip() and complete and status not in FAILURE_STATUSES:
                            saved, saved_complete, _ = _candidate_answer(run, job_id, record, stage, goal, settings['model'])
                            if text != saved or complete != saved_complete:
                                raise ValueError('Export differs from the engine-selected candidate: ' + job_id)
                        if arm == 'raw-single' and text.strip() and complete and status not in FAILURE_STATUSES:
                            request = _decode(_bytes(_inside(run, f'jobs/{job_id}/raw-0/request.json')).decode('utf-8'))
                            expected_messages = [{'role': 'user', 'content': plan['common_instruction'] + '\n\nSTATEMENT:\n' + goal}]
                            if request.get('model') != settings['model'] or request.get('messages') != expected_messages:
                                raise ValueError('Raw request belongs to a different statement or model: ' + job_id)
                    else:
                        reason = 'answer_missing'
            eligible = bool(text.strip()) and complete and (stage == 'proof-initial' or status not in FAILURE_STATUSES)
            item = {'job_id': job_id, 'problem_id': problem_id, 'arm': arm, 'stage': stage,
                    'replicate': replicate, 'status': status, 'exported': eligible}
            if not eligible:
                item['reason'] = 'operational_failure' if status in FAILURE_STATUSES else reason
                failures.append(item)
            else:
                original = metadata[problem_id]['record']
                native = {field: original[field] for field in FIELDS}
                native.update(proof_steps=[text], proof_model=('anonymous' if blind else
                    settings['model'] + (' + SquareHarness Proof' if stage == 'proof' else
                                         ' [Proof initial]' if stage == 'proof-initial' else ' [raw-single]')))
                rows.append((stage, native, item))
            coverage.append(item)
    random.Random(settings['seed']).shuffle(rows)
    native_files = {'proofs.jsonl': []} if blind else {stage + '.proofs.jsonl': [] for stage in
        list(arms) + (['proof-initial'] if include_proof_initial and 'proof' in arms else [])}
    for index, (stage, native, item) in enumerate(rows, 1):
        identifier = f'submission-{index:05d}'
        native['submission_id'] = identifier
        key[identifier] = item
        native_files['proofs.jsonl' if blind else stage + '.proofs.jsonl'].append(native)
    summary = {'version': 1, 'manifest_sha256': report['manifest_sha256'],
               'source_sha256': report['source_sha256'], 'planned_jobs': len(expected),
               'planned_submissions': len(coverage), 'exported_submissions': len(rows),
               'failed_or_missing_submissions': len(failures), 'blind': blind,
               'include_proof_initial': include_proof_initial,
               'per_stage': {stage: {'planned': sum(r['stage'] == stage for r in coverage),
                   'exported': sum(r['stage'] == stage and r['exported'] for r in coverage)}
                   for stage in sorted({r['stage'] for r in coverage})}, 'submissions': coverage}
    destination = _new_directory(output)
    for filename, native in native_files.items():
        _write(destination / filename, _jsonl(native))
    _write(destination / 'failures.jsonl', _jsonl(failures))
    _write(destination / 'grading-key.private.json', _json(key))
    _write(destination / 'coverage.json', _json(summary))
    return summary


def parser():
    p = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    commands = p.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare', allow_abbrev=False, help='Prepare statement-only inputs without inference')
    prep.add_argument('--input', type=Path, required=True)
    prep.add_argument('--output', type=Path, required=True)
    prep.add_argument('--selection', choices=('all-accepted', 'all'), required=True)
    prep.add_argument('--exclude-line', type=int, action='append', default=[], help='Explicit source-line exclusion, recorded in the preparation ledger')
    prep.add_argument('--name', default='LemmaBench')
    out = commands.add_parser('export', allow_abbrev=False, help='Export saved answers without inference or judging')
    out.add_argument('--run', type=Path, required=True)
    out.add_argument('--prepared', type=Path, required=True)
    out.add_argument('--output', type=Path, required=True)
    out.add_argument('--include-proof-initial', action='store_true')
    out.add_argument('--blind', action='store_true')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == 'prepare':
            result = prepare(args.input, args.output, selection=args.selection,
                             exclude_lines=args.exclude_line, name=args.name)
        else:
            result = export(args.run, args.prepared, args.output,
                            include_proof_initial=args.include_proof_initial, blind=args.blind)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit('LemmaBench: ' + str(exc)) from exc
    print(json.dumps({k: v for k, v in result.items() if k not in {'decisions', 'submissions'}}, indent=2))


if __name__ == '__main__':
    main()
