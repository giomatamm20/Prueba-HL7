"""One equipment definition: reception, outbound templates and result generators.

The embedded catalog is versioned with its protocol profile. Legacy catalog
revisions are read only when opening an equipment that has not been migrated.
"""
import copy
import re

from .hl7_contract import infer_hl7_contract
from .hl7_text import normalize_hl7
from .study_catalog import dynamic_outbound, infer_studies, validate_catalog

SEMANTICS = {
    'PATIENT_IDENTIFIER': 'patient.id', 'FAMILY_NAME': 'patient.name', 'GIVEN_NAME': 'patient.name',
    'BIRTH_DATE': 'patient.birthDate', 'ADMINISTRATIVE_SEX': 'patient.sex', 'PATIENT_CLASS': 'visit.type',
    'PLACER_ORDER_NUMBER': 'order.id', 'FILLER_ORDER_NUMBER': 'order.fillerId',
    'SPECIMEN_IDENTIFIER': 'sample.id', 'SPECIMEN_TYPE_CODE': 'sample.code', 'SPECIMEN_TYPE_NAME': 'sample.name',
    'TEST_CODE': 'test.code', 'TEST_NAME': 'test.name', 'SENDING_APPLICATION': 'analyzer.id',
    'SENDING_FACILITY': 'analyzer.vendor', 'RECEIVING_APPLICATION': 'inbound.MSH-3',
    'RECEIVING_FACILITY': 'inbound.MSH-4', 'MESSAGE_DATETIME': 'system.timestamp',
    'MESSAGE_CONTROL_ID': 'system.messageId', 'SET_ID': 'counter', 'OBSERVATION_IDENTIFIER': 'result.code',
    'OBSERVATION_TEXT': 'result.name', 'OBSERVATION_VALUE': 'result.hl7Value', 'VALUE_TYPE': 'result.valueType',
    'UNITS': 'result.unit', 'REFERENCE_RANGE': 'result.referenceRange', 'ABNORMAL_FLAGS': 'result.abnormalFlag',
    'OBSERVATION_STATUS': 'result.status',
}


def walk_nodes(nodes):
    for node in nodes or []:
        yield node
        yield from walk_nodes(node.get('children', []))


def catalog_nodes(study):
    yield study
    for group in study.get('groups', []):
        yield from catalog_nodes(group)


def study_ready(study):
    parameters = [p for node in catalog_nodes(study) for p in node.get('parameters', []) if p.get('enabled', True)]
    return bool(parameters) and all(p.get('generator') for p in parameters)


def validate_embedded_catalog(catalog):
    if not isinstance(catalog, dict) or not isinstance(catalog.get('studies', []), list):
        raise ValueError('Los estudios del perfil deben ser una lista')
    result = copy.deepcopy(catalog)
    studies = result.setdefault('studies', [])
    if len(studies) > 200:
        raise ValueError('Máximo 200 estudios por equipo')
    seen = set()
    for study in studies:
        # Validate unfinished imports structurally using a temporary generator;
        # the persisted definition retains None and cannot generate results.
        candidate = copy.deepcopy(study)
        for node in catalog_nodes(candidate):
            for p in node.get('parameters', []):
                if p.get('generator') is None:
                    p['generator'] = {'kind': 'fixed', 'value': 0 if p.get('valueType') == 'NM' else
                        {'code': 'PENDING', 'name': 'Pendiente', 'system': 'L'} if p.get('valueType') in {'CE', 'CWE'} else 'PENDIENTE'}
        if not any(n.get('parameters') for n in catalog_nodes(candidate)):
            candidate['parameters'] = [{'code': '_PENDING', 'name': 'Pendiente', 'valueType': 'ST',
                                        'generator': {'kind': 'fixed', 'value': 'PENDIENTE'}}]
        validate_catalog({'name': 'Perfil del equipo', 'studies': [candidate]})
        for alias in [study['code'], *study.get('aliases', [])]:
            if alias in seen:
                raise ValueError('Código de estudio repetido en el perfil: ' + alias)
            seen.add(alias)
    return result


def schema_routes(schema):
    """Expand components and repetitions, preserving explicit structural constants."""
    result = []
    for segment in (schema or {}).get('segments', []):
        fields = []
        for number, field in segment.get('fields', {}).items():
            if segment['name'] == 'MSH' and str(number) == '2':
                continue
            for repetition in field.get('repetitions', []):
                index = repetition.get('index', 1)
                for component, rule in repetition.get('components', {}).items():
                    path = f"{segment['name']}-{number}"
                    if field.get('maxRepetitions') == '*' or len(field.get('repetitions', [])) > 1 or index > 1:
                        path += f'[{index}]'
                    if field.get('composite') or rule.get('subcomponents'):
                        path += '.' + str(component)
                    leaves = [(path + '.' + str(sub), leaf) for sub, leaf in rule['subcomponents'].items()] if rule.get('subcomponents') else [(path, rule)]
                    for target, leaf in leaves:
                        semantic = leaf.get('semantic')
                        mapping = {'target': target}
                        if 'const' in leaf:
                            mapping.update(sourceType='fixed', value=str(leaf['const']))
                        else:
                            source = leaf.get('source') or SEMANTICS.get(semantic) or 'inbound.' + target
                            if semantic in {'PATIENT_IDENTIFIER', 'FAMILY_NAME', 'GIVEN_NAME'} and index > 1:
                                source = 'inbound.' + target
                            mapping['source'] = source
                            if semantic in {'FAMILY_NAME', 'GIVEN_NAME'} and not leaf.get('source'):
                                mapping['transform'] = 'component1' if semantic == 'FAMILY_NAME' else 'component2'
                            if leaf.get('transform'):
                                mapping['transform'] = leaf['transform']
                            if semantic in {'VALUE_TYPE', 'OBSERVATION_STATUS', 'SENDING_FACILITY'} and leaf.get('example'):
                                mapping['default'] = str(leaf['example'])
                        fields.append(mapping)
        result.append({'name': segment['name'], 'fields': fields,
                       **({'repeatBy': 'results'} if segment['name'] == 'OBX' else {})})
    return result


def inbound_mappings(schema):
    mappings = {}
    for segment in schema_routes(schema):
        for field in segment['fields']:
            source = field.get('source', '')
            if source.startswith(('patient.', 'order.', 'sample.', 'test.', 'visit.', 'customFields.')):
                path = field['target']
                if source == 'patient.name':
                    path = path.split('.')[0]
                mapping = mappings.setdefault(source, {'target': source, 'source': path, 'alternatives': [], 'required': False})
                if path != mapping['source'] and path not in mapping['alternatives']:
                    mapping['alternatives'].append(path)
    return list(mappings.values())


def compile_result_template(profile):
    """Bind one observation template to all embedded analitos, retaining routes."""
    outbound = profile.setdefault('outbound', {})
    if not profile.get('catalog', {}).get('studies'):
        return profile
    events = outbound.setdefault('events', {})
    event = events.setdefault('RESULT_FINAL', copy.deepcopy(dynamic_outbound()['events']['RESULT_FINAL']))
    if any(f.get('source') == 'resultDocument.pdfBase64' for s in walk_nodes(event['segments']) for f in s.get('fields', [])):
        return profile
    segments = event['segments']
    if not any(s.get('repeatBy') == 'studies' for s in walk_nodes(segments)):
        group = copy.deepcopy(dynamic_outbound()['events']['RESULT_FINAL']['segments'][2])
        for name in ('OBR', 'SPM', 'OBX'):
            previous = [s for s in segments if s['name'] == name]
            target = group if name == 'OBR' else next(s for s in group['children'] if s['name'] == name)
            if previous:
                target['fields'] = copy.deepcopy(previous[0].get('fields', []))
                # Multiple observations in the example become catalog rows, not
                # multiple copies of the same outbound observation template.
                for other in previous[1:]:
                    for field in other.get('fields', []):
                        if not any(f['target'] == field['target'] for f in target['fields']):
                            target['fields'].append(copy.deepcopy(field))
                if previous[0].get('children'):
                    target['children'] = copy.deepcopy(previous[0]['children'])
        rebuilt, inserted = [], False
        for node in segments:
            if node['name'] in {'OBR', 'SPM', 'OBX'}:
                if not inserted:
                    rebuilt.append(group)
                    inserted = True
            else:
                rebuilt.append(node)
        if not inserted:
            rebuilt.append(group)
        event['segments'] = rebuilt
    for segment in walk_nodes(event['segments']):
        if segment['name'] != 'OBX':
            continue
        segment['repeatBy'] = 'results'
        fields = segment.setdefault('fields', [])
        for target, source in [('OBX-1', 'counter'), ('OBX-2', 'result.valueType'), ('OBX-3.1', 'result.code'),
                               ('OBX-3.2', 'result.name'), ('OBX-4', 'result.subId'), ('OBX-5', 'result.hl7Value'),
                               ('OBX-6', 'result.unit'), ('OBX-7', 'result.referenceRange'),
                               ('OBX-8', 'result.abnormalFlag'), ('OBX-11', 'result.status')]:
            field = next((f for f in fields if f['target'].replace('[1]', '') == target), None)
            if target == 'OBX-2' and field:
                field.pop('sourceType', None)
                field.pop('value', None)
                field['source'] = source
            elif field is None and not any(f.get('source') == source or
                    source == 'result.hl7Value' and f.get('source') == 'result.value' for f in fields):
                fields.append({'target': target, 'source': source})
    # The output schema follows the template; the received example is never a
    # second authority that can demand obsolete sample-only fields.
    previous = event.get('schema') or outbound.get('schema') or {}
    event['schema'] = {'formatVersion': 2, 'messageType': event['messageType'], 'inferenceMode': 'FLEXIBLE',
        'delimiters': copy.deepcopy(previous.get('delimiters', {})),
        'options': {'allowUnexpectedSegments': True, 'allowUnexpectedFields': True, 'allowUnexpectedComponents': True},
        'segments': [{'name': s['name'], 'min': 1 if s['name'] == 'MSH' else 0, 'max': 1 if s['name'] == 'MSH' else '*', 'fields': {}}
                     for s in walk_nodes(event['segments'])]}
    return profile


def compile_equipment_profile(payload):
    try:
        return _compile_equipment_profile(payload)
    except (TypeError, AttributeError, KeyError, OverflowError, RecursionError) as error:
        raise ValueError('Estructura del perfil del equipo inválida: ' + str(error)) from error


def _compile_equipment_profile(payload):
    from .profile_validation import validate_profile
    profile = copy.deepcopy(payload)
    profile['configurationVersion'] = 3
    profile['catalog'] = validate_embedded_catalog(profile.get('catalog') or {'studies': []})
    sync_inbound_schema(profile)
    profile = compile_result_template(profile)
    for event in profile.get('outbound', {}).get('events', {}).values():
        for node in walk_nodes(event.get('segments', [])):
            targets = [f['target'].replace('[1]', '') for f in node.get('fields', [])]
            if len(targets) != len(set(targets)):
                raise ValueError('Hay rutas de salida duplicadas en ' + node['name'])
    return validate_profile(profile)


def sync_inbound_schema(profile):
    """Mappings own data presence; inference never leaves stale requirements."""
    inbound = profile.get('inbound', {})
    mappings = inbound.get('mappings', [])
    targets = [m.get('target') for m in mappings]
    if len(targets) != len(set(targets)):
        raise ValueError('Un dato de entrada tiene varias filas. Usa alternativas en la misma ruta.')
    schema = inbound.get('schema') or {}
    if schema.get('formatVersion') != 2:
        return
    for segment in schema.get('segments', []):
        if segment['name'] == 'MSH':
            continue
        for field in segment.get('fields', {}).values():
            known = False
            for rep in field.get('repetitions', []):
                for component in rep.get('components', {}).values():
                    for rule in component.get('subcomponents', {}).values() if component.get('subcomponents') else [component]:
                        source = rule.get('source') or SEMANTICS.get(rule.get('semantic'), '')
                        if source.startswith(('patient.', 'order.', 'sample.', 'test.', 'visit.', 'customFields.')):
                            rule['required'] = False
                            known = True
            if known:
                field['required'] = False
                field['minRepetitions'] = 0
    # Required canonical data is checked by parse_with_profile, including its
    # alternatives. Making all alternatives mandatory in the structural schema
    # would contradict the map that the user is actually editing.
    for mapping in mappings:
        for path in [mapping.get('source', ''), *mapping.get('alternatives', [])]:
            match = re.fullmatch(r'([A-Z][A-Z0-9]{2})-([1-9]\d*)(?:\[([1-9]\d*)\])?(?:\.([1-9]\d*))?(?:\.([1-9]\d*))?', path)
            if not match:
                continue  # validate_profile reports the precise invalid path.
            name, number, rep, component, sub = match.groups()
            if name == 'MSH':
                continue
            segment = next((s for s in schema['segments'] if s['name'] == name), None)
            if not segment:
                segment = {'name': name, 'min': 0, 'max': '*', 'fields': {}}
                schema['segments'].append(segment)
            field = segment.setdefault('fields', {}).setdefault(number, {'required': False, 'minRepetitions': 0,
                'maxRepetitions': '*', 'composite': bool(component), 'repetitions': []})
            repetition = next((r for r in field.setdefault('repetitions', []) if r.get('index', 1) == int(rep or 1)), None)
            if not repetition:
                repetition = {'index': int(rep or 1), 'components': {}}
                field['repetitions'].append(repetition)
            rule = {'required': False, 'source': mapping['target']}
            if sub:
                repetition['components'].setdefault(component, {}).setdefault('subcomponents', {}).setdefault(sub, rule)
            else:
                repetition['components'].setdefault(component or '1', rule)


def apply_example(payload, raw, direction='auto', event_type='RESULT_FINAL'):
    profile = compile_equipment_profile(payload)
    raw = normalize_hl7(raw)
    schema = infer_hl7_contract(raw, strict=False)
    if direction == 'auto':
        direction = 'outbound' if schema['messageType'].split('^')[0] in {'ORU', 'OUL'} else 'inbound'
    if direction not in {'inbound', 'outbound'}:
        raise ValueError('Elige entrada o salida')
    if direction == 'inbound':
        inbound = profile.setdefault('inbound', {})
        inbound.update(schema=schema, rawHL7=raw, messageType=schema['messageType'], mappings=inbound_mappings(schema))
        inbound.setdefault('receptionMode', 'PERMISSIVE')
        if inbound['receptionMode'] == 'LEARN':
            inbound['receptionMode'] = 'PERMISSIVE'
    else:
        outbound = profile.setdefault('outbound', {})
        outbound.setdefault('events', {})[event_type] = {'messageType': schema['messageType'], 'version': schema.get('version', '2.5.1'),
            'segments': schema_routes(schema), 'schema': schema, 'rawHL7': raw}
    suggestions = infer_studies(raw, allow_orders=True)['studies'] if any(s['name'] in {'OBR', 'OBX'} for s in schema['segments']) else []
    for study in suggestions:
        study['parameters'] = [p for p in study['parameters'] if p['valueType'] in {'NM', 'ST', 'TX', 'CE', 'CWE'}]
    if direction == 'outbound':
        suggestions = [s for s in suggestions if s['parameters']]
    studies = profile.setdefault('catalog', {}).setdefault('studies', [])
    for study in suggestions:
        existing = next((s for s in studies if s['code'] == study['code'] or study['code'] in s.get('aliases', [])), None)
        if existing:
            for parameter in study.get('parameters', []):
                if not any(p['code'] == parameter['code'] for n in catalog_nodes(existing) for p in n.get('parameters', [])):
                    existing.setdefault('parameters', []).append(parameter)
        else:
            studies.append(study)
    return {'profile': compile_equipment_profile(profile), 'direction': direction,
            'detected': {'messageType': schema['messageType'], 'segments': len(schema['segments']),
                         'studies': len(suggestions), 'parameters': sum(len(s.get('parameters', [])) for s in suggestions)}}
