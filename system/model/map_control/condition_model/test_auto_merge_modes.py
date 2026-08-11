# -*- coding: utf-8 -*-
"""Regression tests for merge modes, risk evidence, and input validation."""
from pathlib import Path
import tempfile
import pandas as pd

from system.model.slurry_control.condition_model.condition_config import from_dict
from system.model.slurry_control.condition_model.initial_condition_builder import InitialConditionBuilder, normalize_and_validate_training_frame
from system.model.slurry_control.condition_model.auto_merge_manager import AutoMergeManager
from system.model.slurry_control.condition_model.condition_merger import ConditionMerger
from system.model.slurry_control.condition_model.online_condition_classifier import OnlineConditionClassifier


def cfg(mode='evidence_only', enabled=True):
    return from_dict({
        'grid_definition': {
            'jzfh': {'min':100,'max':120,'step':10},
            'yyq_SO2': {'min':500,'max':900,'step':200},
        },
        'data_columns': {'outlet_so2':'jyq_SO2','xst_ph':'xstjy_PH','apt_ph':'aptjy_PH','liquid_gas':'liquid_gas_ratio'},
        'emission_limit':35,
        'merge': {
            'enabled':enabled,'mode':mode,
            'min_observed_samples':2,'min_mature_samples':3,
            'min_auto_merge_samples':5,'min_auto_confirm_samples':8,
            'min_common_state_samples':2,'min_risk_samples':2,
            'min_metric_coverage_ratio':0.8,'min_consecutive_pass_snapshots':3,
            'min_new_samples_per_member_for_confirmation':2,
            'max_auto_region_cells':4,
            'max_liquid_gas_relative_difference':0.15,
            'max_pump_distribution_distance':0.25,
            'max_risk_rate_difference':0.10,
        },
        'online': {'minimum_dwell_cycles':1,'allow_provisional_region_fallback':True},
    })

def rows(n=5, outlet=20):
    result=[]
    for so2,lg in ((600,10.0),(800,10.5)):
        for _ in range(n):
            result.append({'jzfh':105,'yyq_SO2':so2,'jyq_SO2':outlet,'xstjy_PH':5.5,'aptjy_PH':5.7,'liquid_gas_ratio':lg,'xst_circulation_pump_count':2,'apt_circulation_pump_count':1})
    return result

# disabled
c=cfg(enabled=False)
s=InitialConditionBuilder(c).build(rows(), 'v001')
s,r=AutoMergeManager(c).apply(s)
assert all(len(x.member_grid_ids)==1 for x in s.policy_regions.values())
assert r['summary']['published_merged_region_count']==0

# conservative needs 8 for initial publication, only 5 available
c=cfg(mode='conservative')
s=InitialConditionBuilder(c).build(rows(), 'v001')
s,r=AutoMergeManager(c).apply(s)
assert all(len(x.member_grid_ids)==1 for x in s.policy_regions.values())
assert any(x['decision']=='INSUFFICIENT_AUTO_SAMPLES' for x in r['candidates'])

# risk missing is a hard rejection
c=cfg()
missing=rows()
for x in missing: x['jyq_SO2']=None
s=InitialConditionBuilder(c).build(missing, 'v001')
d=ConditionMerger(c).evaluate_pair(s.grid_catalog['P1-S1'],s.grid_catalog['P1-S2'])
assert d['decision']=='INSUFFICIENT_RISK_EVIDENCE', d

# field validation
frame=pd.DataFrame(rows()).drop(columns=['xstjy_PH'])
try:
    normalize_and_validate_training_frame(frame,c)
except ValueError as exc:
    assert 'xst_ph=xstjy_PH' in str(exc)
else:
    raise AssertionError('missing field not rejected')

# provisional online fallback is not economic exploration
s=InitialConditionBuilder(c).build(rows(), 'v001')
s,r=AutoMergeManager(c).apply(s)
cl=OnlineConditionClassifier(c,s)
res=cl.classify({'jzfh':105,'yyq_SO2':600,'xst_circulation_pump_count':3,'apt_circulation_pump_count':1})
assert res.experience_source=='MERGED_REGION'
assert not res.economic_exploration_allowed
print('MODES_TEST_PASSED')
