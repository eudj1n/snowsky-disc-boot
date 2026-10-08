"""Independently reconstruct a saved write USB trace (the candidate's or the way back to stock,
the restore); never opens USB."""
import argparse
import hashlib
import json
from pathlib import Path
import stat
import struct
import zlib
from usb_trace_audit import compare, ready

if not __debug__:
    raise RuntimeError('USB trace audits require Python assertions')

parser=argparse.ArgumentParser(description=__doc__)
for name in ('run', 'package', 'artifacts', 'build', 'staging-build', 'diskos', 'output'):
    parser.add_argument('--'+name, type=Path, required=True)
parser.add_argument('--target', choices=('candidate', 'restore'), default='candidate', help='Which of the package\'s write plans the trace carried out')
args=parser.parse_args()
root=Path(__file__).resolve().parents[2]
run=args.run
sha=lambda b:hashlib.sha256(b).hexdigest()
fp=lambda x:sha(json.dumps(x,sort_keys=True,separators=(',',':')).encode())
request=json.loads((run/'request.json').read_text())
result=json.loads((run/'result.json').read_text())
plan=request['plan']; version=plan['version']
package_plan=json.loads((args.package/f'{args.target}-write-plan.json').read_text())
assert plan==result['plan']==package_plan['plan']
assert fp(plan)==package_plan['plan_sha256']==request['approved_plan_sha256']==result['approved_plan_sha256']
assert plan['operation']=='writer-write' and plan['writer_executions']==1 and plan['nand_writes']
assert plan['physical_write_admitted'] and result['evidence_currency_confirmed']
assert request['session_id']==result['session_id'] and request['nonce_hex']==result['nonce_hex']
assert result['status']=='writer-completion-observed' and not result['cleanup_errors']
assert result['spl_execution_attempted'] is (not result['spl_skipped'])
for name in ('page_execution_attempted','staging_execution_attempted','ram_roundtrip_passed',
             'full_staging_patterns_verified','image_ram_verified','image_hash_sample_verified','writer_ram_verified',
             'completion_poison_verified','writer_execution_attempted','writer_return_observed'):
    assert result[name] is True
for name in ('postwrite_verified','boot_verified','flash_ready'):
    assert result[name] is False
for name,digest in plan['transport_sources_sha256'].items(): assert sha((root/name).read_bytes())==digest
reader=json.loads((root/f'firmware/readers/v{version}.json').read_text())
transport=json.loads((root/f'firmware/transports/v{version}.json').read_text())
layout=json.loads((args.package/'proposed-installer-profile.json').read_text())
assert fp(reader)==plan['reader_profile_sha256'] and fp(transport)==plan['transport_profile_sha256']
assert fp(layout)==plan['installer_profile_sha256']
dependency=json.loads((run/'dependency.json').read_text())
assert sha(Path(dependency['path']).read_bytes())==dependency['sha256']
spl=(args.diskos/'flash/disc_spl_lpddr3.bin').read_bytes()
code=(args.diskos/'flash/my_write5_dram.bin').read_bytes()
payload=(args.build/'identity.bin').read_bytes()
staging_raw=(args.staging_build/'build.json').read_bytes()
staging=(args.staging_build/'identity.bin').read_bytes()
assert json.loads(staging_raw)['purpose']=='staging-check' and json.loads(staging_raw)['nand_opcodes']==[]
assert sha(staging_raw)==plan['staging_build_sha256'] and sha(staging)==plan['staging_payload_sha256']
manifest_raw=(args.build/'build.json').read_bytes()
manifest=json.loads(manifest_raw); policy=manifest['page_policy']
assert sha(manifest_raw)==plan['build_sha256'] and fp(policy)==plan['page_policy_sha256']
image=(args.artifacts/plan['image_name']).read_bytes()
for data,key in ((spl,'spl_sha256'),(code,'writer_sha256'),(payload,'metadata_payload_sha256'),(image,'image_sha256')):
    assert sha(data)==plan[key]
nonce=bytes.fromhex(request['nonce_hex'])
metadata=(run/'metadata-main.bin').read_bytes()
oob=(run/'metadata-oob.bin').read_bytes()
assert sha(metadata)==plan['metadata_sha256'] and len(metadata)==2048 and len(oob)==128
q=struct.pack('<3I16s5I',0x3151524e,1,2,nonce,1,policy['page'],0,0,0)
assert q==(run/'metadata-request.bin').read_bytes()
metadata_results=[]
for saved in run.glob('read-*.bin'):
    assert stat.S_ISREG(saved.lstat().st_mode)
    if saved.stat().st_size==4428:
        candidate=saved.read_bytes()
        if candidate[:16]==struct.pack('<4I',0x3153524e,1,0x454e4f44,0) and candidate[16:32]==nonce:
            metadata_results.append(candidate)
assert len(metadata_results)==1
raw=metadata_results[0]; w=struct.unpack_from('<19I',raw)
assert w[:4]==(0x3153524e,1,0x454e4f44,0)
assert raw[16:32]==nonce and w[8:12]==(1,2,policy['page'],2176)
assert w[12]==zlib.crc32(metadata+oob) and raw[76:2252]==metadata+oob and not any(raw[2252:])
assert w[13:17]==(0x120b,0x38,0x10,0) and 2<=w[17]<=2*reader['nand_polls'] and w[18]==w[17]+8
poison=bytearray(hashlib.shake_256(nonce+b'writer-debug').digest(1024))
for index in (0,9,16): struct.pack_into('<I',poison,index*4,0xeeeeeeee)
poison=bytes(poison); assert poison==(run/'writer-debug-poison.bin').read_bytes()
debug=(run/'writer-result.bin').read_bytes()
assert len(debug)==1024 and debug!=poison
w=struct.unpack('<256I',debug); wp=policy['writer']; blocks=wp['logical_blocks']; start=wp['start_block']; reserve=wp['bad_block_reserve']
assert (w[0],w[9],w[16])==(0x4004e005,0x55555555,0x600df10c)
assert (w[1],w[5],w[6])==(blocks,start,blocks)
assert w[3]==w[4]==w[7]==w[8]==0 and w[15]&0x10 and w[21]&0x10
assert w[12]==0x73717368 and w[20]<=reserve
bad=list(w[40:40+w[20]])
assert bad==sorted(set(bad)) and all(start<=b<start+blocks+reserve for b in bad)
assert start+blocks<=w[11]<=start+blocks+reserve
skipped=sum(b<w[11] for b in bad)
assert w[10]==skipped and w[11]-start==blocks+skipped and w[11]-1 not in bad
assert w[17]<=blocks and ((w[17]==0 and w[18]==0) or (w[17]>0 and 2<=w[18]<=wp['max_tries']))
assert result['writer_result']==dict(status='consistent-writer-record',freshnessVerified=False,bootVerified=False,logicalBlocks=w[1],physicalEndExclusive=w[11],badBlocks=bad,skipped=skipped,retried=w[17],worstTries=w[18])
regions=[(r['name'],r['address'],r['bytes']) for r in plan['ram_regions']]
def pattern(address,size,turn):
    data=hashlib.shake_256(nonce+struct.pack('<I',address)).digest(size)
    return bytes(x^255 for x in data) if turn else data

def chunks(regions):
    for name,address,size in regions:
        for offset in range(0,size,65536): yield name,address+offset,min(65536,size-offset),offset

def control(request,parameter=0):
    yield ('control',request,parameter,b'X2000' if request==0 else None)
def transfer(address,data,incoming):
    yield from control(1,address); yield from control(2,len(data))
    yield ('bulk',129 if incoming else 1,len(data),data)
def staging_request(op,address,length,seed):
    return struct.pack('<6I16s2I',0x51475453,1,op,address,length,seed,nonce,0,0)
def staging_result(op,address,length,digest=bytes(32)):
    return struct.pack('<4I16s4I32s',0x52475453,1,op,0,nonce,address,length,2*(length//4) if op==1 else length,0,digest)
small=[r for r in regions if r[0]!='image']
_,image_address,image_bytes=next(r for r in regions if r[0]=='image')
seed=struct.unpack('<I',hashlib.shake_256(nonce+b'staging-pattern').digest(4))[0] or 1
def expected():
    # DDR's diagnostic first: this entry's clean one means no SPL again (ram_transport.bring_up, plan, stage 4c).
    clean=struct.pack('<5I',0xd1a6c0de,9,0,0,0)
    yield from control(0)
    yield from transfer(transport['diagnostic_address'],bytes.fromhex(result['entry_diagnostic']),True)
    assert result['spl_skipped']==(bytes.fromhex(result['entry_diagnostic'])==clean)
    if not result['spl_skipped']:
        yield from transfer(transport['spl_load_address'],spl,False)
        yield from transfer(transport['spl_load_address'],spl,True)
        yield from control(4,transport['spl_entry']); yield from control(0)
        yield from transfer(transport['diagnostic_address'],clean,True)
    for turn in range(2):
        for incoming in (False,True):
            for _,address,size in regions[:4]: yield from transfer(address,pattern(address,size,turn),incoming)
    for address,data in ((reader['load_address'],payload),(reader['request_address'],q),(reader['result_address'],bytes(4428))):
        yield from transfer(address,data,False); yield from transfer(address,data,True)
    yield from control(4,reader['load_address']); yield from control(0)
    yield from transfer(reader['result_address'],raw,True)
    # The small regions: the host's two pattern passes (plan, stage 4c).
    for turn in range(2):
        for incoming in (False,True):
            for _,address,size,_ in chunks(small): yield from transfer(address,pattern(address,size,turn),incoming)
    # The staging check in the code region, run for the image region's pattern; the image written and
    # read back; the check run again for the hash of the image's first staging_sample_bytes.
    for incoming in (False,True):
        for _,address,size,offset in chunks([('code',reader['load_address'],len(staging))]):
            yield from transfer(address,staging[offset:offset+size],incoming)
    sample=plan['staging_sample_bytes']
    for op,op_seed,length,limit in ((1,seed,image_bytes,'staging_check_ms'),(2,0,sample,'staging_sample_ms')):
        for address,data in ((reader['request_address'],staging_request(op,image_address,length,op_seed)),
                             (reader['result_address'],bytes(80))):
            yield from transfer(address,data,False); yield from transfer(address,data,True)
        yield from control(4,reader['load_address'])
        if plan['completion_ask']: yield ready(plan[limit],b'X2000')
        else: yield from control(0)
        digest=hashlib.sha256(image[:length]).digest() if op==2 else bytes(32)
        yield from transfer(reader['result_address'],staging_result(op,image_address,length,digest),True)
        if op==1:
            for incoming in (False,True):
                for _,address,size,offset in chunks([('image',image_address,image_bytes)]):
                    yield from transfer(address,image[offset:offset+size],incoming)
    for incoming in (False,True):
        for name,address,size,offset in chunks(small):
            if name=='code': data=code.ljust(reader['code_bytes'],b'\0')[offset:offset+size]
            elif name=='writer-debug': data=poison[offset:offset+size]
            else: data=pattern(address,size,1)
            yield from transfer(address,data,incoming)
    # The writer's one held ask, one more after an ask that failed at once (plan, stage 4c).
    yield from control(4,plan['writer_entry'])
    if plan['completion_ask']: yield ready(plan['writer_wait_ms'],b'X2000',tolerant=True)
    else: yield from control(0)
    yield from transfer(layout['debug_address'],debug,True)

rows=[json.loads(line) for line in (run/'transfers.jsonl').read_text().splitlines()]
trace=compare(rows,expected(),run,transport,plan['protocol_call_limit'],
              ([] if result['spl_skipped'] else [transport['spl_entry']])+[reader['load_address']]*3+[plan['writer_entry']],
              result['connection'])
report=dict(status=f'saved-{args.target}-write-trace-matches',session_id=result['session_id'],plan_sha256=fp(plan),
            calls=trace['calls'],raw_reads=trace['raw_reads'],read_bytes=trace['read_bytes'],
            bulk_writes=trace['bulk_writes'],executions=trace['executions'],
            result_sha256=sha((run/'result.json').read_bytes()),journal_sha256=sha((run/'transfers.jsonl').read_bytes()),
            image_sha256=sha(image),metadata_sha256=sha(metadata),audit_script_sha256=sha(Path(__file__).read_bytes()),
            physical_device_accessed=False,writer_executions=1,nand_writes=True,boot_verified=False,flash_ready=False)
with args.output.open('x') as f: json.dump(report,f,indent=2); f.write('\n')
print(json.dumps(report,indent=2))
