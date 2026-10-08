#!/usr/bin/env python3
"""Prepare a pinned, offline installation evidence bundle. No USB or NAND writes.

The bundle is evidence, not authorization, device identity or freshness proof.
Installer admission pins its canonical hash separately, avoiding a hash cycle.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from firmware_profile import fingerprint, sha
from deployment import ram_transport as ram
from deployment import boot_review, boot_evidence, preinstall, readback, collect_rootfs

check=ram.check
ROOT=ram.ROOT


def source_pins():
    paths=list((ROOT/'scripts/deployment').glob('*.py'))+[ROOT/'scripts/firmware_profile.py']
    return {str(p.relative_to(ROOT)):ram.probe.digest(p) for p in sorted(paths)}


def load(path):return json.loads(ram.read_file(path,1024*1024))


def file_pin(path,limit):
    from deployment.review import regular
    size=regular(path,limit);h=hashlib.sha256()
    with path.open('rb') as f:
        while data:=f.read(1024*1024):h.update(data)
    check(path.stat().st_size==size,'Evidence size changed while hashing')
    return dict(bytes=size,sha256=h.hexdigest())


def layout_contract(layout):
    return {k:v for k,v in layout.items() if k not in ('physical_write_admitted','installation_review_sha256')}


def context(base,cpu,reader,transport,layout,inputs):
    return dict(version=base['version'],firmware_sha256=fingerprint(base),cpu_sha256=fingerprint(cpu),
        reader_sha256=fingerprint(reader),transport_sha256=fingerprint(transport),
        additional_profile_pins={name:ram.sha(ram.read_file(ROOT/'firmware'/name/f'v{base["version"]}.json',65536))
                                 for name in ('boot','bootloaders','preinstall','usb','collectors')},
        page_policy_sha256=fingerprint(inputs['page_policy']),layout=layout_contract(layout),
        build_sha256=inputs['build_sha256'],metadata_payload_sha256=ram.sha(inputs['payload']),
        metadata_sha256=ram.sha(inputs['metadata']),spl_sha256=ram.sha(inputs['spl']),
        writer_sha256=ram.sha(inputs['writer']),image_review_sha256=inputs['image_review_sha256'],
        staging_build_sha256=inputs['staging_build_sha256'],staging_payload_sha256=ram.sha(inputs['staging_payload']),
        readback_plan_sha256=fingerprint(inputs['readback_plan']))


def validate_binding(bundle,base,cpu,reader,transport,layout,inputs):
    check(isinstance(bundle,dict) and bundle.get('schema_version')==1
          and bundle.get('status')=='installation-inputs-reviewed'
          and fingerprint(bundle)==layout.get('installation_review_sha256'),'Unpinned installation evidence bundle')
    check(bundle.get('context')==context(base,cpu,reader,transport,layout,inputs),'Installation inputs/profile/build changed')
    check(bundle.get('source_pins')==source_pins(),'Installation review sources changed; regenerate and review')
    state=bundle.get('source_state',{})
    check(state.get('kind') in ('recorded-stock','installed-candidate')
          and state.get('freshness_verified') is False,'Missing historical source-state contract')
    if state['kind']=='installed-candidate':
        check(state.get('new_image_staged') is False and sha(state.get('previous_review_sha256'))
              and state.get('exact_readback',{}).get('status')=='saved-logical-readback-matches'
              and state['exact_readback'].get('image_sha256')==state.get('image',{}).get('sha256')
              and state['exact_readback'].get('freshness_verified') is False,
              'Invalid installed-candidate source state')
    check(bundle.get('physical_device_accessed') is False and bundle.get('flash_ready') is False
          and bundle.get('freshness_verified') is False and sha(bundle.get('libusb_sha256')),
          'Invalid installation evidence scope')
    target=inputs['target'];image=bundle.get('images',{}).get(target,{})
    check(image==dict(name=inputs['image_name'],bytes=len(inputs['image']),sha256=ram.sha(inputs['image'])),
          'Installation target image mismatch')
    check(bundle.get('boot',{}).get('static_selection_reviewed') is True
          and bundle['boot']['selected_rootfs']==inputs['page_policy']['kernel']['metadata']['target'],
          'Installation target differs from reviewed boot selection')
    check(bundle.get('postwrite_collection')==inputs['readback_plan'],'Readback acquisition changed')
    expected=readback.make_plan(base,reader,inputs['page_policy'],inputs['metadata'],image['sha256'])
    check(bundle.get('exact_readback',{}).get(target)==expected,'Missing exact padded-image readback contract')
    return fingerprint(bundle)


def evidence(folder,kind):
    audit_name='offline-journal-review.json' if kind=='stock' else 'offline-review.json'
    result=load(folder/'result.json');request=load(folder/'request.json');audit=load(folder/audit_name)
    expected={'boot':'boot-evidence-collected','stock':'rootfs-collected','stage':'writer-staging-verified'}[kind]
    check(result.get('status')==expected and result.get('cleanup_errors')==[]
          and result.get('physical_device_accessed') is True,'Incomplete physical evidence')
    p=result['plan'];h=fingerprint(p)
    check(request['plan']==p and request['approved_plan_sha256']==result['approved_plan_sha256']==h
          and request['session_id']==result['session_id'] and request['nonce_hex']==result['nonce_hex'],
          'Physical request/result mismatch')
    check(p.get('nand_writes') is False and result.get('writer_execution_attempted',False) is False,
          'Expected read/staging evidence, not a possible write')
    files={n:file_pin(folder/n,32*1024*1024) for n in ('request.json','result.json','transfers.jsonl',audit_name,'dependency.json')}
    check(audit['result_sha256']==files['result.json']['sha256']
          and audit['journal_sha256']==files['transfers.jsonl']['sha256'],'Audit no longer matches saved session')
    if kind!='stock':
        check(audit['session_id']==result['session_id'] and audit['plan_sha256']==h,'Audit session/plan mismatch')
    if kind=='boot':
        check(audit.get('status')=='saved-boot-trace-matches' and audit.get('records')==result['records_completed']
              and 0<audit['calls']<=p['protocol_call_limit'],'Incomplete boot trace review')
    if kind=='stock':
        check(audit.get('records')==result['records_completed'] and audit.get('spl_executions') in (0,1)
              and audit.get('batch_executions')==result['batch_executions']
              and 0<audit['protocol_calls']<=p['protocol_call_limit'],'Incomplete stock trace review')
    if kind!='stage':
        files['records.bin']=file_pin(folder/'records.bin',256*1024*1024)
        check(files['records.bin']['sha256']==result['capture_sha256']==audit['capture_sha256'],
              'Capture no longer matches physical audit')
    else:
        check(audit.get('image_sha256')==p['image_sha256'] and audit.get('metadata_sha256')==p['metadata_sha256']
              and audit.get('writer_executions')==0 and audit.get('nand_writes') is False
              and 0<audit['calls']<=p['protocol_call_limit'],'Staging trace coverage mismatch')
        check(audit.get('status')=='saved-stage-trace-matches' and all(result.get(k) is True for k in (
            'full_staging_patterns_verified','image_ram_verified','writer_ram_verified','completion_poison_verified')),
            'Incomplete full staging evidence')
    return result,dict(session_id=result['session_id'],approved_plan_sha256=h,files=files,
                       dependency_sha256=load(folder/'dependency.json')['sha256'])


def assemble(base,cpu,reader,transport,layout,inputs,restore_path,captures,libusb,installed=None):
    results={};pins={}
    for kind in (('boot','stock') if installed else ('boot','stock','stage')):
        results[kind],pins[kind]=evidence(captures[kind],kind)
    library=file_pin(libusb,16*1024*1024)
    check(all(p['dependency_sha256']==library['sha256'] for p in pins.values()),'Unreviewed USB dependency')
    metadata=inputs['candidate']['metadata'];meta_hash=ram.sha(metadata);policy=inputs['candidate']['page_policy']
    for result in results.values():
        p=result['plan']
        check(p['firmware_profile_sha256']==fingerprint(base) and p['reader_profile_sha256']==fingerprint(reader)
              and p.get('probe_profile_sha256',p.get('cpu_profile_sha256'))==fingerprint(cpu)
              and p['transport_profile_sha256']==fingerprint(transport)
              and p['metadata_sha256']==meta_hash,'Evidence firmware/reader/metadata mismatch')
    bp=boot_evidence.load_policy(base,reader);profile=boot_review.load_bootloader(base,bp)
    boot=boot_review.assess(captures['boot']/'records.bin',bytes.fromhex(results['boot']['nonce_hex']),base,reader,bp,profile)
    check(boot['selected_route']=='primary' and boot['selected_rootfs']==policy['kernel']['metadata']['target']
          and boot['metadata_sha256']==meta_hash,'Unqualified installation boot target')
    pre=preinstall.load_review(base,policy)
    plan=preinstall.make_plan(base,reader,policy,pre,metadata,ram.sha(inputs['restore']['image']))
    stock=preinstall.verify(captures['stock']/'records.bin',restore_path,plan,base,reader,policy,pre,metadata,
                           bytes.fromhex(results['stock']['nonce_hex']))
    current=inputs['candidate']
    images={k:dict(name=v['image_name'],bytes=len(v['image']),sha256=ram.sha(v['image'])) for k,v in inputs.items()}
    source_state=dict(kind='recorded-stock',freshness_verified=False)
    if installed:
        from deployment.installed_candidate import assess
        source_state,stage,extra=assess(*installed,context(base,cpu,reader,transport,layout,current),
            images['restore'],pins,base,reader,policy,metadata,library['sha256'])
        pins.update(extra)
    else:
        stage=results['stage']['plan']
    # Historical source/build manifests remain historical. Bind the exercised ABI
    # and exact staged bytes, while new plans pin current source/build manifests.
    staged_image=source_state['image']['sha256'] if installed else ram.sha(current['image'])
    for key,value in dict(image_sha256=staged_image,image_bytes=len(current['image']),
                          writer_sha256=ram.sha(current['writer']),spl_sha256=ram.sha(current['spl']),
                          metadata_payload_sha256=ram.sha(current['payload']),page_policy_sha256=fingerprint(policy),
                          transport_profile_sha256=fingerprint(transport),metadata_sha256=meta_hash,
                          firmware_profile_sha256=fingerprint(base),reader_profile_sha256=fingerprint(reader),
                          cpu_profile_sha256=fingerprint(cpu)).items():
        check(stage.get(key)==value,'Staging evidence does not cover current '+key)
    from deployment.writer_transport import validate_layout
    regions=validate_layout(base,reader,transport,policy,layout)
    check(stage['ram_regions']==[dict(name=n,address=a,bytes=s) for n,a,s in regions]
          and stage['writer_entry']==reader['load_address']+layout['writer_entry_offset'], 'Staging ABI changed')
    check(context(base,cpu,reader,transport,layout,inputs['candidate'])==
          context(base,cpu,reader,transport,layout,inputs['restore']),'Candidate/restore contexts differ')
    return dict(schema_version=1,status='installation-inputs-reviewed',
        context=context(base,cpu,reader,transport,layout,current),images=images,source_pins=source_pins(),
        libusb_sha256=library['sha256'],evidence=pins,boot=boot,stock=stock,source_state=source_state,
        postwrite_collection=current['readback_plan'],
        exact_readback={k:readback.make_plan(base,reader,policy,metadata,v['sha256']) for k,v in images.items()},
        scope='one separately authorized target write; separate readback and restore authorization',
        freshness_verified=False,physical_device_accessed=False,flash_ready=False)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--version')
    for n in ('diskos','artifacts','build','staging-build','readback-build','boot-capture','stock-capture','libusb','output'):
        p.add_argument('--'+n,type=Path,required=True)
    p.add_argument('--stage-capture',type=Path)
    for n in ('previous-review','previous-image','write-capture','readback-capture'):
        p.add_argument('--'+n,type=Path)
    a=p.parse_args()
    installed=(a.previous_review,a.previous_image,a.write_capture,a.readback_capture)
    if any(installed):
        if not all(installed) or a.stage_capture:
            p.error('installed-candidate review requires all four history inputs and no stage capture')
    elif not a.stage_capture:
        p.error('recorded-stock review requires a stage capture')
    try:
        from deployment import writer_transport as writer
        base=ram.load_profile(a.version);cpu=ram.load_probe_profile(base);reader=ram.load_reader_profile(base)
        transport=ram.load_transport(base,reader);policy=ram.load_metadata_policy(base,reader)
        layout=writer.load_layout(base,reader,transport,policy)
        metadata=ram.read_file(a.boot_capture/'metadata-main.bin',policy['main_bytes'])
        ri=ram.prepare_inputs(base,cpu,reader,transport,a.readback_build,a.diskos,'rootfs')
        ri['completion']=ram.load_completion(base,transport)
        rp=collect_rootfs.make_plan(base,cpu,reader,transport,ri,metadata)
        inputs={k:writer.prepare(base,cpu,reader,transport,a.build,a.diskos,a.artifacts,k,metadata,a.staging_build)
                for k in ('candidate','restore')}
        for value in inputs.values():value['readback_plan']=rp
        bundle=assemble(base,cpu,reader,transport,layout,inputs,a.artifacts/inputs['restore']['image_name'],
                        dict(boot=a.boot_capture,stock=a.stock_capture,stage=a.stage_capture),a.libusb,
                        installed if all(installed) else None)
        a.output.mkdir(parents=True,exist_ok=False);ram.save_json(a.output/'installation-review.json',bundle)
        # These are review proposals, not a mutation of the tracked admission profile.
        proposed=dict(layout,physical_write_admitted=True,installation_review_sha256=fingerprint(bundle))
        ram.save_json(a.output/'proposed-installer-profile.json',proposed)
        for target,value in inputs.items():
            value['installation_review']=bundle
            wp=writer.make_plan(base,cpu,reader,transport,proposed,value,'write')
            ram.save_json(a.output/(target+'-write-plan.json'),dict(plan=wp,plan_sha256=fingerprint(wp)))
            plan=bundle['exact_readback'][target]
            ram.save_json(a.output/(target+'-exact-readback-plan.json'),dict(plan=plan,plan_sha256=fingerprint(plan)))
        ram.save_json(a.output/'postwrite-collection-plan.json',dict(plan=rp,plan_sha256=fingerprint(rp)))
        print(json.dumps(dict(status='offline-installation-package-prepared',bundle_sha256=fingerprint(bundle),
                              output=str(a.output),physical_device_accessed=False,flash_ready=False),indent=2))
    except (Exception,KeyboardInterrupt) as e:print(f'Installation review refused: {e}',file=sys.stderr);return 1
    return 0


if __name__=='__main__':raise SystemExit(main())
