import ast,json,pathlib,subprocess,sys,tempfile
from unittest.mock import patch
from ro import mapper,pet
results=[]
w=next(pathlib.Path('/workspace/.cloud-setup/ro.bot/dist').glob('*.whl'))
code='import sys;sys.path[:]=[p for p in sys.path if p != "/workspace/ro.bot"];sys.path.insert(0,sys.argv[1]);from ro.ambient import serial_lines;next(serial_lines("/dev/fixture",115200))'
r=subprocess.run([sys.executable,'-I','-c',code,str(w)],cwd='/tmp',text=True,capture_output=True)
results.append({'case':'S23_wheel_serial','exit_code':r.returncode,'error':r.stderr.splitlines()[-1]})
with tempfile.TemporaryDirectory() as t:
    mp=pathlib.Path(t)/'map.json';mapper.save_map({'projects':[],'tools':{}},mp)
    rt=pet.PetRuntime(home=t,log_dir=t,map_path=mp)
    first=rt.queue_write_approval('FIRST_FIXTURE',engine='codex')
    second=rt.queue_write_approval('SECOND_FIXTURE',engine='codex')
    with patch('ro.pet.route.run',return_value={'ok':True,'output':'fixture','code':0,'duration':0}) as run:
        rt.resolve_write_approval(True)
        results.append({'case':'S24_write_gate_identity','different_ids':first['id']!=second['id'],'executed_second':run.call_args.args[1]=='SECOND_FIXTURE','approval_takes_id':False})
    rt2=pet.PetRuntime(home=t,log_dir=t,map_path=str(pathlib.Path(t)/'missing.json'))
    results.append({'case':'S25_first_run_question','reason':rt2.preview_question('hello',engine='codex').get('reason')})
p=pathlib.Path('/workspace/ro.bot/ro/cli.py')
cmds=[n.args[0].value for n in ast.walk(ast.parse(p.read_text())) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='add_parser' and isinstance(n.func.value,ast.Name) and n.func.value.id=='sub']
results.append({'inventory':'top_level_cli','count':len(cmds),'names':cmds})
pathlib.Path('/workspace/product-audit/distribution-results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
for x in results:print(json.dumps(x,ensure_ascii=False))
