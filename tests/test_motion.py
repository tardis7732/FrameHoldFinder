import copy
import inspect
import unittest
import numpy as np
import motion_core as m


def analysis(speeds):
    points=np.array([(gx*96+10+k*8,gy*90+15+k*8) for gy in range(6) for gx in range(10) for k in range(4)],np.float32)
    steps=[]
    for speed in speeds:
        v=np.zeros_like(points);v[:,0]=speed
        steps.append(dict(ids=np.arange(len(points)),p=points.copy(),v=v,speed=np.linalg.norm(v,axis=1)))
    return dict(steps=steps,analysis_width=960,analysis_height=540,frames=len(speeds)+1)


def adjacent_peaks(count=2):
    # Different feature populations can have peaks on adjacent intervals.
    a=analysis([10.]*(count+5))
    for group in range(count):
        step=a['steps'][2+group]
        indices=np.array_split(np.arange(240),count)[group]
        step['v'][indices,0]=30.
        step['speed']=np.linalg.norm(step['v'],axis=1)
    return a


def repair_row(key,kind,targets,frames=10,included=True,uses_prediction=False):
    start=int(key.split(':')[1]);end=int(key.split(':')[2]) if kind=='hold' else start
    return dict(key=key,kind=kind,start=start,end=end,targets=list(targets),source_frames=frames,
                included=included,repairable=True,reason='',uses_prediction=uses_prediction,
                generated=len(targets),copies=len(targets),delta=0,repair_span=[],repair_anchors=[])


class MotionTests(unittest.TestCase):
    def rows(self,speeds,**kw): return m.candidates(m.prepare(analysis(speeds),**kw))

    def assert_fixed_runs(self,rows,frames):
        runs=m.render_runs(rows);last_end=0
        for run in runs:
            self.assertLess(last_end,run['start']);self.assertLess(run['start'],run['end'])
            self.assertLess(run['end'],frames)
            self.assertEqual(run['targets'],list(range(run['start']+1,run['end']+1)))
            self.assertEqual(run['generated'],run['end']-run['start'])
            self.assertEqual(run['copies'],run['generated']);last_end=run['end']
        self.assertEqual(frames+sum(r['generated']-(r['end']-r['start']) for r in runs),frames)
        self.assertTrue(all(row['delta']==0 for row in rows))
        return runs

    def test_normal_slowdown_direction_and_continuous_acceleration(self):
        for speeds in ([10]*10,[10,10,5,10,10],[1,2,3,4,5,6],[10,10,-10,-10,-10]):
            self.assertEqual(self.rows(speeds),[])

    def test_holds_always_replace_same_number(self):
        for distance in (10,20,30,100):
            rows=self.rows([10,10,0,0,distance,10,10]);self.assertEqual(len(rows),1)
            row=rows[0]
            self.assertEqual((row['kind'],row['copies'],row['generated'],row['delta']),('hold',2,2,0))
            self.assertEqual(row['targets'],[4,5]);self.assertEqual(row['repair_anchors'],[3,6])
            self.assert_fixed_runs(rows,8)

    def test_manual_range_keeps_exact_anchors_and_original_frame_count(self):
        a=analysis([10]*10)
        model=m.prepare(a,hold_enabled=False,manual_ranges=[dict(left=2,right=6)])
        rows=m.candidates(model,jump_enabled=False)
        self.assertEqual(len(rows),1);row=rows[0]
        self.assertEqual((row['key'],row['kind'],row['start'],row['end'],row['right']),
                         ('replace:2:6','replace',2,5,6))
        self.assertEqual(row['targets'],[3,4,5]);self.assertEqual(row['repair_anchors'],[2,6])
        self.assertTrue(row['manual']);self.assertTrue(row['included']);self.assertTrue(row['repairable'])
        self.assertFalse(row['jump']);self.assertFalse(row['uses_prediction']);self.assertEqual(row['score'],0)
        run=self.assert_fixed_runs(rows,a['frames'])[0]
        self.assertEqual((run['start'],run['end'],run['generated']),(2,5,3))
        self.assertEqual(run['source_keys'],['replace:2:6'])
        self.assertEqual(m.render_runs(rows),[run])

    def test_manual_shared_endpoint_stays_original_and_runs_never_merge(self):
        ranges=[dict(left=5,right=8),dict(left=2,right=5)]
        model=m.prepare(dict(frames=10,steps=[]),manual_ranges=ranges)
        self.assertEqual(model['manual_ranges'],[
            dict(key='replace:2:5',left=2,right=5),dict(key='replace:5:8',left=5,right=8)])
        rows=m.candidates(model);runs=self.assert_fixed_runs(rows,10)
        self.assertEqual([r['targets'] for r in runs],[[3,4],[6,7]])
        self.assertEqual([r['repair_anchors'] for r in rows],[[2,5],[5,8]])
        self.assertFalse(any(5 in r['targets'] for r in runs))
        self.assertTrue(all(len(r['source_keys'])==1 for r in runs))

    def test_manual_without_analysis_or_count_cap(self):
        rows=m.candidates(m.prepare(dict(frames=154),manual_ranges=[dict(left=2,right=153)]))
        run=self.assert_fixed_runs(rows,154)[0]
        self.assertEqual(run['generated'],150)
        self.assertEqual((run['start'],run['end']),(2,152))
        self.assertEqual(m.candidates(m.prepare(dict(frames=1,steps=[]))),[])

    def test_manual_range_validation_rejects_invalid_and_overlapping_ranges(self):
        invalid=[dict(left=0,right=3),dict(left=1,right=11),dict(left=3,right=3),
                 dict(left=3,right=4),dict(left=5,right=2),dict(left=True,right=5),
                 dict(left=2.5,right=6),dict(left=2,right=float('inf')),
                 dict(left=float('nan'),right=6),dict(left=2),{},None,
                 dict(key='replace:2:7',left=2,right=6)]
        for item in invalid:
            with self.subTest(item=item),self.assertRaises(ValueError):
                m.validate_manual_ranges([item],10)
        for ranges in ([dict(left=2,right=6),dict(left=2,right=6)],
                       [dict(left=2,right=6),dict(left=5,right=8)],
                       [dict(left=2,right=8),dict(left=3,right=6)]):
            with self.subTest(ranges=ranges),self.assertRaises(ValueError):
                m.validate_manual_ranges(ranges,10)
        for frames in (0,True,3.5,float('nan')):
            with self.subTest(frames=frames),self.assertRaises(ValueError):m.validate_manual_ranges([],frames)
        self.assertEqual(m.validate_manual_ranges(None,10),[])

    def test_manual_blocks_automatic_targets_at_both_anchors_and_interior(self):
        a=adjacent_peaks(3)
        for left,right in ((3,5),(4,6),(5,7)):
            rows=m.candidates(m.prepare(a,manual_ranges=[dict(left=left,right=right)]),15)
            for row in rows:
                if row['kind']=='replace':continue
                target=row['targets'][0]
                blocked=left<=target<=right
                self.assertEqual(row['blocked_by_manual'],blocked)
                if blocked:
                    self.assertFalse(row['included']);self.assertFalse(row['repairable'])
                    self.assertEqual(row['reason'],'수동 추가 구간과 겹침')
            runs=self.assert_fixed_runs(rows,a['frames'])
            manual=next(r for r in runs if f'replace:{left}:{right}' in r['source_keys'])
            self.assertEqual((manual['start'],manual['end']+1),(left,right))
            self.assertEqual(manual['source_keys'],[f'replace:{left}:{right}'])

    def test_manual_and_automatic_can_share_only_an_untouched_original_anchor(self):
        a=adjacent_peaks(3)
        rows=m.candidates(m.prepare(a,manual_ranges=[dict(left=5,right=7)]),15)
        runs=self.assert_fixed_runs(rows,a['frames'])
        self.assertEqual([r['targets'] for r in runs],[[4],[6]])
        self.assertEqual([r['source_keys'] for r in runs],[['jump:3:4'],['replace:5:7']])
        self.assertEqual([(r['start'],r['end']+1) for r in runs],[(3,5),(5,7)])

    def test_manual_disables_conflicting_hold_prediction_and_exclusion_restores_it(self):
        a=analysis([10,10,0,20,30,10,10]);ranges=[dict(left=3,right=5)]
        base=m.prepare(a);manual=m.prepare(a,manual_ranges=ranges)
        self.assertTrue(base['holds'][0]['active']);self.assertFalse(manual['holds'][0]['active'])
        self.assertTrue(manual['holds'][0]['blocked_by_manual'])
        self.assertTrue(any(metric['uses_prediction'] for metric in base['metrics']))
        self.assertFalse(any(metric['uses_prediction'] for metric in manual['metrics']))
        for metric in manual['metrics']:self.assertEqual(metric['score'],metric['raw_score'])
        rows=m.candidates(manual,15)
        hold=next(r for r in rows if r['kind']=='hold')
        self.assertFalse(hold['included']);self.assertFalse(hold['repairable'])
        self.assertEqual(hold['reason'],'수동 추가 구간과 겹침')
        for model in (m.prepare(a,manual_ranges=ranges,excluded={'replace:3:5'}),
                      m.prepare(a,manual_ranges=[])):
            self.assertTrue(model['holds'][0]['active'])
            for left,right in zip(base['metrics'],model['metrics']):
                self.assertEqual(left['score'],right['score'])
                self.assertEqual(left['prediction_holds'],right['prediction_holds'])
                np.testing.assert_array_equal(left['vectors'],right['vectors'])
            restored=m.candidates(model,15)
            self.assertEqual(m.render_runs(restored),m.render_runs(m.candidates(base,15)))
            self.assertTrue(all(not r['blocked_by_manual'] for r in restored))

    def test_excluded_automatic_hold_does_not_block_manual_or_propagate_when_overridden(self):
        a=analysis([10,0,20,0,20,10]);excluded={'hold:4:5'}
        baseline=m.prepare(a,excluded=excluded)
        self.assertFalse(any(h['active'] for h in baseline['holds']))
        model=m.prepare(a,excluded=excluded,manual_ranges=[dict(left=4,right=6)])
        first,second=model['holds']
        self.assertTrue(first['active']);self.assertFalse(second['active'])
        self.assertTrue(second['blocked_by_manual'])
        self.assertEqual(model['protected_targets'],[]);self.assertEqual(model['blocked_anchors'],[])
        rows=m.candidates(model,15);runs=self.assert_fixed_runs(rows,a['frames'])
        self.assertEqual([r['targets'] for r in runs],[[3],[5]])
        self.assertEqual(runs[1]['source_keys'],['replace:4:6'])

    def test_manual_takes_priority_over_excluded_automatic_jump(self):
        a=adjacent_peaks(3)
        model=m.prepare(a,excluded={'jump:4:5'},manual_ranges=[dict(left=3,right=7)])
        self.assertEqual(model['protected_targets'],[])
        rows=m.candidates(model,15);runs=self.assert_fixed_runs(rows,a['frames'])
        self.assertEqual(len(runs),1);self.assertEqual(runs[0]['targets'],[4,5,6])
        self.assertEqual(runs[0]['source_keys'],['replace:3:7'])

    def test_excluded_manual_does_not_protect_automatic_targets(self):
        a=adjacent_peaks(3)
        rows=m.candidates(m.prepare(a,manual_ranges=[dict(left=3,right=7)],excluded={'replace:3:7'}),15)
        runs=self.assert_fixed_runs(rows,a['frames'])
        self.assertEqual(len(runs),1);self.assertEqual(runs[0]['targets'],[4,5,6])
        self.assertEqual(runs[0]['source_keys'],['jump:3:4','jump:4:5','jump:5:6'])
        manual=next(r for r in rows if r['kind']=='replace')
        self.assertFalse(manual['included']);self.assertTrue(manual['repairable'])

    def test_obsolete_variable_length_controls_and_statistics_are_removed(self):
        self.assertFalse(hasattr(m,'best_count'))
        obsolete={'overrides','max_generated','auto','needed','suggested'}
        for function in (m.prepare,m.candidates,m.peak_stats):
            self.assertFalse(obsolete.intersection(inspect.signature(function).parameters))
        for speeds in ([10,10,0,0,100,10,10],[10,10,30,10,10]):
            model=m.prepare(analysis(speeds))
            self.assertFalse(obsolete.intersection(model))
            for metric in model['metrics']: self.assertFalse(obsolete.intersection(metric['stats']))
            for row in m.candidates(model): self.assertFalse(obsolete.intersection(row))

    def test_single_jump_replaces_arrival_using_outside_frames(self):
        rows=self.rows([10,10,30,10,10]);row=rows[0]
        self.assertEqual((row['kind'],row['start'],row['end'],row['right']),('jump',3,3,4))
        self.assertEqual(row['targets'],[4]);self.assertEqual(row['repair_anchors'],[3,5])
        run=self.assert_fixed_runs(rows,6)[0]
        self.assertEqual((run['start'],run['end'],run['generated']),(3,4,1))
        self.assertEqual([j/(run['generated']+1) for j in range(1,run['generated']+1)],[.5])

    def test_adjacent_candidates_share_original_endpoints_and_thirds(self):
        a=adjacent_peaks();rows=m.candidates(m.prepare(a),15)
        self.assertEqual([r['targets'] for r in rows],[[4],[5]])
        run=self.assert_fixed_runs(rows,a['frames'])[0]
        self.assertEqual((run['start'],run['end'],run['generated']),(3,5,2))
        self.assertEqual([j/(run['generated']+1) for j in range(1,run['generated']+1)],[1/3,2/3])
        self.assertTrue(all(r['repair_anchors']==[3,6] and r['repair_span']==[4,5] for r in rows))

    def test_exclusion_precedes_grouping_and_can_split_a_run(self):
        a=adjacent_peaks(3);excluded={'jump:4:5'}
        rows=m.candidates(m.prepare(a,excluded=excluded),15)
        runs=self.assert_fixed_runs(rows,a['frames'])
        self.assertEqual([r['targets'] for r in runs],[[4],[6]])
        self.assertEqual([(r['start'],r['end']+1) for r in runs],[(3,5),(5,7)])
        self.assertFalse(next(r for r in rows if r['key']=='jump:4:5')['included'])
        rows=m.candidates(m.prepare(adjacent_peaks(),excluded={'jump:3:4'}),15)
        self.assertEqual(m.render_runs(rows)[0]['targets'],[5])
        self.assertEqual(m.render_runs(rows)[0]['start'],4)

    def test_merge_targets_across_hold_and_jump_without_duplicates(self):
        rows=[repair_row('hold:3:5','hold',[4,5],uses_prediction=True),repair_row('jump:5:6','jump',[6])]
        run=self.assert_fixed_runs(rows,10)[0]
        self.assertEqual((run['start'],run['end'],run['generated']),(3,6,3))
        self.assertEqual(run['source_keys'],['hold:3:5','jump:5:6'])
        duplicate=[repair_row('hold:3:5','hold',[4,5]),repair_row('jump:4:5','jump',[5])]
        self.assertEqual(m.render_runs(duplicate)[0]['targets'],[4,5])

    def test_excluded_target_cannot_be_restored_through_overlap(self):
        rows=[repair_row('hold:3:5','hold',[4,5]),repair_row('jump:4:5','jump',[5],included=False)]
        self.assertEqual(m.render_runs(rows),[])
        self.assertFalse(rows[0]['repairable']);self.assertTrue(rows[0]['reason'])
        model=m.prepare(analysis([10,10,0,0,30,10,10]),excluded={'jump:4:5'})
        row=m.candidates(model,15)[0]
        self.assertFalse(row['repairable']);self.assertFalse(model['holds'][0]['active'])
        self.assertEqual(m.render_runs([row]),[])

    def test_excluded_hold_images_are_not_targets_or_anchors(self):
        excluded=repair_row('hold:3:4','hold',[4],included=False)
        for target in (3,4,5):
            row=repair_row(f'jump:{target-1}:{target}','jump',[target])
            self.assertEqual(m.render_runs([copy.deepcopy(excluded),row]),[])
            self.assertFalse(row['repairable'])
        a=analysis([10,10,0,30,10,10])
        rows=m.candidates(m.prepare(a,hold_enabled=False,excluded={'hold:3:4'}),15)
        self.assertTrue(rows);self.assertTrue(all(not r['repairable'] for r in rows))
        self.assertEqual(m.render_runs(rows),[])

    def test_deferred_hold_does_not_contaminate_neighbor_anchors(self):
        a=analysis([10,0,20,0,20,10]);model=m.prepare(a,excluded={'hold:4:5'})
        self.assertTrue(all(not r['active'] for r in model['holds']))
        rows=m.candidates(model,15);self.assertEqual(m.render_runs(rows),[])
        self.assertFalse(next(r for r in rows if r['key']=='hold:2:3')['repairable'])

    def test_residual_peak_after_assumed_hold_repair_is_actionable(self):
        a=analysis([10,10,0,20,30,10,10]);model=m.prepare(a)
        rows=m.candidates(model,15)
        row=next(r for r in rows if r['key']=='jump:5:6')
        self.assertEqual((row['kind'],row['generated'],row['delta']),('jump',1,0))
        self.assertTrue(row['predicted_peak']);self.assertTrue(row['jump'])
        self.assertTrue(row['repairable']);self.assertEqual(row['targets'],[6])
        self.assertEqual(row['prediction_holds'],['hold:3:4'])
        self.assertEqual([r['targets'] for r in self.assert_fixed_runs(rows,a['frames'])],[[4],[6]])
        referenced=repair_row('jump:5:6','jump',[6],uses_prediction=True)
        self.assertEqual(m.render_runs([referenced])[0]['targets'],[6])

    def test_residual_target_merges_with_hold_and_respects_exclusion(self):
        a=analysis([10,10,30,0,20,10,10])
        rows=m.candidates(m.prepare(a),15)
        jump=next(r for r in rows if r['kind']=='jump')
        self.assertEqual(jump['key'],'jump:3:4');self.assertTrue(jump['predicted_peak'])
        runs=self.assert_fixed_runs(rows,a['frames'])
        self.assertEqual(len(runs),1)
        self.assertEqual((runs[0]['start'],runs[0]['end'],runs[0]['generated']),(3,5,2))
        self.assertEqual(runs[0]['targets'],[4,5])
        self.assertTrue(all(r['repair_anchors']==[3,6] for r in rows))
        excluded={'jump:3:4'}
        rows=m.candidates(m.prepare(a,excluded=excluded),15)
        self.assertEqual([r['targets'] for r in m.render_runs(rows)],[[5]])
        rows=m.candidates(m.prepare(a,excluded={'hold:4:5'}),15)
        self.assertEqual(m.render_runs(rows),[])

    def test_assumed_midpoints_or_thirds_remove_resolved_peaks(self):
        for speeds,span in (([10,10,0,20,10,10],slice(2,4)),
                            ([10,10,0,0,30,10,10],slice(2,5))):
            a=analysis(speeds);model=m.prepare(a)
            self.assertTrue(any(q['raw_score']>50 for q in model['metrics'][span]))
            for metric in model['metrics'][span]:
                self.assertTrue(metric['uses_prediction'])
                self.assertEqual(metric['score'],0)
            rows=m.candidates(model,15)
            self.assertEqual(len(rows),1);self.assertEqual(rows[0]['kind'],'hold')
            self.assertFalse(rows[0]['jump']);self.assertFalse(rows[0]['predicted_peak'])
            self.assert_fixed_runs(rows,a['frames'])
        # A virtual neighbor alone must never create an action below threshold.
        model=m.prepare(analysis([10,10,0,20,11,10,10]))
        self.assertTrue(model['metrics'][4]['uses_prediction'])
        self.assertLess(model['metrics'][4]['score'],15)
        rows=m.candidates(model,15)
        self.assertEqual([r['kind'] for r in rows],['hold'])

    def test_small_peak_is_only_a_candidate_above_the_selected_threshold(self):
        model=m.prepare(analysis([10,10,12,10,10]))
        self.assertEqual(m.candidates(model,50),[])
        rows=m.candidates(model,15);self.assertEqual(rows[0]['generated'],1)
        self.assertAlmostEqual(model['metrics'][2]['score'],20,places=4)

    def test_real_jump_far_from_hold_stays_measured(self):
        rows=m.candidates(m.prepare(analysis([10,10,0,20,10,10,30,10,10])),15)
        row=next(r for r in rows if r['key']=='jump:7:8')
        self.assertEqual(row['kind'],'jump');self.assertTrue(row['repairable'])
        self.assertFalse(row['uses_prediction']);self.assertEqual(row['targets'],[8])

    def test_fixed_hold_stats_and_vectors_share_the_original_interval_count(self):
        model=m.prepare(analysis([10,10,0,0,40,10,10]))
        hold=model['holds'][0]
        for metric in model['metrics'][2:5]:
            self.assertIs(metric['stats'],hold['peak'])
            self.assertEqual(metric['score'],metric['stats']['score'])
            np.testing.assert_allclose(np.linalg.norm(metric['vectors'],axis=1),40/3,rtol=1e-6)
        row=m.candidates(model,15)[0]
        self.assertTrue(row['predicted_peak']);self.assertTrue(row['jump']);self.assertTrue(row['repairable'])
        self.assertEqual(row['generated'],2)

    def test_jump_minimum_does_not_change_hold_confirmation(self):
        a=analysis([10,10,0,20,30,10,10])
        # Two moving populations keep the jump comparison meaningful at both
        # minima while exposing a change in its eligible feature population.
        for step in a['steps']:
            step['v'][120:]*=3
            step['speed']=np.linalg.norm(step['v'],axis=1)
        low=m.prepare(a,quiet_px=.5,min_move=1.)
        high=m.prepare(a,quiet_px=.5,min_move=19.2)
        self.assertEqual(low['hold_min_move'],1.)
        self.assertEqual(high['hold_min_move'],1.)
        self.assertEqual([q['quiet'] for q in low['metrics']],[q['quiet'] for q in high['metrics']])
        self.assertEqual([(h['key'],h['ratio'],h['generated']) for h in low['holds']],
                         [(h['key'],h['ratio'],h['generated']) for h in high['holds']])
        for left,right in zip(low['metrics'],high['metrics']):
            np.testing.assert_array_equal(left['vector_ids'],right['vector_ids'])
            np.testing.assert_array_equal(left['vectors'],right['vectors'])
        low_hold=[r for r in m.candidates(low,15) if r['kind']=='hold']
        high_hold=[r for r in m.candidates(high,15) if r['kind']=='hold']
        self.assertEqual([r['targets'] for r in low_hold],[[4]])
        self.assertEqual([r['targets'] for r in high_hold],[[4]])
        self.assertEqual(low['metrics'][4]['stats']['points'],240)
        self.assertEqual(high['metrics'][4]['stats']['points'],120)
        self.assertEqual(m.prepare(a,hold_min_move=100)['holds'],[])

    def test_hold_context_minimum_requires_finite_positive_value(self):
        a=analysis([10,10,0,20,10,10])
        for value in (0,-1,float('nan'),float('inf'),-float('inf'),None,'bad',True):
            with self.subTest(value=value):
                with self.assertRaises(ValueError): m.prepare(a,hold_min_move=value)

    def test_tracks_without_hold_endpoints_are_not_raw_fallback(self):
        a=analysis([10,0,20,10,10]);extra_ids=np.arange(240,360)
        for index,speed in [(2,1),(3,30),(4,10)]:
            step=a['steps'][index];vector=np.zeros((120,2),np.float32);vector[:,0]=speed
            step['ids']=np.r_[step['ids'],extra_ids]
            step['p']=np.concatenate([step['p'],step['p'][:120]])
            step['v']=np.concatenate([step['v'],vector]);step['speed']=np.linalg.norm(step['v'],axis=1)
        model=m.prepare(a)
        self.assertEqual([r['kind'] for r in m.candidates(model,15)],['hold'])
        self.assertTrue(np.all(model['metrics'][2]['vector_ids']<240))
        self.assertTrue(np.all(model['metrics'][3]['stats']['ids']<240))
        self.assertEqual(model['metrics'][3]['stats']['points'],240)

    def test_graph_score_matches_minimum_twenty_and_ten_percent(self):
        stats=m.peak_stats(np.r_[np.full(180,10.),np.full(20,20.)],np.full(200,10.),np.full(200,10.),support_percent=10)
        self.assertEqual(stats['score'],100.)
        too_few=m.peak_stats(np.r_[np.full(181,10.),np.full(19,20.)],np.full(200,10.),np.full(200,10.),support_percent=10)
        self.assertEqual(too_few['score'],0.)

    def population_peak(self,population,fast):
        a=analysis([10,10,10,10,10])
        for step in a['steps']:
            for key in ('ids','p','v','speed'): step[key]=step[key][:population].copy()
        a['steps'][2]['v'][:fast,0]=20
        a['steps'][2]['speed']=np.linalg.norm(a['steps'][2]['v'],axis=1)
        return a

    def test_support_default_thirty_and_exact_percentage_boundaries(self):
        default=m.prepare(self.population_peak(240,60))
        self.assertEqual(default['support_percent'],30.)
        self.assertEqual(default['metrics'][2]['stats']['required_points'],72)
        self.assertEqual(m.candidates(default,50),[])
        self.assertEqual(m.peak_stats(np.full(40,20.),np.full(40,10.),np.full(40,10.))['support_percent'],30.)
        for percent,required in ((10,24),(30,72),(100,240),(30.1,73),(33.3,80)):
            for fast in (required-1,required):
                with self.subTest(percent=percent,fast=fast):
                    model=m.prepare(self.population_peak(240,fast),support_percent=percent)
                    stats=model['metrics'][2]['stats'];rows=m.candidates(model,50)
                    self.assertEqual(stats['required_points'],required)
                    self.assertEqual(stats['points'],240)
                    self.assertEqual(stats['score'],100. if fast==required else 0.)
                    self.assertEqual(bool(rows),fast==required)
                    self.assertEqual(bool(rows),stats['score']>50)

    def test_support_keeps_twenty_point_floor_and_eligible_denominator(self):
        for population,fast,percent,expected in ((50,19,1,False),(50,20,1,True),
                                                (19,19,100,False),(20,20,100,True)):
            model=m.prepare(self.population_peak(population,fast),support_percent=percent)
            stats=model['metrics'][2]['stats']
            self.assertEqual(stats['required_points'],20)
            self.assertEqual(bool(m.candidates(model,50)),expected)
        a=self.population_peak(240,24)
        for step in a['steps']:
            step['v'][80:]=0;step['speed']=np.linalg.norm(step['v'],axis=1)
        model=m.prepare(a,support_percent=30);stats=model['metrics'][2]['stats']
        self.assertEqual(stats['points'],80);self.assertEqual(stats['required_points'],24)
        self.assertEqual(len(m.candidates(model,50)),1)

    def test_support_threshold_is_strict_and_matches_graph(self):
        model=m.prepare(self.population_peak(240,72),support_percent=30)
        self.assertEqual(model['metrics'][2]['score'],100.)
        for threshold,expected in ((99.999,True),(100.,False),(100.001,False)):
            self.assertEqual(bool(m.candidates(model,threshold)),expected)
            self.assertEqual(bool(m.candidates(model,threshold)),model['metrics'][2]['score']>threshold)

    def test_support_is_shared_by_raw_virtual_and_hold_metrics(self):
        a=analysis([10,10,0,20,10,10])
        a['steps'][3]['v'][:60,0]=40
        a['steps'][3]['speed']=np.linalg.norm(a['steps'][3]['v'],axis=1)
        models={p:m.prepare(a,support_percent=p) for p in (10,30)}
        for percent,raw_score,virtual_score in ((10,300.,100.),(30,100.,0.)):
            model=models[percent];hold=model['holds'][0]
            self.assertEqual(model['metrics'][3]['raw_score'],raw_score)
            self.assertEqual(hold['peak']['score'],virtual_score)
            self.assertTrue(all(q['stats']['support_percent']==percent for q in model['metrics']))
            self.assertEqual(hold['peak']['support_percent'],percent)
            self.assertTrue(all(q['stats'] is hold['peak'] for q in model['metrics'][2:4]))
            row=m.candidates(model,50)[0]
            self.assertEqual(row['jump'],percent==10)
            self.assertEqual(row['predicted_peak'],percent==10)
        for left,right in zip(models[10]['metrics'],models[30]['metrics']):
            np.testing.assert_array_equal(left['stats']['ids'],right['stats']['ids'])
            np.testing.assert_array_equal(left['stats']['ratios'],right['stats']['ratios'])

    def test_support_rejects_invalid_values(self):
        a=analysis([10,10,30,10,10])
        for value in (0,.5,-1,100.01,float('nan'),float('inf'),-float('inf'),None,'30','bad',True,1j):
            with self.subTest(value=value):
                with self.assertRaises(ValueError): m.prepare(a,support_percent=value)
                with self.assertRaises(ValueError): m.peak_stats([20],[10],[10],support_percent=value)
        for value in (1,100,np.float64(30.1)):
            self.assertEqual(m.prepare(a,support_percent=value)['support_percent'],float(value))

    def test_minimum_points_default_twenty_matches_explicit_setting(self):
        a=self.population_peak(50,20)
        default=m.prepare(a,support_percent=10)
        explicit=m.prepare(a,support_percent=10,min_points=20)
        self.assertEqual(default['min_points'],20)
        self.assertEqual(m.candidates(default,50),m.candidates(explicit,50))
        for left,right in zip(default['metrics'],explicit['metrics']):
            self.assertEqual(left['score'],right['score'])
            self.assertEqual(left['raw_score'],right['raw_score'])
            self.assertEqual(left['stats']['min_points'],20)
            np.testing.assert_array_equal(left['stats']['ids'],right['stats']['ids'])
            np.testing.assert_array_equal(left['stats']['ratios'],right['stats']['ratios'])

    def test_minimum_points_changes_floor_but_percentage_still_applies(self):
        cases=((50,4,5,10,5,False),(50,5,5,10,5,True),
               (50,29,30,10,30,False),(50,30,30,10,30,True),
               (50,50,100,10,100,False),
               (50,39,5,80,40,False),(50,40,5,80,40,True))
        for population,fast,floor,percent,required,expected in cases:
            with self.subTest(floor=floor,percent=percent,fast=fast):
                model=m.prepare(self.population_peak(population,fast),support_percent=percent,min_points=floor)
                stats=model['metrics'][2]['stats']
                self.assertEqual(stats['min_points'],floor)
                self.assertEqual(stats['required_points'],required)
                self.assertEqual(bool(m.candidates(model,50)),expected)
                self.assertEqual(stats['score']>50,expected)

    def test_minimum_points_strict_threshold_and_empty_population(self):
        model=m.prepare(self.population_peak(10,5),support_percent=10,min_points=5)
        self.assertEqual(model['metrics'][2]['score'],100.)
        self.assertTrue(m.candidates(model,99.999))
        self.assertEqual(m.candidates(model,100),[])
        enough=m.peak_stats([30]*5,[10]*5,[10]*5,min_points=5)
        insufficient=m.peak_stats([30]*5,[10]*5,[10]*5,min_points=6)
        self.assertEqual(enough['score'],200.)
        self.assertEqual(insufficient['score'],0.)
        empty=m.peak_stats([],[],[],min_points=1)
        self.assertEqual((empty['points'],empty['required_points'],empty['score']),(0,1,0))
        self.assertEqual(m.candidates(m.prepare(analysis([]),min_points=1)),[])

    def test_minimum_points_reaches_raw_virtual_and_hold_statistics(self):
        a=analysis([10,10,0,20,10,10])
        a['steps'][3]['v'][:60,0]=40
        a['steps'][3]['speed']=np.linalg.norm(a['steps'][3]['v'],axis=1)
        for floor,raw_score,virtual_score in ((5,300.,100.),(80,100.,0.)):
            model=m.prepare(a,support_percent=10,min_points=floor)
            hold=model['holds'][0]
            self.assertEqual(model['metrics'][3]['raw_score'],raw_score)
            self.assertEqual(hold['peak']['score'],virtual_score)
            self.assertTrue(all(q['stats']['min_points']==floor for q in model['metrics']))
            self.assertEqual(hold['peak']['min_points'],floor)
            self.assertEqual(m.candidates(model,50)[0]['jump'],floor==5)

    def test_minimum_points_does_not_change_fifty_point_hold_gate(self):
        for population,expected in ((49,0),(50,1)):
            a=analysis([10,10,0,20,10,10])
            for step in a['steps']:
                for key in ('ids','p','v','speed'): step[key]=step[key][:population].copy()
            for floor in (1,100):
                model=m.prepare(a,min_points=floor)
                self.assertEqual(len(model['holds']),expected)
                self.assertEqual(sum(r['kind']=='hold' for r in m.candidates(model,15)),expected)

    def test_minimum_points_validation_accepts_only_bounded_integer_values(self):
        a=analysis([10,10,30,10,10])
        for value,expected in ((1,1),(10000,10000),(20.,20),('20',20),('20.0',20),
                               (np.int64(30),30),(np.float64(5),5)):
            self.assertEqual(m.validate_min_points(value),expected)
            self.assertEqual(m.prepare(a,min_points=value)['min_points'],expected)
        for value in (0,-1,10001,2.5,'2.5',float('nan'),float('inf'),-float('inf'),
                      True,False,np.bool_(True),None,'','bad',1j):
            with self.subTest(value=value):
                with self.assertRaises(ValueError): m.validate_min_points(value)
                with self.assertRaises(ValueError): m.prepare(a,min_points=value)
                with self.assertRaises(ValueError): m.peak_stats([20],[10],[10],min_points=value)

    def test_excluded_hold_is_not_predicted_repaired(self):
        a=analysis([10,10,0,0,30,10,10]);model=m.prepare(a,excluded={'hold:3:5'})
        for i in (2,3,4): np.testing.assert_allclose(model['metrics'][i]['vectors'],a['steps'][i]['v'])
        rows=m.candidates(model,15)
        self.assertEqual(len(rows),1);self.assertFalse(rows[0]['included'])
        self.assertEqual(m.render_runs(rows),[])

    def test_missing_right_anchor_is_deferred_not_inserted(self):
        model=m.prepare(analysis([10,10,10,10]));metric=model['metrics'][-1]
        metric['stats']=m.peak_stats(np.full(240,30.),np.full(240,10.),np.full(240,10.))
        metric['score']=metric['stats']['score']
        rows=m.candidates(model,15);row=rows[0]
        self.assertEqual(row['targets'],[5]);self.assertFalse(row['repairable']);self.assertTrue(row['reason'])
        self.assertEqual(m.render_runs(rows),[])
        valid=self.rows([10,10,30,10])
        self.assertEqual(self.assert_fixed_runs(valid,5)[0]['targets'],[4])

    def test_long_hold_has_no_obsolete_generated_count_cap(self):
        rows=self.rows([10,10]+[0]*130+[1310,10,10])
        self.assertEqual(rows[0]['generated'],130)
        self.assertEqual(self.assert_fixed_runs(rows,136)[0]['generated'],130)

    def test_render_plans_are_idempotent_and_rebuild_after_exclusion(self):
        a=adjacent_peaks();rows=m.candidates(m.prepare(a),15)
        expected=copy.deepcopy(m.render_runs(rows))
        self.assertEqual(m.render_runs(rows),expected);self.assertEqual(m.render_runs(rows),expected)
        rows=m.candidates(m.prepare(a,excluded={'jump:3:4'}),15)
        self.assertEqual(m.render_runs(rows)[0]['targets'],[5])
        rows=m.candidates(m.prepare(a),15);self.assertEqual(m.render_runs(rows),expected)

    def test_static_boundary_modes_and_validation(self):
        for speeds in ([0]*10,[0,0,10,10],[10,10,0,0]): self.assertEqual(self.rows(speeds),[])
        a=analysis([10,10,0,0,30,10,10]);model=m.prepare(a,hold_enabled=False)
        self.assertEqual([r['kind'] for r in m.candidates(model)],['jump'])
        self.assertEqual(m.candidates(model,jump_enabled=False),[])
        self.assertEqual(m.candidates(model,threshold=250),[])
        with self.assertRaises(ValueError): m.candidates(model,float('nan'))
        with self.assertRaises(ValueError): m.prepare(a,quiet_px=-1)

    def test_unrelated_feature_ids_not_compared(self):
        a=analysis([10,10,30,10,10])
        for i,step in enumerate(a['steps']): step['ids']+=i*1000
        self.assertEqual(m.candidates(m.prepare(a)),[])


if __name__=='__main__': unittest.main()
