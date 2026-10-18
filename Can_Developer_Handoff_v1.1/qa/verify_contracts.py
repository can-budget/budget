from pathlib import Path
import yaml,json,re
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012
R=Path(__file__).resolve().parents[1]
spec=yaml.safe_load((R/'contracts/openapi.yaml').read_text())
refs=[]
def walk(node):
    if isinstance(node,dict):
        if '$ref' in node:
            ref=node['$ref'];refs.append(ref)
            assert ref.startswith('#/'),ref
            value=spec
            for part in ref[2:].split('/'):value=value[part.replace('~1','/').replace('~0','~')]
        for v in node.values():walk(v)
    elif isinstance(node,list):
        for v in node:walk(v)
walk(spec)
for name,schema in spec['components']['schemas'].items():Draft202012Validator.check_schema(schema)
ids=[]
for path,methods in spec['paths'].items():
    for method,op in methods.items():
        ids.append(op['operationId'])
        declared={p['name'] for p in op.get('parameters',[]) if p['in']=='path'}
        assert declared==set(re.findall(r'{(\w+)}',path)),path
        assert 'responses' in op
assert len(ids)==len(set(ids))
registry=Registry().with_resource('urn:can:openapi', Resource.from_contents(spec, default_specification=DRAFT202012))
def named_validator(name):
    return Draft202012Validator({'$ref':'urn:can:openapi#/components/schemas/'+name}, registry=registry)
validator=named_validator('Command')
u='00000000-0000-4000-8000-000000000001'
valid={'command_id':u,'type':'add_entry','payload':{'entry_id':u,'sprint_id':u,'kind':'expense','amount_minor':12800,'occurred_at':'2026-10-11T11:00:00Z','allow_completed':False}}
validator.validate(valid)
invalid=json.loads(json.dumps(valid));invalid['payload']['amount_minor']=12.8
assert list(validator.iter_errors(invalid))
invalid=json.loads(json.dumps(valid));invalid['payload']['bank_minor']=100
assert list(validator.iter_errors(invalid))
proj=named_validator('Projection')
for item in json.loads((R/'reference/golden_cases.json').read_text()):proj.validate(item['expected'])
json.loads((R/'contracts/design_tokens.json').read_text())
print('YAML/JSON parse OK;',len(refs),'internal refs resolved;',len(spec['components']['schemas']),'JSON Schema definitions checked;',len(ids),'unique operations checked; valid/invalid command samples and projection fixtures validated.')
print('NOT a full OpenAPI conformance check and NOT a PostgreSQL execution test.')

# v1.1 structural and command invariants (NOT runtime invariants).
assert spec['info']['version']=='1.1.0'
S=spec['components']['schemas']
assert S['Cabinet']['properties']['members']['maxItems']==10
assert S['Sprint']['properties']['calculation_version']['const']==2
for name in ['Snapshot','ArchivedProjection']:
    assert 'sprint_revisions' in S[name]['required']
edit={'command_id':u,'type':'update_sprint','payload':{'sprint_id':u,'expected_cabinet_revision':1,'expected_business_date':'2026-10-12','bank_minor':900000}}
validator.validate(edit)
edit_date=json.loads(json.dumps(edit));edit_date['payload'].pop('bank_minor');edit_date['payload']['end_date']='2026-10-25';validator.validate(edit_date)
edit_both=json.loads(json.dumps(edit));edit_both['payload']['end_date']='2026-10-25';validator.validate(edit_both)
for which in ['no_dirty_fields','initial_instead_of_bank','missing_business_date','fractional_minor']:
    bad=json.loads(json.dumps(edit))
    if which=='no_dirty_fields': bad['payload'].pop('bank_minor')
    elif which=='initial_instead_of_bank': bad['payload']['initial_minor']=bad['payload'].pop('bank_minor')
    elif which=='missing_business_date': bad['payload'].pop('expected_business_date')
    else: bad['payload']['bank_minor']=0.5
    assert list(validator.iter_errors(bad)),which
print('v1.1 command samples, capacity metadata, revision payload declarations, and calculation version checked.')
