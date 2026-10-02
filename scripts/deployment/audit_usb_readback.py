"""Independently reconstruct a saved postwrite USB trace; never opens USB."""
import argparse,hashlib,json,struct,zlib
from pathlib import Path
from usb_trace_audit import compare

if not __debug__:
    raise RuntimeError('USB trace audits require Python assertions')
parser=argparse.ArgumentParser(description=__doc__)
for name in ('run', 'package', 'build', 'diskos', 'artifacts', 'output'):
    parser.add_argument('--'+name, type=Path, required=True)
args=parser.parse_args()
root=Path(__file__).resolve().parents[2];run=args.run
sha=lambda b:hashlib.sha256(b).hexdigest()
fp=lambda x:sha(json.dumps(x,sort_keys=True,separators=(',',':')).encode())
r=json.loads((run/'result.json').read_text());q=json.loads((run/'request.json').read_text());p=r['plan']
package_plan=json.loads((args.package/'postwrite-collection-plan.json').read_text())
assert p==q['plan']==package_plan['plan']
assert fp(p)==package_plan['plan_sha256']==r['approved_plan_sha256']==q['approved_plan_sha256']
assert r['status']=='rootfs-collected' and not r['cleanup_errors']
assert r['session_id']==q['session_id'] and r['nonce_hex']==q['nonce_hex']
assert r['physical_device_accessed'] and r['ram_roundtrip_passed'] and not r['flash_ready']
for n,h in p['transport_sources_sha256'].items():assert sha((root/n).read_bytes())==h
v=p['version'];reader=json.loads((root/f'firmware/readers/v{v}.json').read_text());t=json.loads((root/f'firmware/transports/v{v}.json').read_text())
collector=json.loads((root/f'firmware/collectors/v{v}.json').read_text())
scope=dict(profile=collector,mode=p['operation'],first_page=p['first_page'],
           end_page=p['end_page_exclusive'],logical_blocks=p['logical_blocks'],
           session_budget_ms=collector['full_budget_ms'])
assert p['operation']=='rootfs' and fp(scope)==p['collector_policy_sha256']
assert fp(reader)==p['reader_profile_sha256'] and fp(t)==p['transport_profile_sha256']
build=args.build;assert sha((build/'build.json').read_bytes())==p['build_sha256']
payload=(build/'identity.bin').read_bytes();assert sha(payload)==p['payload_sha256']
spl=(args.diskos/'flash/disc_spl_lpddr3.bin').read_bytes();assert sha(spl)==p['spl_sha256']
nonce=bytes.fromhex(r['nonce_hex']);raw=(run/'records.bin').read_bytes()
assert len(raw)==p['capture_bytes'] and sha(raw)==r['capture_sha256']
ppb=p['pages_per_block'];first=p['first_page']//ppb;end=p['end_page_exclusive']//ppb
marker_pages=[n for n in range(p['first_page'],p['end_page_exclusive'],ppb) for _ in range(2)]
main=[];framed=[];hist=[0]*16;observed_pages=[]
for i in range(len(raw)//4476):
 request=raw[i*4476:i*4476+48];b=raw[i*4476+48:(i+1)*4476];w=struct.unpack_from('<19I',b);page=w[10]
 assert request==struct.pack('<3I16s5I',0x3151524e,1,2,nonce,i+1,page,0,0,0)
 assert w[:4]==(0x3153524e,1,0x454e4f44,0) and b[16:32]==nonce and w[8:10]==(i+1,2) and w[11]==2176
 assert w[12]==zlib.crc32(b[76:2252]) and not any(b[2252:])
 assert w[13]==0x120b and w[15]&0x50==0x10 and not w[16]&1
 ecc=(w[16]&0x70)>>4;assert ecc in (0,1) and 2<=w[17]<=2*reader['nand_polls'] and w[18]==w[17]+8
 hist[ecc]+=1;main.append(b[76:2252]);framed.append(b);observed_pages.append(page)
good=[];bad=[]
for i,block in enumerate(range(first,end)):
 a,b=main[2*i:2*i+2];assert a[:2048]==b[:2048] and a[2048]==b[2048]
 (good if a[2048]==255 else bad).append(block)
mapping=good[:p['logical_blocks']];assert len(mapping)==p['logical_blocks'] and mapping==r['logical_to_physical'] and bad==r['bad_blocks']
pages=marker_pages+[n for block in mapping for n in range(block*ppb,(block+1)*ppb)]
assert pages==observed_pages and hist==r['ecc_histogram'] and len(pages)==r['records_completed']
image=b''.join(b[:2048] for b in main[len(marker_pages):]);assert image==(run/'logical-image.bin').read_bytes()
assert sha(image)==r['logical_image_sha256']
review=json.loads((args.package/'installation-review.json').read_text())
candidate=review['images']['candidate']
assert candidate['bytes']==len(image) and sha(image)==candidate['sha256']
assert (args.artifacts/candidate['name']).read_bytes()==image
for i,n in enumerate(pages[len(marker_pages):],len(marker_pages)):
 if n%ppb==0:assert main[i][2048]==255

def ctrl(n,a=0):yield ('control',n,a,b'X2000' if n==0 else None)
def transfer(a,b,incoming):
 yield from ctrl(1,a);yield from ctrl(2,len(b));yield ('bulk',129 if incoming else 1,len(b),b)
def chunks(a,b):
 for off in range(0,len(b),65536):yield a+off,b[off:off+65536]
def compare_ram(a,b):
 for incoming in (False,True):
  for where,part in chunks(a,b):yield from transfer(where,part,incoming)
def expected():
 yield from ctrl(0);yield from compare_ram(t['spl_load_address'],spl);yield from ctrl(4,t['spl_entry']);yield from ctrl(0)
 yield from transfer(t['diagnostic_address'],struct.pack('<5I',0xd1a6c0de,9,0,0,0),True)
 for turn in range(2):
  for incoming in (False,True):
   for region in p['ram_regions']:
    a=region['address'];b=hashlib.shake_256(nonce+struct.pack('<I',a)).digest(region['bytes'])
    if turn:b=bytes(v^255 for v in b)
    for where,part in chunks(a,b):yield from transfer(where,part,incoming)
 yield from compare_ram(reader['load_address'],payload)
 for batch,off in enumerate(range(0,len(pages),p['batch_pages']),1):
  count=min(p['batch_pages'],len(pages)-off)
  request=(struct.pack('<4I',0x3151424e,1,count,0)+b''.join(raw[k*4476:k*4476+48] for k in range(off,off+count))).ljust(3088,b'\0')
  assert request==(run/f'batch-{batch:04}-request.bin').read_bytes()
  result=(struct.pack('<4I',0x3152424e,1,count,0)+b''.join(framed[off:off+count])).ljust(283408,b'\0')
  yield from compare_ram(collector['request_address'],request);yield from compare_ram(collector['result_address'],bytes(283408));yield from ctrl(4,reader['load_address']);yield from ctrl(0)
  for where,part in chunks(collector['result_address'],result):yield from transfer(where,part,True)
 assert batch==r['batch_executions']==p['batch_limit']
rows=[json.loads(l) for l in (run/'transfers.jsonl').read_text().splitlines()]
trace=compare(rows,expected(),run,t,p['protocol_call_limit'],
              [t['spl_entry']]+[reader['load_address']]*r['batch_executions'],r['connection'])
report=dict(status='saved-postwrite-trace-matches',session_id=r['session_id'],calls=trace['calls'],raw_reads=trace['raw_reads'],raw_bytes=trace['read_bytes'],bulk_ram_writes=trace['bulk_writes'],spl_executions=1,batch_executions=r['batch_executions'],records=len(pages),bad_blocks=bad,ecc_histogram=hist,plan_sha256=fp(p),result_sha256=sha((run/'result.json').read_bytes()),journal_sha256=sha((run/'transfers.jsonl').read_bytes()),capture_sha256=sha(raw),image_sha256=sha(image),audit_script_sha256=sha(Path(__file__).read_bytes()),new_device_access=False,nand_writes=False,active_boot_verified=False)
with args.output.open('x') as f:json.dump(report,f,indent=2);f.write('\n')
print(json.dumps(report,indent=2))
