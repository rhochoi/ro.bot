"""Read-only product audit: only synthetic files in TemporaryDirectory; no real engine calls."""
import json, os, tempfile, time, threading
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from ro import ask, config, learn, log, mapper, organize, pet, records, route, setup, stats, distill, cli

results=[]
def record(case, **facts):
    results.append({'case':case, **facts})
def runtime(base):
    h=base/'state'; h.mkdir(exist_ok=True)
    ld=h/'log'; mp=h/'map.json'
    mapper.save_map({'projects':[], 'tools':{}},mp)
    return pet.PetRuntime(home=str(h),log_dir=str(ld),map_path=str(mp))
def call(rt,server,path,data=None,token=True,host=None):
    headers={'Content-Type':'application/json'}
    if token: headers['X-Ro-Token']=rt.token
    if host: headers['Host']=host
    req=Request(f'http://127.0.0.1:{server.server_address[1]}'+path,headers=headers,data=None if data is None else json.dumps(data).encode())
    try:
        with urlopen(req,timeout=8) as res: return res.status,json.load(res)
    except HTTPError as e: return e.code,json.load(e)

with tempfile.TemporaryDirectory(prefix='ro-product-audit-') as tmp:
    base=Path(tmp)
    rt=runtime(base)
    # Actual HTTP server with fixture storage only.
    srv=pet.start_server(rt)
    try:
        record('S01_HTTP_guards', no_token=call(rt,srv,'/api/state',token=False)[0],bad_host=call(rt,srv,'/api/state',host='invalid.test')[0],valid=call(rt,srv,'/api/state')[0])
        _,cap=call(rt,srv,'/api/capture',{'kind':'text','name':'fixture','text':'alpha 작업 완료'})
        _,rec=call(rt,srv,'/api/recall',{'question':'오늘 뭐 했지?'})
        _,rev=call(rt,srv,'/api/capture_undo',{'id':cap['id']})
        _,after=call(rt,srv,'/api/recall',{'question':'오늘 뭐 했지?'})
        record('S02_capture_recall_revoke',capture_ok=cap['ok'],found_before=rec['found'],undo_ok=rev['ok'],found_after=after['found'])
        with patch('ro.pet.route.run',return_value={'ok':True,'output':'fixture answer','code':0,'duration':0,'reason':''}) as runner:
            _,denied=call(rt,srv,'/api/ask',{'question':'alpha 설명'})
            record('S03_ask_without_preview',reason=denied.get('reason'),engine_calls=runner.call_count)
            _,pv=call(rt,srv,'/api/ask/preview',{'question':'alpha 설명'})
            rt._preview['ts']-=pet.PREVIEW_TTL_SEC+1
            _,expired=call(rt,srv,'/api/ask',{'question':'alpha 설명','preview_id':pv['id']})
            record('S04_expired_preview',reason=expired.get('reason'),engine_calls=runner.call_count)
            _,pv=call(rt,srv,'/api/ask/preview',{'question':'alpha 설명'})
            _,ans=call(rt,srv,'/api/ask',{'question':'alpha 설명','preview_id':pv['id']})
            for _ in range(3): call(rt,srv,'/api/feedback',{'accepted':True})
            ev=log.read_all(rt.log_dir)
            record('S05_question_feedback',answer_ok=ans['ok'],routes=sum(e['kind']=='route' for e in ev),feedbacks=sum(e['kind']=='feedback' for e in ev),engine_calls=runner.call_count)
    finally:
        srv.shutdown(); srv.server_close()

    # Same-second record identity collision.
    ld=base/'collision-log'; fixed=time.time()
    with patch('ro.log.time.time',return_value=fixed):
        a=records.capture('text','one','동일 메모',0,[],log_dir=ld)
        b=records.capture('text','two','동일 메모',0,[],log_dir=ld)
    records.revoke(a['id'],log_dir=ld)
    record('S06_record_identity_collision',same_id=a['id']==b['id'],remaining=len(records.live(log.read_all(ld))))

    # Removing the last allowed root re-exposes an existing cached map.
    h=base/'roots-state'; h.mkdir(); root=base/'allowed'; root.mkdir(); project=root/'alpha'; project.mkdir()
    p={'name':'alpha','path':str(project),'languages':[],'summary':{'purpose':'FIXTURE_PRIVATE_CONTEXT'}}
    mp=h/'map.json'; mapper.save_map({'projects':[p],'tools':{}},mp)
    config.update(h,roots=[str(root)])
    setup.remove_roots([str(root)],h)
    record('S07_last_root_removed',saved_roots=config.load(h)['roots'],visible_projects=[p['name'] for p in ask.load_map(mp,h)['projects']],next_scan_roots=mapper.default_roots(h))

    # Approval uses cached permission scope.
    config.update(rt.home,roots=[str(root)])
    mapper.save_map({'projects':[p],'tools':{}},rt.map_path)
    pv=rt.preview_question('alpha 설명',engine='codex')
    other=base/'other'; other.mkdir(); config.update(rt.home,roots=[str(other)])
    with patch('ro.pet.route.run',return_value={'ok':True,'output':'fixture','code':0,'duration':0,'reason':''}) as runner:
        out=rt.ask_question('alpha 설명',preview_id=pv['id'])
        record('S08_scope_changed_after_preview',current_projects=len(ask.load_map(rt.map_path,rt.home)['projects']),sent_revoked_context='FIXTURE_PRIVATE_CONTEXT' in runner.call_args.args[1],ok=out['ok'])

    # Approved memory file isn't included by the question planner.
    mh=base/'memory-state'; mh.mkdir(); mem=mh/'ro-model.md'
    cand={'id':'fixture-memory','kind':'memory','text':'답변에 FIXTURE_MEMORY_ZEBRA를 사용','evidence':'fixture','source':'audit'}
    learn.add_pending([cand],mh); learn.approve(cand['id'],mh,memory_path=mem)
    plan=ask.plan('내 선호대로 답해줘',{'projects':[],'tools':{}},rules=learn.load_rules(mh),log_dir=base/'empty')
    record('S09_memory_consumption',approved=bool(learn.load_rules(mh)),file_contains_memory='FIXTURE_MEMORY_ZEBRA' in mem.read_text(),prompt_contains_memory='FIXTURE_MEMORY_ZEBRA' in plan['prompt'])
    # Failed storage after candidate removal makes an approval unretryable.
    cand2={**cand,'id':'fixture-memory-fail'}; learn.add_pending([cand2],mh)
    try:
        with patch('ro.learn.append_memory',side_effect=OSError('fixture disk failure')):
            learn.approve(cand2['id'],mh,memory_path=mem)
    except OSError: pass
    record('S10_memory_partial_commit',pending_after_error=any(c['id']==cand2['id'] for c in learn.pending(mh)),approved_after_error=any(c['id']==cand2['id'] for c in learn.load_rules(mh)))

    # File organization: fixture user's home, real file movements and journals.
    with patch('pathlib.Path.home',return_value=base):
        root2=base/'downloads'; root2.mkdir(); dst=base/'sorted'
        f=root2/'sample.png'; f.write_bytes(b'fixture image'); os.utime(f,(1,1))
        plan=organize.scan(root2,dst,extractor=lambda p:{})
        organize.save_plan(plan,rt.home)
        cid=plan['items'][0]['id']; cat=plan['items'][0]['category']
        q=rt.queue_organize_apply(category=cat)
        displayed=q['pending']['files'][0]['to']
        organize.edit(cid,name='different-approved-name',home=rt.home,log_dir=rt.log_dir)
        out=rt.resolve_write_approval(True)
        actual=Path(organize.pending(rt.home)[-1]['dst']).name
        record('S11_plan_edited_after_preview',displayed=displayed,actual=actual,ok=out['ok'],same=displayed==actual)
        b,done,skips=organize.undo(home=rt.home,log_dir=rt.log_dir)
        record('S12_organize_restore',restored=len(done),skipped=len(skips),content_restored=f.read_bytes()==b'fixture image')
        # Rescan, make journal append fail after successful move.
        plan=organize.scan(root2,dst,extractor=lambda p:{})
        organize.save_plan(plan,rt.home)
        before=len(organize.pending(rt.home))
        try:
            with patch('ro.organize._append',side_effect=OSError('fixture journal failure')):
                organize.apply(ids=[cid],home=rt.home,log_dir=rt.log_dir)
        except OSError: pass
        record('S13_move_without_journal',source_exists=f.exists(),dest_exists=Path(plan['items'][0]['dest']).exists(),new_undo_entries=len(organize.pending(rt.home))-before)

    # Revoked records disagree with stats and external activity context.
    ld2=base/'rev-log'; day=time.strftime('%Y-%m-%d')
    e=log.append('activity','alpha fixture commit',{'source':'git','ref':'/fixture/alpha','id':'abc'},log_dir=ld2)
    records.revoke(records.eid(e),log_dir=ld2)
    record('S14_revoke_consumers',recall_found=__import__('ro.recall',fromlist=['answer']).answer('오늘 뭐 했지?',log.read_all(ld2),[],home=mh)['found'],stats_activity=stats.daily(day,ld2)['activity'],external_context_contains_revoked='alpha fixture commit' in ask.activity_context('오늘 뭐 했지?',ld2))
    # Deleting raw logs doesn't invalidate precomputed stats or conversation history.
    dh=base/'delete-state'; dh.mkdir(); dlog=dh/'log'
    for ago in range(3):
        at=time.time()-86400*ago; day2=time.strftime('%Y-%m-%d',time.localtime(at))
        log.append('activity','python test',{'source':'shell','ref':str(ago)},log_dir=dlog,at=at)
        stats.write(day2,dh,dlog)
    ask.save_history('FIXTURE_DELETED_QUESTION','FIXTURE_DELETED_ANSWER',dh)
    log.delete(log_dir=dlog)
    new,_,days=distill.run(days=3,home=dh,log_dir=dlog)
    record('S15_delete_derived_state',raw_events=len(log.read_all(dlog)),stats_days=days,memory_candidates=len(new),history_survives=ask.load_history(dh) is not None)

Path('/workspace/product-audit/connection-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
for r in results: print(json.dumps(r,ensure_ascii=False))
