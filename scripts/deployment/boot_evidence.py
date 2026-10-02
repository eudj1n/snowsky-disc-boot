#!/usr/bin/env python3
"""Offline build/plan or separately authorized bounded boot/OTA reads. Never writes NAND."""
import argparse
import ctypes as C
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from firmware_profile import PROFILES, fingerprint
from deployment import ram_transport as ram
from deployment import collect_rootfs as collector
from deployment import build_identity as builder
from deployment.collector_policy import load_collector_policy, REQUEST_BYTES, RESULT_BYTES, MAX_PAGES
from deployment.nand_records import encode_request, decode_result

check = ram.check
ARTIFACTS = {'identity.elf':1048576,'identity.bin':32768,'identity_layout.h':8192,'identity.ld':8192}


def load_policy(base,reader,directory=PROFILES):
    page=ram.load_metadata_policy(base,reader,directory)
    buffers=load_collector_policy(base,reader,'rootfs-probe',directory)['profile']
    p=json.loads(ram.read_file(directory/'boot'/f'v{base["version"]}.json',16384))
    check(p.get('schema_version')==1 and p.get('version')==base['version'] and
          p.get('page_policy_sha256')==fingerprint(page), 'Boot profile/policy mismatch')
    check(p.get('physical_qualified') is False and p.get('writes_admitted') is False,'Boot evidence cannot admit writes')
    check(type(p.get('session_budget_ms')) is int and 10000<=p['session_budget_ms']<=600000,'Invalid boot budget')
    check(type(p.get('selector_bytes')) is int and 16<=p['selector_bytes']<=page['main_bytes'],'Invalid selector length')
    check(p.get('selectors')=={'ota:kernel':'rootfs','ota:kernel2':'rootfs2'},'Unsupported selector format')
    parts=p.get('partitions')
    check(isinstance(parts,list) and len(parts)==2 and [x.get('name') for x in parts]==['uboot','ota'],'Invalid evidence targets')
    block=page['main_bytes']*page['chip']['pages_per_block']; end=0
    for part in parts:
        a,n=part.get('offset'),part.get('size')
        check(type(a) is int and type(n) is int and a>=end and a%block==0 and n%block==0
              and 0<n<=4*1024*1024 and a+n<=page['main_bytes']*page['total_pages'],'Invalid evidence range')
        end=a+n
    check(parts[0]['offset']<=page['page']*page['main_bytes']<parts[0]['offset']+parts[0]['size'],
          'Metadata page outside boot partition')
    check(set(p.get('source_pins',{}))=={'etc/ota_bin/'+n for n in
          ('ota_local_method.sh','ota_utils.sh','recovery_to_main_os.sh')} and
          all(isinstance(v,str) and len(v)==64 and all(c in '0123456789abcdef' for c in v) for v in p['source_pins'].values()),
          'Invalid OTA source pins')
    return dict(profile=p,page_policy=page,buffers=buffers)


def scopes(policy):
    main=policy['page_policy']['main_bytes']
    return [dict(name=p['name'],first_page=p['offset']//main,end_page=(p['offset']+p['size'])//main,
                 profile=policy['buffers']) for p in policy['profile']['partitions']]


def source_pins():
    return {n:ram.probe.digest(ram.ROOT/n) for n in builder.source_paths()+['scripts/deployment/boot_evidence.py']}


def build(base,reader,policy,diskos,output):
    for n,h in reader['source_pins'].items(): check(ram.probe.digest(diskos/n)==h,'Changed external build input')
    output=output.resolve()
    check(any(output.is_relative_to(ram.ROOT/n) for n in ('build','work')),'Build must be ignored')
    output.mkdir(parents=True,exist_ok=False)
    image=os.environ.get('DISC_TOOLCHAIN_IMAGE','diskos-ui-builder')
    image_id=subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',image],text=True).strip()
    manifest=dict(schema_version=1,purpose='boot-evidence',firmware_sha256=fingerprint(base),
                  reader_sha256=fingerprint(reader),policy_sha256=fingerprint(policy),sources=source_pins(),
                  scopes=scopes(policy),artifacts={},toolchain_image=image_id,nand_writes=False)
    for scope in scopes(policy):
        dest=output/scope['name'];dest.mkdir()
        builder.prepare(reader,dest,policy['page_policy'],scope)
        subprocess.run(['docker','run','--rm','--platform','linux/amd64','--network','none',
                        '-v',f'{ram.ROOT}:/src:ro','-v',f'{dest}:/out','-w','/out',image_id,
                        'sh','/src/scripts/deployment/link_identity.sh'],check=True)
        check(builder.inspect_elf((dest/'identity.elf').read_bytes(),reader)==(dest/'identity.bin').read_bytes(),'ELF/raw mismatch')
        manifest['artifacts'][scope['name']]={n:ram.probe.digest(dest/n) for n in ARTIFACTS}
    ram.save_json(output/'build.json',manifest)
    return manifest


def prepare(base,cpu,reader,transport,policy,diskos,build_dir,metadata):
    raw=ram.read_file(build_dir/'build.json',65536); m=json.loads(raw)
    for key,value in dict(schema_version=1,purpose='boot-evidence',firmware_sha256=fingerprint(base),
                          reader_sha256=fingerprint(reader),policy_sha256=fingerprint(policy),sources=source_pins(),
                          scopes=scopes(policy),nand_writes=False).items():
        check(m.get(key)==value,f'Boot build mismatch: {key}')
    check(set(m.get('artifacts',{}))=={'uboot','ota'},'Missing/extra scope artifacts')
    payloads={}
    for scope in scopes(policy):
        name=scope['name']; check(set(m['artifacts'][name])==set(ARTIFACTS),'Invalid build artifact set')
        data={n:ram.read_file(build_dir/name/n,limit) for n,limit in ARTIFACTS.items()}
        check(all(ram.sha(v)==m['artifacts'][name][n] for n,v in data.items()),'Changed build artifact')
        check(builder.inspect_elf(data['identity.elf'],reader)==data['identity.bin'],'Wrong boot payload ELF')
        payloads[name]=data['identity.bin']
    for name,digest in reader['source_pins'].items(): check(ram.probe.digest(diskos/name)==digest,'Changed diskOS source')
    check(ram.probe.digest(diskos/'src/usbboot/usbboot.c')==cpu['reference_source_sha256'],'Changed USB source')
    spl=ram.read_file(diskos/'flash/disc_spl_lpddr3.bin',transport['spl_bytes'])
    check(len(spl)==transport['spl_bytes'] and ram.sha(spl)==reader['source_pins']['flash/disc_spl_lpddr3.bin']
          and spl[0x7c0:0x7d4]==bytes(20),'Unreviewed SPL')
    return dict(spl=spl,payload=payloads['uboot'],payloads=payloads,metadata=metadata,build_sha256=ram.sha(raw))


def make_plan(base,cpu,reader,transport,policy,inputs):
    page=policy['page_policy']; ppb=page['chip']['pages_per_block']
    table=ram.review_partitions(inputs['metadata'],page['kernel'],page['chip'],page['writer'])
    for expected in policy['profile']['partitions']:
        found=next((p for p in table['partitions'] if p['name']==expected['name']),None)
        check(found is not None and all(found[k]==expected[k] for k in expected)
              and found['mask_flags']==found['manager_mode']==0,'Evidence partition differs from reviewed profile')
    spans=scopes(policy); boot,ota=spans
    counts=[(s['end_page']-s['first_page'])//ppb for s in spans]
    batches=math.ceil(counts[0]*2/MAX_PAGES)+1+math.ceil(counts[0]*ppb/MAX_PAGES)+math.ceil(counts[1]*2/MAX_PAGES)
    p=ram.plan(base,cpu,reader,transport,{k:inputs[k] for k in ('spl','payload','build_sha256')},'ram-check')
    p.update(operation='boot-evidence',policy_sha256=fingerprint(policy),scopes=spans,
             metadata_sha256=table['page_sha256'],payload_entry=reader['load_address'],
             payload_sha256={n:ram.sha(v) for n,v in inputs['payloads'].items()},
             payload_bytes={n:len(v) for n,v in inputs['payloads'].items()},
             batch_limit=batches,record_limit=sum(counts)*2+1+counts[0]*ppb,
             boot_main_bytes_limit=counts[0]*ppb*page['main_bytes'],
             protocol_call_limit=160+batches*53,session_budget_ms=policy['profile']['session_budget_ms'],
             nand_commands=['0x9f','0x0f','0x13','0x0b'],writer_executions=0,
             steps=['one SPL; bounded RAM checks; compared partition-specific read payloads',
                    'paired boot markers; fresh metadata; all pages of good boot blocks',
                    'paired first-page OTA reads only; classify selector without boot inference'])
    p['ram_regions'] += [dict(name='batch-request',address=policy['buffers']['request_address'],bytes=REQUEST_BYTES),
                         dict(name='batch-result',address=policy['buffers']['result_address'],bytes=RESULT_BYTES)]
    p['timeout_ms']['session_budget_ms']=p['session_budget_ms']
    p['metadata']=dict(page=page['page'],bytes=page['main_bytes']+page['oob_bytes'],
                       policy_sha256=fingerprint(page))
    p['transport_sources_sha256'].update(source_pins())
    p['transport_sources_sha256']['scripts/deployment/collect_rootfs.py']=ram.probe.digest(ram.ROOT/'scripts/deployment/collect_rootfs.py')
    check(set(inputs['payloads'])=={'uboot','ota'} and inputs['payload']==inputs['payloads']['uboot'] and
          all(isinstance(v,bytes) and 0<len(v)<=reader['code_bytes'] for v in inputs['payloads'].values()),'Invalid boot payloads')
    return p


def selector(data,profile):
    check(len(data)>=profile['selector_bytes'],'Short selector')
    raw=data[:profile['selector_bytes']]
    for token,target in profile['selectors'].items():
        if raw==token.encode().ljust(len(raw),b' '):
            return dict(status='recognized-ota-selector',target=target,token=token,active_boot_verified=False)
    return dict(status='unknown-ota-selector',target=None,selector_sha256=ram.sha(raw),active_boot_verified=False)


def save_bytes(path,data):
    with path.open('xb') as stream:
        stream.write(data); stream.flush(); os.fsync(stream.fileno())


def capture(session,reader,policy,inputs,nonce,record):
    page=policy['page_policy']; buffers=policy['buffers']; ppb=page['chip']['pages_per_block']; main=page['main_bytes']
    sequence=1; histogram=[0]*16; folder=session.journal.output
    with (folder/'records.bin').open('xb') as stream:
        def batch(scope,pages,markers=False):
            nonlocal sequence
            check(0<len(pages)<=MAX_PAGES and all(type(n) is int and scope['first_page']<=n<scope['end_page']
                  and (not markers or n%ppb==0) for n in pages),'Page outside boot evidence scope')
            check(scope['name']!='ota' or markers,'OTA admits marker/selector pages only')
            requests=[encode_request(2,nonce,sequence+i,n) for i,n in enumerate(pages)]
            q=(struct.pack('<4I',0x3151424e,1,len(pages),0)+b''.join(requests)).ljust(REQUEST_BYTES,b'\0')
            with (folder/f'batch-{record["batch_executions"]+1:04}-request.bin').open('xb') as f:
                f.write(q); f.flush(); os.fsync(f.fileno())
            collector.write_compare(session,buffers['request_address'],q)
            collector.write_compare(session,buffers['result_address'],bytes(RESULT_BYTES))
            session.execute_batch()
            raw=b''.join(session.read(buffers['result_address']+off,min(65536,RESULT_BYTES-off)) for off in range(0,RESULT_BYTES,65536))
            check(struct.unpack_from('<4I',raw)==(0x3152424e,1,len(pages),0) and not any(raw[16+len(pages)*4428:]),'Incomplete boot batch')
            values=[]
            for i,req in enumerate(requests):
                d=decode_result(raw[16+i*4428:16+(i+1)*4428],req,page['main_bytes']+page['oob_bytes'])
                check(0<d['observed_id']<0xffffff and d['observed_id']&page['id_mask']==page['expected_id']
                      and all(0<=d[k]<=255 for k in ('feature','status','protect')) and not d['status']&1
                      and d['feature']&page['feature_mask']==page['feature_value']
                      and 2<=d['polls']<=2*reader['nand_polls'] and d['transfers']==d['polls']+8,'Untrusted boot page')
                ecc=(d['status']&page['ecc_mask'])>>page['ecc_shift']; check(page['ecc_admitted']&(1<<ecc),'Untrusted boot ECC')
                histogram[ecc]+=1; values.append(d['data'])
            for i,req in enumerate(requests): stream.write(req+raw[16+i*4428:16+(i+1)*4428])
            stream.flush(); os.fsync(stream.fileno()); sequence+=len(pages)
            record['records_completed']=sequence-1
            return values
        for scope in scopes(policy):
            name=scope['name']
            if name!='uboot': collector.write_compare(session,reader['load_address'],inputs['payloads'][name])
            numbers=[n for n in range(scope['first_page'],scope['end_page'],ppb) for _ in range(2)]
            good=[]; bad=[]; first_pages={}
            for off in range(0,len(numbers),MAX_PAGES):
                ns=numbers[off:off+MAX_PAGES]; data=batch(scope,ns,True)
                for i in range(0,len(ns),2):
                    a,b=data[i],data[i+1]
                    check(a[main]==b[main],'Ambiguous boot marker')
                    if a[main]==255:
                        check(a[:main]==b[:main],'Unstable boot first-page main data')
                        good.append(ns[i]//ppb);first_pages[ns[i]//ppb]=a[:main]
                    else: bad.append(ns[i]//ppb)
            check(good,'No good evidence blocks')
            record[name+'_good_blocks']=good;record[name+'_bad_blocks']=bad
            if name=='uboot':
                check(page['page']//ppb in good,'Metadata block is bad')
                observed=batch(scope,[page['page']])[0][:main]
                save_bytes(folder/'metadata-main.bin',observed)
                check(observed==inputs['metadata'],'Fresh partition metadata changed')
                with (folder/'uboot-good-blocks.bin').open('xb') as image:
                    for block in good:
                        for start in range(block*ppb,(block+1)*ppb,MAX_PAGES):
                            ns=list(range(start,min(start+MAX_PAGES,(block+1)*ppb))); values=batch(scope,ns)
                            for number,value in zip(ns,values):
                                if number%ppb==0: check(value[main]==255 and value[:main]==first_pages[block],'Boot marker/main changed during data read')
                                if number==page['page']: check(value[:main]==observed,'Metadata changed during boot read')
                                image.write(value[:main])
                    image.flush();os.fsync(image.fileno())
            else:
                # Preserve every observed selector. The stock bootloader's exact
                # bad-block selection remains to be established from its capture.
                for block,data in first_pages.items(): save_bytes(folder/f'ota-block-{block}-main.bin',data)
                record['ota_selectors']={str(b):selector(data,policy['profile']) for b,data in first_pages.items()}
    check(record['records_completed']<=record['plan']['record_limit'],'Boot capture exceeds plan')
    record.update(status='boot-evidence-collected',ecc_histogram=histogram,
                  capture_sha256=ram.probe.digest(folder/'records.bin'),
                  boot_image_sha256=ram.probe.digest(folder/'uboot-good-blocks.bin'),active_boot_verified=False)


def acquire(plan,approved,base,cpu,reader,transport,policy,inputs,library,output,
            loader=C.CDLL,clock=time.monotonic,sleep=time.sleep):
    check(plan==make_plan(base,cpu,reader,transport,policy,inputs) and approved==fingerprint(plan),'Boot plan/inputs changed')
    output.mkdir(mode=0o700);nonce=uuid.uuid4().bytes
    record=dict(plan=plan,approved_plan_sha256=approved,session_id=str(uuid.uuid4()),nonce_hex=nonce.hex(),
                started_at=datetime.now(timezone.utc).isoformat(),status='incomplete',batch_executions=0,records_completed=0,
                spl_execution_attempted=False,page_execution_attempted=False,physical_device_accessed=False,
                writer_execution_attempted=False,nand_writes=False,active_boot_verified=False,flash_ready=False)
    ram.save_json(output/'request.json',record);journal=session=None
    try:
        ram.read_file(library,16*1024*1024)
        ram.save_json(output/'dependency.json',dict(path=str(library.resolve()),sha256=ram.probe.digest(library)))
        journal=collector.Journal(output,plan['protocol_call_limit']);journal.event(phase='usb-discovery-attempt')
        session=collector.Session(ram.bind(loader(str(library))),cpu,dict(transport,session_budget_ms=plan['session_budget_ms']),record,journal,clock,sleep)
        session.open();collector.bootstrap(session,inputs,nonce,record);capture(session,reader,policy,inputs,nonce,record)
    except (Exception,KeyboardInterrupt) as exc: record.update(status='failed',error=f'{type(exc).__name__}: {exc}')
    finally:
        if session and session.close(): record['status']='failed'
        if journal: journal.file.close()
        record['finished_at']=datetime.now(timezone.utc).isoformat();ram.save_json(output/'result.json',record)
    return record


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('build','plan','acquire'))
    p.add_argument('--version');p.add_argument('--diskos',type=Path,required=True)
    for name in ('build','metadata-page','libusb','output'): p.add_argument('--'+name,type=Path)
    p.add_argument('--approved-plan-sha256');a=p.parse_args()
    required={'build':{'output'},'plan':{'build','metadata_page'},'acquire':{'build','metadata_page','libusb','output','approved_plan_sha256'}}
    if {n for n in ('build','metadata_page','libusb','output','approved_plan_sha256') if getattr(a,n) is not None}!=required[a.action]:
        p.error('Supply exactly the arguments for build, offline plan or separately authorized acquisition')
    try:
        base=ram.load_profile(a.version);reader=ram.load_reader_profile(base);cpu=ram.load_probe_profile(base)
        transport=ram.load_transport(base,reader);policy=load_policy(base,reader)
        if a.action=='build': result=build(base,reader,policy,a.diskos,a.output)
        else:
            inputs=prepare(base,cpu,reader,transport,policy,a.diskos,a.build,ram.read_file(a.metadata_page,policy['page_policy']['main_bytes']))
            plan=make_plan(base,cpu,reader,transport,policy,inputs)
            result=dict(plan=plan,plan_sha256=fingerprint(plan)) if a.action=='plan' else acquire(plan,a.approved_plan_sha256,
                base,cpu,reader,transport,policy,inputs,a.libusb,a.output)
        print(json.dumps(result,indent=2));return 1 if result.get('status')=='failed' else 0
    except (Exception,KeyboardInterrupt) as exc: print(f'{type(exc).__name__}: {exc}',file=sys.stderr);return 2


if __name__=='__main__': raise SystemExit(main())
