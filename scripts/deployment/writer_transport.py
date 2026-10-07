#!/usr/bin/env python3
"""Reviewed writer staging and single invocation; no replay or automatic restore.

Physical writes require a profile-pinned installation review, current device
state confirmation and separately authorized exact plan; stage mode never writes.
"""
import argparse
import ctypes as C
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from firmware_profile import PROFILES, artifact_names, fingerprint, sha
from deployment import ram_transport as ram
from deployment.collect_rootfs import Journal
from deployment import review, installation_review

check = ram.check


def load_layout(base, reader, transport, policy, directory=PROFILES):
    layout = json.loads(ram.read_file(directory/'installers'/f'v{base["version"]}.json', 8192))
    validate_layout(base, reader, transport, policy, layout)
    return layout


def validate_layout(base, reader, transport, policy, layout):
    check(layout.get('schema_version') == 1 and layout.get('version') == base['version'], 'Invalid installer schema')
    for name, value in [('firmware', base), ('page_policy', policy), ('transport', transport), ('writer', policy['writer'])]:
        key = name+'_sha256' if name == 'page_policy' else name+'_profile_sha256'
        check(layout.get(key) == fingerprint(value), f'Installer {name} audit changed')
    check(type(layout.get('physical_write_admitted')) is bool, 'Missing physical write admission')
    binding=layout.get('installation_review_sha256')
    check((binding is None and not layout['physical_write_admitted']) or sha(binding),
          'Write admission requires a pinned installation evidence bundle')
    for key in ('writer_entry_offset', 'writer_stack_bottom', 'writer_stack_top', 'debug_address', 'image_address', 'dram_end'):
        check(type(layout.get(key)) is int and layout[key] % 16 == 0, f'Invalid installer address: {key}')
    check(0 < layout['writer_entry_offset'] < reader['code_bytes'], 'Writer entry outside code')
    # The pinned legacy executable is not relocatable. These are its linked ABI,
    # not firmware-version checks; a different writer needs a new ABI review.
    check((reader['load_address'], layout['writer_entry_offset'], layout['writer_stack_top'],
           layout['debug_address'], layout['image_address']) ==
          (0xa0c00000, 0x30, 0xa0bffff0, 0xa0a00000, 0xa1000000), 'Unsupported linked writer ABI')
    check(0xa0000000 < layout['dram_end'] <= 0xa8000000, 'Unreviewed RAM ceiling')
    check(layout['writer_stack_bottom'] < layout['writer_stack_top'], 'Invalid writer stack')
    regions = ram.regions(reader)+[
        ('writer-stack', layout['writer_stack_bottom'], layout['writer_stack_top']-layout['writer_stack_bottom']),
        ('writer-debug', layout['debug_address'], 1024),
        ('image', layout['image_address'], policy['writer']['logical_blocks']*policy['writer']['block_bytes'])]
    seen = []
    for name, address, size in regions:
        check(0 < size and 0xa0000000 <= address < address+size <= layout['dram_end'], f'Invalid RAM range: {name}')
        check(all(address+size <= a or a+n <= address for a,n in seen), f'Overlapping RAM: {name}')
        seen.append((address,size))
    for key, low, high in [('staging_budget_ms',10000,900000), ('session_budget_ms',20000,3600000),
                           ('writer_wait_ms',1000,1800000)]:
        check(type(layout.get(key)) is int and low <= layout[key] <= high, f'Invalid installer budget: {key}')
    check(layout['session_budget_ms'] >= layout['staging_budget_ms']+layout['writer_wait_ms'], 'Insufficient writer session budget')
    return regions


def prepare(base, cpu, reader, transport, build, diskos, artifacts, target, metadata):
    check(target in ('candidate', 'restore'), 'Unknown image target')
    inputs = ram.prepare_inputs(base,cpu,reader,transport,build,diskos,'metadata')
    writer = inputs['page_policy']['writer']
    report = review.review(artifacts,diskos,base,writer)
    name = artifact_names(base,report['variant'])[target == 'restore']
    image = ram.read_file(artifacts/name,128*1024*1024)
    check(ram.sha(image) == report['images'][name]['sha256'], 'Image changed after review')
    binary = ram.read_file(diskos/writer['writer_file'], reader['code_bytes'])
    check(ram.sha(binary) == writer['source_pins'][writer['writer_file']], 'Writer changed after review')
    review.writer_capacity(binary,writer)
    inputs.update(writer=binary,image=image,image_name=name,target=target,
                  image_review_sha256=fingerprint(report),metadata=metadata)
    return inputs


def make_plan(base, cpu, reader, transport, layout, inputs, mode):
    check(mode in ('stage','write'), 'Unknown writer operation')
    policy = inputs['page_policy']
    regions = validate_layout(base,reader,transport,policy,layout)
    partitions = ram.review_partitions(inputs['metadata'],policy['kernel'],policy['chip'],policy['writer'])
    capacity = policy['writer']['logical_blocks']*policy['writer']['block_bytes']
    check(isinstance(inputs['image'],bytes) and len(inputs['image']) == capacity, 'Image capacity mismatch')
    check(0 < layout['writer_entry_offset'] < len(inputs['writer']) <= reader['code_bytes'], 'Invalid writer binary size')
    writer = policy['writer']
    check(ram.sha(inputs['writer']) == writer['source_pins'][writer['writer_file']], 'Writer binary pin mismatch')
    review.writer_capacity(inputs['writer'],writer)
    check(inputs['target'] in ('candidate','restore'), 'Invalid writer target')
    binding=installation_review.validate_binding(inputs.get('installation_review'),base,cpu,reader,transport,layout,inputs) if mode=='write' else None
    return dict(schema_version=1, operation='writer-'+mode, version=base['version'],
                firmware_profile_sha256=fingerprint(base), cpu_profile_sha256=fingerprint(cpu),
                reader_profile_sha256=fingerprint(reader), transport_profile_sha256=fingerprint(transport),
                installer_profile_sha256=fingerprint(layout), page_policy_sha256=fingerprint(policy),
                metadata_sha256=partitions['page_sha256'], writer_range=partitions['writer_range'],
                build_sha256=inputs['build_sha256'], spl_sha256=ram.sha(inputs['spl']),
                metadata_payload_sha256=ram.sha(inputs['payload']), writer_sha256=ram.sha(inputs['writer']),
                image_sha256=ram.sha(inputs['image']), image_bytes=capacity, target=inputs['target'],
                image_name=inputs['image_name'], image_review_sha256=inputs['image_review_sha256'],
                writer_entry=reader['load_address']+layout['writer_entry_offset'],
                ram_regions=[dict(name=n,address=a,bytes=s) for n,a,s in regions],
                pattern_passes=2, pattern='SHAKE256(nonce + chunk-address), then complement; write all before comparing all',
                protocol_call_limit=136+18*sum(math.ceil(n/65536) for _,_,n in regions),
                writer_wait_ms=layout['writer_wait_ms'],
                session_budget_ms=layout['session_budget_ms'] if mode == 'write' else layout['staging_budget_ms'],
                transport_sources_sha256={name:ram.probe.digest(ram.ROOT/name) for name in (
                    'scripts/deployment/writer_transport.py','scripts/deployment/ram_transport.py',
                    'scripts/deployment/rom_probe.py','scripts/deployment/collect_rootfs.py',
                    'scripts/deployment/review.py','scripts/deployment/nand_records.py',
                    'scripts/deployment/installation_review.py')},
                nand_writes=mode == 'write', writer_executions=1 if mode == 'write' else 0,
                physical_write_admitted=layout['physical_write_admitted'], host_retries=0,
                installation_review_sha256=binding, requires_current_evidence_confirmation=mode=='write',
                writer_block_attempts=policy['writer']['max_tries'], reconnect=False,
                physical_device_accessed=False, postwrite_verified=False, flash_ready=False)


class Session(ram.Session):
    def execute(self,address,field):
        expected = {'spl_execution_attempted':self.config['spl_entry'],
                    'page_execution_attempted':self.record['reader_entry']}
        check(field in expected and address == expected[field] and not self.record.get(field), 'Repeated/unreviewed execution')
        self.control(4,address,field)
        self.wait_ms(self.config['settle_ms'])
        self.control(0)

    def wait_ms(self,duration):
        end = self.clock()+duration/1000
        check(end < self.deadline, 'Insufficient remaining execution budget')
        while self.clock() < end:
            self.sleep(min(1,end-self.clock()))


def pieces(regions):
    for name,address,size in regions:
        for offset in range(0,size,65536):
            yield name,address+offset,min(65536,size-offset),offset


def pattern(nonce,address,size,turn):
    data = hashlib.shake_256(nonce+struct.pack('<I',address)).digest(size)
    return bytes(b ^ 255 for b in data) if turn else data


def stage(session, reader, layout, inputs, nonce):
    record = session.record
    # Existing bounded metadata workflow includes one SPL and fresh chip/ECC/partition observation.
    ram.observe(session,reader,inputs,'metadata',nonce,record)
    observed = (session.journal.output/'metadata-main.bin').read_bytes()
    check(observed == inputs['metadata'], 'Partition metadata changed; stop before staging writer')
    regions = [(r['name'],r['address'],r['bytes']) for r in record['plan']['ram_regions']]
    for turn in range(2):
        for _,address,size,_ in pieces(regions): session.write(address,pattern(nonce,address,size,turn))
        for _,address,size,_ in pieces(regions):
            check(session.read(address,size) == pattern(nonce,address,size,turn), 'Full staging RAM pattern mismatch')
    record['full_staging_patterns_verified'] = True
    poison = hashlib.shake_256(nonce+b'writer-debug').digest(1024)
    # Explicitly poison the signature/completion/result words, independent of nonce entropy.
    poison = bytearray(poison)
    for index in (0,9,16): struct.pack_into('<I',poison,4*index,0xeeeeeeee)
    poison = bytes(poison)
    with (session.journal.output/'writer-debug-poison.bin').open('xb') as f:
        f.write(poison); f.flush(); ram.os.fsync(f.fileno())
    code = inputs['writer'].ljust(reader['code_bytes'],b'\0')
    def content(name,address,size,offset):
        if name == 'image': return inputs['image'][offset:offset+size]
        if name == 'code': return code[offset:offset+size]
        if name == 'writer-debug': return poison[offset:offset+size]
        return pattern(nonce,address,size,1)
    for item in pieces(regions): session.write(item[1],content(*item))
    for item in pieces(regions):
        check(session.read(item[1],item[2]) == content(*item), 'Staged image/writer/guard comparison failed')
    record.update(status='writer-staging-verified',image_ram_verified=True,writer_ram_verified=True,
                  completion_poison_verified=True)


def invoke_writer(session, layout, inputs):
    record = session.record
    check(record['plan']['operation'] == 'writer-write' and layout['physical_write_admitted'], 'Physical writer admission is closed')
    check(record.get('image_ram_verified') and record.get('writer_ram_verified') and
          record.get('completion_poison_verified') and not record.get('writer_execution_attempted'),
          'Writer requires complete staging and a single invocation')
    # Budget refusal before invocation must never be labelled a possible write.
    check(session.clock()+layout['writer_wait_ms']/1000+5 < session.deadline, 'Insufficient writer completion budget')
    record['status'] = 'writer-outcome-unknown'
    session.control(4,record['plan']['writer_entry'],'writer_execution_attempted')
    session.wait_ms(layout['writer_wait_ms'])
    session.control(0)
    record['writer_return_observed'] = True
    raw = session.read(layout['debug_address'],1024)
    with (session.journal.output/'writer-result.bin').open('xb') as f:
        f.write(raw); f.flush(); ram.os.fsync(f.fileno())
    record['writer_result'] = review.check_debug(raw,0,inputs['page_policy']['writer'])
    record['status'] = 'writer-completion-observed'
    # The legacy device record has no nonce; independent readback/boot are still required.


def acquire(plan,approved,base,cpu,reader,transport,layout,inputs,library,output,
            loader=C.CDLL,clock=time.monotonic,sleep=time.sleep,evidence_current=False):
    mode = plan['operation'].removeprefix('writer-')
    check(plan == make_plan(base,cpu,reader,transport,layout,inputs,mode) and approved == fingerprint(plan), 'Writer plan/inputs changed')
    check(mode != 'write' or layout['physical_write_admitted'], 'Physical writer admission is closed; no USB access')
    if mode=='write':
        check(evidence_current is True,'Confirm reviewed state of the same player and no unresolved writer; see target-specific procedure')
        ram.read_file(library,16*1024*1024)
        check(ram.probe.digest(library)==inputs['installation_review']['libusb_sha256'],'USB library differs from installation review')
    output.mkdir(mode=0o700)
    nonce = uuid.uuid4().bytes
    record = dict(plan=plan,approved_plan_sha256=approved,session_id=str(uuid.uuid4()),nonce_hex=nonce.hex(),
                  evidence_currency_confirmed=evidence_current if mode=='write' else False,
                  status='incomplete',started_at=datetime.now(timezone.utc).isoformat(),reader_entry=reader['load_address'],
                  physical_device_accessed=False,spl_execution_attempted=False,page_execution_attempted=False,
                  writer_execution_attempted=False,writer_return_observed=False,ram_roundtrip_passed=False,
                  full_staging_patterns_verified=False,image_ram_verified=False,writer_ram_verified=False,
                  completion_poison_verified=False,postwrite_verified=False,boot_verified=False,flash_ready=False)
    ram.save_json(output/'request.json',record)
    journal = session = None
    try:
        ram.read_file(library,16*1024*1024)
        ram.save_json(output/'dependency.json',dict(path=str(library.resolve()),sha256=ram.probe.digest(library)))
        journal = Journal(output,plan['protocol_call_limit'])
        journal.event(phase='usb-discovery-attempt')
        config = dict(transport,session_budget_ms=plan['session_budget_ms'])
        session = Session(ram.bind(loader(str(library))),cpu,config,record,journal,clock,sleep)
        session.open()
        stage_deadline = min(session.deadline,clock()+layout['staging_budget_ms']/1000)
        overall_deadline = session.deadline
        session.deadline = stage_deadline
        stage(session,reader,layout,inputs,nonce)
        session.deadline = overall_deadline
        if mode == 'write': invoke_writer(session,layout,inputs)
    except (Exception,KeyboardInterrupt) as exc:
        record.update(status='writer-outcome-unknown' if record['writer_execution_attempted'] else 'failed-before-writer',
                      error=f'{type(exc).__name__}: {exc}')
    finally:
        if session and session.close():
            record['status'] = 'writer-outcome-unknown' if record['writer_execution_attempted'] else 'failed-before-writer'
        if journal: journal.file.close()
        record['finished_at'] = datetime.now(timezone.utc).isoformat()
        ram.save_json(output/'result.json',record)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('plan','acquire'))
    parser.add_argument('--mode',choices=('stage','write'),required=True)
    parser.add_argument('--target',choices=('candidate','restore'),required=True)
    parser.add_argument('--version')
    for name in ('build','diskos','artifacts','metadata-page'): parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--libusb',type=Path); parser.add_argument('--output',type=Path)
    parser.add_argument('--approved-plan-sha256')
    parser.add_argument('--installation-review',type=Path)
    parser.add_argument('--readback-build',type=Path)
    parser.add_argument('--confirm-reviewed-device-state',action='store_true',
                        help='Confirm the same player, unchanged boot/kernel/OTA/layout and target-specific write history; never resolves an unknown writer outcome')
    args = parser.parse_args()
    extras = (args.libusb,args.output,args.approved_plan_sha256)
    if (args.action == 'plan' and any(x is not None for x in extras)) or (args.action == 'acquire' and not all(extras)):
        parser.error('acquire requires libusb, fresh output and approved plan; plan accepts none')
    if (args.mode=='write' and not (args.installation_review and args.readback_build)) or (
            args.mode=='stage' and (args.installation_review or args.readback_build or args.confirm_reviewed_device_state)):
        parser.error('write requires installation review and readback build; stage accepts neither')
    if args.confirm_reviewed_device_state and args.action!='acquire':
        parser.error('Evidence currency confirmation belongs only to a separately authorized write acquisition')
    try:
        base = ram.load_profile(args.version); cpu = ram.load_probe_profile(base); reader = ram.load_reader_profile(base)
        transport = ram.load_transport(base,reader)
        policy = ram.load_metadata_policy(base,reader)
        inputs = prepare(base,cpu,reader,transport,args.build,args.diskos,args.artifacts,args.target,
                         ram.read_file(args.metadata_page,policy['main_bytes']))
        layout = load_layout(base,reader,transport,inputs['page_policy'])
        if args.mode=='write':
            inputs['installation_review']=installation_review.load(args.installation_review)
            ri=ram.prepare_inputs(base,cpu,reader,transport,args.readback_build,args.diskos,'rootfs')
            inputs['readback_plan']=installation_review.collect_rootfs.make_plan(base,cpu,reader,transport,ri,inputs['metadata'])
        plan = make_plan(base,cpu,reader,transport,layout,inputs,args.mode)
        result = dict(plan=plan,plan_sha256=fingerprint(plan)) if args.action == 'plan' else acquire(
            plan,args.approved_plan_sha256,base,cpu,reader,transport,layout,inputs,args.libusb,args.output,
            evidence_current=args.confirm_reviewed_device_state)
        print(json.dumps(result,indent=2))
        return 1 if result.get('status') in ('failed-before-writer','writer-outcome-unknown') else 0
    except (Exception,KeyboardInterrupt) as exc:
        print(f'{type(exc).__name__}: {exc}',file=sys.stderr)
        return 2


if __name__ == '__main__': raise SystemExit(main())
