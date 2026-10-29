"""Sparse, spatially distributed LK trajectories. All distances use source pixels.

Steps retain feature IDs: temporal comparisons never compare unrelated populations.
No synthesis is needed for the virtual hold-repair analysis.
"""
import math
import os
import subprocess
import tempfile
import threading
import cv2
import numpy as np
import hold_core as core


def scan(path, width=960, progress=None, cancel=None):
    cancel = cancel or threading.Event()
    stat = os.stat(path); meta = core.probe(path)
    if meta['transfer'] in ('smpte2084', 'arib-std-b67'):
        raise ValueError('현재 분석은 SDR 영상용입니다.')
    w = min(width, meta['width']); h = max(1, round(meta['height']*w/meta['width']))
    scale = meta['width']/w
    steps=[]; ids=np.empty(0,dtype=np.int64); points=np.empty((0,2),np.float32)
    next_id=0; frames=0; previous=None; done=threading.Event()
    cmd=[core.binary('ffmpeg'),'-v','error','-nostdin','-noautorotate','-i',str(path),
         '-map','0:v:0','-an','-sn','-dn','-vf',f'scale={w}:{h},format=gray',
         '-fps_mode','passthrough','-f','rawvideo','pipe:1']
    lk=dict(winSize=(31,31),maxLevel=4,criteria=(3,40,.001))
    with tempfile.TemporaryFile() as errors:
        process=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=errors,creationflags=core.FLAGS)
        def watch():
            while not done.wait(.1):
                if cancel.is_set():
                    if process.poll() is None: process.kill()
                    return
        threading.Thread(target=watch,daemon=True).start()
        try:
            while True:
                if cancel.is_set(): raise core.Cancelled()
                data=bytearray()
                while len(data)<w*h:
                    part=process.stdout.read(w*h-len(data))
                    if not part: break
                    data.extend(part)
                if cancel.is_set(): raise core.Cancelled()
                if not data: break
                if len(data)!=w*h: raise RuntimeError('불완전한 영상 프레임입니다.')
                image=np.frombuffer(data,np.uint8).reshape(h,w)
                if previous is not None:
                    old=points.copy()
                    if len(points):
                        q,s,_=cv2.calcOpticalFlowPyrLK(previous,image,points[:,None],None,**lk)
                        back,s2,_=cv2.calcOpticalFlowPyrLK(image,previous,q,None,**lk)
                        q=q[:,0];back=back[:,0]
                        keep=(s[:,0]>0)&(s2[:,0]>0)&(np.linalg.norm(back-points,axis=1)<.75)
                        keep &= np.isfinite(q).all(axis=1)&(q[:,0]>=0)&(q[:,0]<w)&(q[:,1]>=0)&(q[:,1]<h)
                        ids=ids[keep];points=q[keep];old=old[keep]
                    step=dict(ids=ids.copy(),p=old.copy(),v=(points-old)*scale)
                    step['speed']=np.linalg.norm(step['v'],axis=1)
                    steps.append(step)
                # Replenish on losses as well as periodically; never reuse an ID.
                if frames%8==0 or len(points)<200:
                    mask=np.full((h,w),255,np.uint8)
                    for pt in points: cv2.circle(mask,tuple(np.round(pt).astype(int)),7,0,-1)
                    additions=[]
                    for gy in range(6):
                        for gx in range(10):
                            x0,x1=gx*w//10,(gx+1)*w//10;y0,y1=gy*h//6,(gy+1)*h//6
                            existing=((points[:,0]>=x0)&(points[:,0]<x1)&(points[:,1]>=y0)&(points[:,1]<y1)).sum()
                            need=12-int(existing)
                            if need<=0: continue
                            pts=cv2.goodFeaturesToTrack(image[y0:y1,x0:x1],maxCorners=need,qualityLevel=.02,
                                minDistance=7,blockSize=5,mask=mask[y0:y1,x0:x1])
                            if pts is not None: additions.extend(pts[:,0]+[x0,y0])
                    if additions:
                        points=np.concatenate([points,np.asarray(additions,np.float32)])
                        ids=np.r_[ids,np.arange(next_id,next_id+len(additions))];next_id+=len(additions)
                previous=image;frames+=1
                if progress: progress(frames,meta['estimated'])
            if process.wait():
                errors.seek(0);raise RuntimeError(errors.read().decode('utf-8','replace')[-1500:])
        finally:
            done.set()
            if process.poll() is None: process.kill()
            process.wait();process.stdout.close()
    if not frames: raise ValueError('디코딩된 프레임이 없습니다.')
    end=os.stat(path)
    if (stat.st_size,stat.st_mtime_ns)!=(end.st_size,end.st_mtime_ns): raise RuntimeError('분석 중 원본이 변경됐습니다.')
    return dict(path=str(path),metadata=meta,frames=frames,steps=steps,analysis_width=w,analysis_height=h,
        source_stat=dict(size=end.st_size,mtime_ns=end.st_mtime_ns),method='same-feature bidirectional sparse LK')


def common(steps, indices):
    """Rows aligned by common feature ID across arbitrary step indices."""
    if not indices or min(indices)<0 or max(indices)>=len(steps): return [],[]
    ids=steps[indices[0]]['ids']
    for i in indices[1:]: ids=np.intersect1d(ids,steps[i]['ids'],assume_unique=True)
    return ids,[np.searchsorted(steps[i]['ids'],ids) for i in indices]


def _validate_support_percent(value):
    try:
        if isinstance(value,(str,bytes,bool)): raise ValueError()
        value=float(value)
    except (TypeError,ValueError,OverflowError):
        raise ValueError('추적점 비율은 1~100 사이의 숫자여야 합니다.') from None
    if not math.isfinite(value) or not 1<=value<=100:
        raise ValueError('추적점 비율은 1~100 사이의 숫자여야 합니다.')
    return value


def validate_min_points(value):
    """Validate UI/CLI values without rounding a fractional point count."""
    try:
        if isinstance(value,(bool,np.bool_)): raise ValueError()
        number=float(value)
    except (TypeError,ValueError,OverflowError):
        raise ValueError('최소 추적점 수는 1~10000 사이의 정수여야 합니다.') from None
    if not math.isfinite(number) or not number.is_integer() or not 1<=number<=10000:
        raise ValueError('최소 추적점 수는 1~10000 사이의 정수여야 합니다.')
    return int(number)


def _required_points(points,support_percent,min_points=20):
    return max(min_points,math.ceil(points*support_percent/100))


def peak_stats(current,before,after,min_move=1.0,ids=None,support_percent=30.0,min_points=20):
    support_percent=_validate_support_percent(support_percent)
    min_points=validate_min_points(min_points)
    current=np.asarray(current);before=np.asarray(before);after=np.asarray(after)
    ids=np.arange(len(current),dtype=np.int64) if ids is None else np.asarray(ids)
    eligible=np.isfinite(current)&np.isfinite(before)&np.isfinite(after)
    eligible &= (current>=min_move)&(np.maximum(before,after)>=min_move)
    c=current[eligible];b=before[eligible];a=after[eligible]
    ratio=c/np.maximum(np.maximum(b,a),min_move)
    # The plotted boundary must match BOTH support requirements exactly:
    # the selected point floor and share of the eligible population.
    required=_required_points(len(ratio),support_percent,min_points)
    score=max(0.,float(np.sort((ratio-1)*100)[-required])) if len(ratio)>=required else 0.
    return dict(ids=ids[eligible],score=score,ratios=ratio,points=len(ratio),
                uses_prediction=False,prediction_holds=[],support_percent=support_percent,
                min_points=min_points,required_points=required)


def fast_feature_ids(metric,threshold):
    """One threshold calculation shared by the plot, detail and vector overlay."""
    stats=metric['stats'];ratios=stats['ratios']
    return stats['ids'][ratios>1+threshold/100]


def validate_manual_ranges(ranges,frames):
    """Normalize fixed original anchors; only a shared endpoint may overlap."""
    def frame_number(value):
        try:
            if isinstance(value,(bool,np.bool_)): raise ValueError()
            number=float(value)
        except (TypeError,ValueError,OverflowError):
            raise ValueError('수동 추가 프레임은 정수여야 합니다.') from None
        if not math.isfinite(number) or not number.is_integer():
            raise ValueError('수동 추가 프레임은 정수여야 합니다.')
        return int(number)
    frames=frame_number(frames)
    if frames<1: raise ValueError('영상의 전체 프레임 수가 올바르지 않습니다.')
    if ranges is None: ranges=[]
    if not isinstance(ranges,(list,tuple)): raise ValueError('수동 추가 구간은 목록이어야 합니다.')
    result=[]
    for item in ranges:
        if not isinstance(item,dict) or 'left' not in item or 'right' not in item:
            raise ValueError('수동 추가 구간의 양끝 프레임이 필요합니다.')
        left,right=frame_number(item['left']),frame_number(item['right'])
        if not 1<=left<right<=frames or right-left<2:
            raise ValueError('영상 범위 안에서 교체할 프레임이 포함되도록 양끝을 선택하세요.')
        key=f'replace:{left}:{right}'
        if item.get('key',key)!=key: raise ValueError('수동 추가 구간의 키와 프레임이 일치하지 않습니다.')
        result.append(dict(key=key,left=left,right=right))
    result.sort(key=lambda r:(r['left'],r['right']))
    for previous,current in zip(result,result[1:]):
        if current['left']<previous['right']:
            raise ValueError('수동 추가 구간은 겹칠 수 없습니다. 양끝 프레임만 공유할 수 있습니다.')
    return result


def _manual_conflict(targets,anchors,manual_ranges):
    """Manual anchors stay original, and automatic anchors cannot be generated."""
    return any(any(r['left']<=t<=r['right'] for t in targets) or
               any(r['left']<a<r['right'] for a in anchors) for r in manual_ranges)


def _hold_constraints(holds,excluded,frames,manual_ranges=()):
    """An excluded repeated image must not become another repair's anchor."""
    protected=set();blocked_anchors=set();reasons={}
    for key in excluded:
        parts=key.split(':')
        if len(parts)==3 and parts[0]=='jump':
            try: target=int(parts[2])
            except ValueError: continue
            if 1<=target<=frames and not _manual_conflict([target],[target-1,target+1],manual_ranges):
                protected.add(target)
    # Disabled automatic rows must not propagate exclusions into an explicit
    # manual range, or into neighboring automatic repairs through a hold.
    holds=[r for r in holds if not _manual_conflict(range(r['start']+1,r['end']+1),
                                                  [r['start'],r['end']+1],manual_ranges)]
    for r in holds:
        if r['key'] in excluded:
            repeated=set(range(r['start'],r['end']+1))
            protected.update(repeated);blocked_anchors.update(repeated)
            reasons[r['key']]='사용자가 제외한 홀드 구간입니다.'
    changed=True
    while changed:
        changed=False
        for r in holds:
            if r['key'] in reasons: continue
            targets=set(range(r['start']+1,r['end']+1))
            anchors={r['start'],r['end']+1}
            reason=''
            if r['start']<1 or r['end']>=frames: reason='앞뒤 기준 프레임이 없어 교체할 수 없습니다.'
            elif targets & protected: reason='제외한 프레임과 겹쳐 홀드 교체를 보류합니다.'
            elif anchors & blocked_anchors: reason='제외되거나 보류된 홀드가 기준 프레임에 있어 교체를 보류합니다.'
            if reason:
                reasons[r['key']]=reason;changed=True
                repeated=set(range(r['start'],r['end']+1))
                protected.update(repeated);blocked_anchors.update(repeated)
    return reasons,protected,blocked_anchors


def prepare(analysis,hold_enabled=True,quiet_px=.5,min_move=1.,*,excluded=None,support_percent=30.0,hold_min_move=1.0,min_points=20,manual_ranges=None):
    """Threshold-independent model, cached by UI while the graph line is dragged.

    An excluded hold is left untouched in the predicted timeline and is still
    represented by one event; it cannot silently reappear as a separate jump.
    """
    if not math.isfinite(quiet_px) or quiet_px<0 or not math.isfinite(min_move) or min_move<=0:
        raise ValueError('홀드 이동량은 0 이상, 최소 이동량은 0보다 커야 합니다.')
    try:
        if isinstance(hold_min_move,bool): raise ValueError()
        hold_min_move=float(hold_min_move)
    except (TypeError,ValueError,OverflowError):
        raise ValueError('홀드 문맥 최소 이동량은 0보다 큰 유한한 숫자여야 합니다.') from None
    if not math.isfinite(hold_min_move) or hold_min_move<=0:
        raise ValueError('홀드 문맥 최소 이동량은 0보다 큰 유한한 숫자여야 합니다.')
    support_percent=_validate_support_percent(support_percent)
    min_points=validate_min_points(min_points)
    excluded=set(excluded or ())
    steps=analysis.get('steps',[]);w=analysis.get('analysis_width',1);h=analysis.get('analysis_height',1)
    frames=int(analysis.get('frames',len(steps)+1))
    manual_ranges=validate_manual_ranges(manual_ranges,frames)
    active_manual=[r for r in manual_ranges if r['key'] not in excluded]
    quiet=[]
    for s in steps:
        values=s['speed'];still=values<=quiet_px;cells=good=0
        cell=np.minimum(9,(s['p'][:,0]*10/w).astype(int))+10*np.minimum(5,(s['p'][:,1]*6/h).astype(int))
        for c in np.unique(cell):
            m=cell==c
            if m.sum()>=3: cells+=1;good+=int(still[m].mean()>=.8)
        quiet.append(bool(len(values)>=50 and cells>=12 and still.mean()>=.9 and good/cells>=.8))
    holds=[];spans={};i=0
    while i<len(steps):
        if not quiet[i]: i+=1;continue
        start=i
        while i+1<len(steps) and quiet[i+1]: i+=1
        end=i;confirmed=False;ratio=None;count=end-start+1
        # Context around the entire stop, including the exit movement.
        indices=list(range(start-1,end+2))
        ids,idx=common(steps,indices)
        if len(ids):
            speeds=np.array([steps[j]['speed'][ix] for j,ix in zip(indices,idx)])
            # Jump filtering may scale with image width; it must not change
            # the existing sensitivity used to confirm a repeated-frame hold.
            baseline=(speeds[0]+speeds[-1])/2;moving=baseline>=hold_min_move
            if moving.sum()>=50:
                ratio=float(np.median(speeds[1:-1,moving].mean(axis=0)/baseline[moving]))
                confirmed=ratio<=.25
        if confirmed:
            # The complete span goes from the first occurrence to the next
            # normal frame. A track missing any span endpoint is unknown;
            # never fall back to its original zero/exit speed after repair.
            span_indices=list(range(start,end+2))
            ids,idx=common(steps,span_indices)
            displacement=sum((steps[j]['v'][ix] for j,ix in zip(span_indices,idx)),np.zeros((len(ids),2),np.float32))
            key=f'hold:{start+1}:{end+2}'
            holds.append(dict(key=key,kind='hold',start=start+1,end=end+2,copies=count,
                ratio=ratio,stats=None,active=key not in excluded))
            spans[key]=dict(start=start,end=end+1,ids=np.asarray(ids),displacement=displacement)
        i+=1

    hold_steps={j for span in spans.values() for j in range(span['start'],span['end']+1)}
    for r in holds:
        count=r['copies']
        blocked=_manual_conflict(range(r['start']+1,r['end']+1),[r['start'],r['end']+1],active_manual)
        r.update(generated=count,delta=0,blocked_by_manual=blocked)
    constraints,protected,blocked_anchors=_hold_constraints(holds,excluded,frames,active_manual)
    for r in holds: r['active']=r['key'] not in constraints and not r['blocked_by_manual']

    # A block is an ordinary source interval or a whole repaired hold span.
    # Its average vector is one corrected interval. Every repeated source
    # frame is replaced, so the number of intervals remains unchanged.
    # Collapsing the semantic span also exposes a genuinely fast plateau to
    # its outside neighbors instead of comparing it against itself.
    active={spans[r['key']]['start']:r for r in holds if hold_enabled and r['active']}
    occupied=hold_steps if hold_enabled else set()
    blocks=[];source_blocks={};i=0
    while i<len(steps):
        r=active.get(i)
        if r is not None:
            span=spans[r['key']];end=span['end'];intervals=r['generated']+1
            block=dict(start=i,end=end,ids=span['ids'],v=span['displacement']/intervals,
                       intervals=intervals,hold=r)
        else:
            end=i;block=dict(start=i,end=i,ids=steps[i]['ids'],v=steps[i]['v'],intervals=1,hold=None)
        block['speed']=np.linalg.norm(block['v'],axis=1)
        for j in range(i,end+1): source_blocks[j]=len(blocks)
        blocks.append(block);i=end+1

    for i,block in enumerate(blocks):
        ids,idx=common(blocks,[i-1,i,i+1])
        if len(ids):
            st=peak_stats(block['speed'][idx[1]],blocks[i-1]['speed'][idx[0]],
                          blocks[i+1]['speed'][idx[2]],min_move,ids,support_percent,min_points)
            # Record actual inputs, not distance from a hold. A neighboring
            # predicted speed makes this comparison an estimate even when
            # the current interval itself is measured from original frames.
            prediction_holds=[blocks[j]['hold']['key'] for j in (i-1,i,i+1) if blocks[j]['hold'] is not None]
            st.update(uses_prediction=bool(prediction_holds),prediction_holds=prediction_holds)
        else: st=peak_stats([],[],[],min_move,support_percent=support_percent,min_points=min_points)
        block['stats']=st
        if block['hold'] is not None:
            r=block['hold'];r['peak']=st
            if len(ids):
                r['stats']=(ids,block['speed'][idx[1]]*block['intervals'],
                            blocks[i-1]['speed'][idx[0]],blocks[i+1]['speed'][idx[2]])

    metrics=[]
    for i,s in enumerate(steps):
        ids,idx=common(steps,[i-1,i,i+1])
        if len(ids):
            raw=peak_stats(s['speed'][idx[1]],steps[i-1]['speed'][idx[0]],steps[i+1]['speed'][idx[2]],min_move,ids,support_percent,min_points)
        else: raw=peak_stats([],[],[],min_move,support_percent=support_percent,min_points=min_points)
        block=blocks[source_blocks[i]];st=block['stats']
        metrics.append(dict(start=i+1,end=i+2,score=st['score'],raw_score=raw['score'],stats=st,
            uses_prediction=st['uses_prediction'],prediction_holds=st['prediction_holds'],
            speed=float(np.median(s['speed'])) if len(s['speed']) else None,
            virtual_speed=float(np.median(block['speed'])) if len(block['speed']) else None,
            vector_ids=block['ids'],vectors=block['v'],block_index=source_blocks[i],
            quiet=quiet[i],occupied=i in occupied))
    for r in holds:
        if 'peak' not in r: r['peak']=peak_stats([],[],[],min_move,support_percent=support_percent,min_points=min_points)
    return dict(holds=holds,metrics=metrics,hold_enabled=hold_enabled,min_move=min_move,manual_ranges=manual_ranges,
                frames=frames,excluded=sorted(excluded),protected_targets=sorted(protected),blocked_anchors=sorted(blocked_anchors),
                support_percent=support_percent,hold_min_move=hold_min_move,min_points=min_points)


def candidates(model,threshold=50.,jump_enabled=True,*,excluded=None):
    if not math.isfinite(threshold) or threshold<0: raise ValueError('점프 기준은 0 이상의 숫자입니다.')
    excluded=set(model.get('excluded',()) if excluded is None else excluded)
    support_percent=_validate_support_percent(model.get('support_percent',30.0))
    min_points=validate_min_points(model.get('min_points',20))
    frames=model['frames']
    manual_ranges=model.get('manual_ranges',[])
    active_manual=[r for r in manual_ranges if r['key'] not in excluded]
    constraints,protected,blocked_anchors=_hold_constraints(model['holds'],excluded,frames,active_manual)
    def passes(st):
        ratios=st['ratios'];peaks=ratios>1+threshold/100
        # Detection depends only on the motion evidence; replacement length
        # is determined by the selected original target frames.
        return bool(np.count_nonzero(peaks)>=_required_points(len(ratios),support_percent,min_points))
    result=[]
    if model['hold_enabled']:
        for source in model['holds']:
            r={k:v for k,v in source.items() if k not in ('stats','peak')}
            peak=source['peak'];detected=jump_enabled and passes(peak)
            r.update(score=peak['score'],jump=detected,
                     predicted_peak=detected and peak['uses_prediction'],uses_prediction=peak['uses_prediction'],
                     prediction_holds=peak['prediction_holds'],included=source['key'] not in excluded)
            r.update(targets=list(range(r['start']+1,r['end']+1)),repairable=r['key'] not in constraints or r['key'] in excluded,
                     reason=constraints.get(r['key'],'') if r['key'] not in excluded else '',manual=False)
            result.append(r)
    if jump_enabled:
        for m in model['metrics']:
            if m['occupied']: continue
            if not passes(m['stats']): continue
            key=f'jump:{m["start"]}:{m["end"]}'
            # Provenance describes the comparison inputs, not eligibility.
            # A peak surviving the assumed hold repair is still actionable.
            predicted_peak=m['stats']['uses_prediction'];count=1
            result.append(dict(key=key,kind='jump',
                start=m['start'],end=m['start'],right=m['end'],copies=count,
                generated=count,delta=0,score=m['score'],
                jump=True,manual=False,predicted_peak=predicted_peak,
                uses_prediction=m['stats']['uses_prediction'],prediction_holds=m['stats']['prediction_holds'],
                targets=[m['end']],repairable=True,reason='',
                included=key not in excluded))
    for source in manual_ranges:
        left,right=source['left'],source['right'];count=right-left-1
        result.append(dict(key=source['key'],kind='replace',start=left,end=right-1,right=right,
            targets=list(range(left+1,right)),copies=count,generated=count,delta=0,
            score=0.,jump=False,manual=True,predicted_peak=False,uses_prediction=False,prediction_holds=[],
            repairable=True,reason='',included=source['key'] not in excluded))
    result=sorted(result,key=lambda r:(r['start'],r['kind']))
    for r in result:
        targets=r['targets']
        r.update(source_frames=frames,protected_targets=sorted(protected),blocked_anchors=sorted(blocked_anchors),
                 repair_span=[targets[0],targets[-1]] if targets else [],
                 repair_anchors=[targets[0]-1,targets[-1]+1] if targets else [])
        if r['kind']!='replace' and _manual_conflict(targets,r['repair_anchors'],active_manual):
            r.update(blocked_by_manual=True,included=False,repairable=False,reason='수동 추가 구간과 겹침')
        else: r['blocked_by_manual']=False
    _replacement_runs(result)
    return result


def render_runs(candidates):
    return _replacement_runs(candidates)


def _replacement_runs(rows):
    """Preserve manual anchors; merge only compatible automatic target frames.

    The resulting anchors are untouched original frames. A target excluded
    through one automatic row cannot reappear through another automatic row.
    Explicit manual ranges take priority over automatic exclusions.
    """
    manuals=[r for r in rows if r['kind']=='replace' and r['included'] and r['repairable']]
    active_manual=[dict(left=r['start'],right=r['right']) for r in manuals]
    automatic=[r for r in rows if r['kind']!='replace']
    for r in automatic:
        anchors=[min(r['targets'])-1,max(r['targets'])+1] if r['targets'] else []
        if _manual_conflict(r['targets'],anchors,active_manual):
            r.update(blocked_by_manual=True,included=False,repairable=False,reason='수동 추가 구간과 겹침')
    protected=set();blocked_anchors=set()
    for r in automatic:
        if r.get('blocked_by_manual'): continue
        protected.update(r.get('protected_targets',()))
        blocked_anchors.update(r.get('blocked_anchors',()))
        if not r['included']:
            protected.update(r['targets'])
            if r['kind']=='hold':
                repeated=set(range(r['start'],r['end']+1))
                protected.update(repeated);blocked_anchors.update(repeated)

    def defer(row,reason):
        row['repairable']=False;row['reason']=reason
        if row['kind']=='hold':
            repeated=set(range(row['start'],row['end']+1))
            protected.update(repeated);blocked_anchors.update(repeated)

    while True:
        changed=False
        selected=[r for r in automatic if r['included'] and r['repairable'] and r['targets']]
        for r in selected:
            if set(r['targets']) & protected:
                defer(r,'제외하거나 보류한 프레임과 겹쳐 교체를 보류합니다.');changed=True
            elif min(r['targets'])<=1 or max(r['targets'])>=r['source_frames']:
                defer(r,'앞뒤 기준 프레임이 없어 교체할 수 없습니다.');changed=True
        if changed: continue
        owners={}
        for r in selected:
            for target in r['targets']: owners.setdefault(target,[]).append(r)
        groups=[]
        for target in sorted(owners):
            if not groups or target!=groups[-1][-1]+1: groups.append([])
            groups[-1].append(target)
        for targets in groups:
            if {targets[0]-1,targets[-1]+1} & blocked_anchors:
                for target in targets:
                    for r in owners[target]:
                        if r['repairable']:
                            defer(r,'제외되거나 보류된 홀드가 기준 프레임에 있어 교체를 보류합니다.');changed=True
        if not changed: break
    result=[]
    for targets in groups:
        keys=[]
        for target in targets:
            for r in owners[target]:
                r['repair_span']=[targets[0],targets[-1]]
                r['repair_anchors']=[targets[0]-1,targets[-1]+1]
                if r['key'] not in keys: keys.append(r['key'])
        result.append(dict(start=targets[0]-1,end=targets[-1],copies=len(targets),generated=len(targets),
                           targets=targets,source_keys=keys))
    for r in manuals:
        # Never merge a manual range with another row or move its selected
        # endpoints. Shared original endpoints produce separate valid runs.
        left,right=r['start'],r['right'];targets=list(range(left+1,right))
        r['repair_span']=[left+1,right-1];r['repair_anchors']=[left,right]
        result.append(dict(start=left,end=right-1,copies=len(targets),generated=len(targets),
                           targets=targets,source_keys=[r['key']]))
    return sorted(result,key=lambda r:r['start'])
