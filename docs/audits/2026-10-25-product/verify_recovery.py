import contextlib, io, json, os, tempfile, threading, time
from pathlib import Path
from unittest.mock import patch
from ro import ask, cli, config, learn, log, mapper, organize, pet, records, route
from playwright.sync_api import sync_playwright
out=[]
def record(case,**kw): out.append(dict(case=case,**kw))
with tempfile.TemporaryDirectory(prefix='ro-recovery-audit-') as tmp:
    base=Path(tmp); home=base/'state'; home.mkdir(); ld=home/'log'; mp=home/'map.json'
    mapper.save_map({'projects':[],'tools':{}},mp)
    rt=pet.PetRuntime(home=str(home),log_dir=str(ld),map_path=str(mp))
    begun=threading.Event()
    def fake_run(*a,**kw):
        begun.set(); cancelled=kw['cancel'].wait(3)
        return {'ok':not cancelled,'output':'fixture','code':None if cancelled else 0,'duration':0,'reason':'cancelled' if cancelled else ''}
    with patch('ro.pet.route.run',side_effect=fake_run):
        pv=rt.preview_question('설명')
        answers=[]; worker=threading.Thread(target=lambda:answers.append(rt.ask_question('설명',preview_id=pv['id'])))
        worker.start(); begun.wait(2)
        collision=rt.ask_question('다른 설명')
        cancel=rt.cancel_ask(); worker.join(4)
    record('S16_busy_cancel',second_rejected=collision.get('busy'),cancel_ok=cancel['ok'],cancelled=answers[0]['cancelled'],busy_after=rt.busy)
    fresh=pet.PetRuntime(home=str(home),log_dir=str(ld),map_path=str(mp))
    record('S17_restart_state',last_answer_restored=fresh.last_answer is not None,preview_restored=fresh._preview is not None,route_events_preserved=sum(e['kind']=='route' for e in log.read_all(ld)))
    # Primary route entrypoint passes unredacted task directly. No network call.
    raw='password=FIXTURE_NOT_A_REAL_SECRET'
    with patch('ro.cli.route.run',return_value={'ok':True,'output':'fixture','code':0,'duration':0,'reason':''}) as run, patch('ro.cli.log.append'), patch('ro.cli.learn.load_rules',return_value=[]),patch('ro.cli.config.load',return_value={}), contextlib.redirect_stdout(io.StringIO()):
        try: cli.main(['route',raw,'--engine','codex'])
        except SystemExit: pass
        passed=run.call_args.args[1]==raw
    record('S18_route_privacy_contract',unmasked_task_reaches_runner=passed)
    # Real Chromium and HTTP server, fixture files only.
    server=pet.start_server(rt)
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(**({'executable_path':os.environ['RO_AUDIT_CHROMIUM']} if os.environ.get('RO_AUDIT_CHROMIUM') else {}))
            page=browser.new_page(viewport={'width':900,'height':760})
            requests=[]; page.on('request',lambda r: requests.append(r.url.split('/')[-1]))
            page.goto(f'http://127.0.0.1:{server.server_address[1]}/')
            page.evaluate('setBubbleOpen(true)')
            page.locator('#askInput').fill('/기록 alpha 정상 기록')
            page.locator('#askForm').evaluate('(e)=>e.requestSubmit()')
            page.wait_for_function("document.querySelector('#toast').textContent.includes('alpha')")
            page.locator('#askInput').fill('오늘 뭐 했지?')
            page.locator('#askForm').evaluate('(e)=>e.requestSubmit()')
            page.wait_for_function("document.querySelector('#recallCard').textContent.includes('alpha')")
            record('S19_browser_record_recall',visible='alpha' in page.locator('#recallCard').inner_text(),capture_request='capture' in requests,recall_request='recall' in requests)
            # Persist preference failure is silently accepted by UI.
            page.locator('[data-tab="settings"]').click()
            with patch.object(rt,'set_prefs',return_value={'ok':False,'error':'fixture save rejected'}):
                page.locator('[data-mat="glass"]').click(); page.wait_for_timeout(250)
            record('S20_browser_preferences_failure',glass_shown=page.evaluate("document.body.classList.contains('material-glass')"),glass_persisted=rt.prefs().get('material')=='glass',error_visible='fixture save rejected' in page.locator('body').inner_text())
            page.reload(); page.wait_for_selector('#stage')
            record('S20b_browser_reload',glass_after_reload=page.evaluate("document.body.classList.contains('material-glass')"))
            browser.close()
    finally: server.shutdown(); server.server_close()
    with patch('pathlib.Path.home',return_value=base):
        root=base/'downloads'; root.mkdir(); dst=base/'sorted'
        f=root/'picture.png'; f.write_bytes(b'image fixture'); os.utime(f,(1,1))
        plan=organize.scan(root,dst,extractor=lambda p:{}); organize.save_plan(plan,home)
        cid=plan['items'][0]['id']; original=time.strftime
        def fixed_batch(fmt,*args): return '20261025-120000' if fmt=='%Y%m%d-%H%M%S' else original(fmt,*args)
        with patch('ro.organize.time.strftime',side_effect=fixed_batch):
            b1,done,_=organize.apply([cid],home=home,log_dir=ld)
            organize.undo(home=home,log_dir=ld)
            b2,done2,_=organize.apply([cid],home=home,log_dir=ld)
            record('S21_same_second_reapply',same_batch=b1==b2,second_move=len(done2),pending_undo=len(organize.pending(home)),source_exists=f.exists())
        # skip behavior is shown as successful resolution
        rt.queue_organize_apply(category=plan['items'][0]['category'])
        r=rt.resolve_write_approval(True)
        record('S22_all_skipped_result',ok=r.get('ok'),output=r.get('output'),resolved=rt.resolved_until>time.time())
result_dir=Path(os.environ.get('RO_AUDIT_OUTPUT_DIR', '/tmp/ro-product-audit-results')); result_dir.mkdir(parents=True,exist_ok=True)
(result_dir/'recovery-results.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
for r in out: print(json.dumps(r,ensure_ascii=False))
