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


def run_layout(base, reader, transport, policy, path, bundle, directory=PROFILES):
    """The admission a review without a history computed for its run (plan, stage 6: the package's
    proposed-installer-profile.json), in place of the tracked profile, which it leaves as it is: only
    for such a review, and differing from the tracked profile only in its admission and review pin."""
    tracked = load_layout(base, reader, transport, policy, directory)
    layout = json.loads(ram.read_file(path, 8192))
    check(isinstance(bundle, dict) and bundle.get('source_state', {}).get('kind') == 'known-image',
          'Only a review without a history brings the admission of its run')
    contract = installation_review.layout_contract
    check(contract(layout) == contract(tracked), 'The run\'s installer profile changes more than its admission and review pin')
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


STAGING_REQUEST, STAGING_RESULT, STAGING_PATTERN, STAGING_SHA256 = 0x51475453, 0x52475453, 1, 2
STAGING_RESULT_BYTES = 80


def staging_seed(nonce):
    """The image region's pattern on the player (device/usbboot/staging.c): xorshift32 from a
    seed of the session's nonce, never zero."""
    return struct.unpack('<I', hashlib.shake_256(nonce+b'staging-pattern').digest(4))[0] or 1


def staging_request(op, address, length, seed, nonce):
    return struct.pack('<6I16s2I', STAGING_REQUEST, 1, op, address, length, seed, nonce, 0, 0)


def staging_result(op, address, length, nonce, digest=bytes(32)):
    """What the staging check answers when it passed: the pattern's words checked twice, or the hash."""
    checked = 2*(length//4) if op == STAGING_PATTERN else length
    return struct.pack('<4I16s4I32s', STAGING_RESULT, 1, op, 0, nonce, address, length, checked, 0, digest)


def prepare(base, cpu, reader, transport, build, diskos, artifacts, target, metadata, staging_build):
    check(target in ('candidate', 'restore'), 'Unknown image target')
    inputs = ram.prepare_inputs(base,cpu,reader,transport,build,diskos,'metadata')
    # The staging check (plan, stage 4c), run from the code region before the writer goes there.
    staging = ram.prepare_inputs(base,cpu,reader,transport,staging_build,diskos,'staging-check')
    writer = inputs['page_policy']['writer']
    report = review.review(artifacts,diskos,base,writer)
    name = artifact_names(base,report['variant'])[target == 'restore']
    image = ram.read_file(artifacts/name,128*1024*1024)
    check(ram.sha(image) == report['images'][name]['sha256'], 'Image changed after review')
    binary = ram.read_file(diskos/writer['writer_file'], reader['code_bytes'])
    check(ram.sha(binary) == writer['source_pins'][writer['writer_file']], 'Writer changed after review')
    review.writer_capacity(binary,writer)
    inputs.update(writer=binary,image=image,image_name=name,target=target,
                  image_review_sha256=fingerprint(report),metadata=metadata,
                  staging_payload=staging['payload'],staging_build_sha256=staging['build_sha256'],
                  completion=ram.load_completion(base,transport))
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
    # A write without a history runs in the USB Boot entry of its evidence (plan, stage 6; ram_transport.bring_up).
    same_entry = mode=='write' and inputs['installation_review']['source_state']['kind']=='known-image'
    check(len(inputs['staging_payload']) <= reader['code_bytes'], 'Staging check payload exceeds the code region')
    small = [(n,a,s) for n,a,s in regions if n != 'image']
    image_chunks = math.ceil(capacity/65536)
    # The host's patterns and contents for the small regions (18 calls a chunk, as before); the image
    # written once and read back (6 a chunk); the staging check uploaded and compared, and run twice
    # (request and result written and compared, the execution, its asks, the result read); the
    # writer's asks.
    # How often and how long the ROM is asked: the completion profile (ram_transport.load_completion).
    c = inputs['completion']
    check(c['staging_check_ms']+c['staging_sample_ms'] < layout['staging_budget_ms'], 'Insufficient staging budget for its checks')
    # Each run: request and result written and compared (12), the execution, its one ask, the result
    # read (3); the writer's ask, and one more after an ask that failed at once.
    staging_calls = 6*math.ceil(len(inputs['staging_payload'])/65536) + 2*(12+1+1+3)
    writer_asks = 2
    return dict(schema_version=1, operation='writer-'+mode, version=base['version'],
                firmware_profile_sha256=fingerprint(base), cpu_profile_sha256=fingerprint(cpu),
                reader_profile_sha256=fingerprint(reader), transport_profile_sha256=fingerprint(transport),
                installer_profile_sha256=fingerprint(layout), page_policy_sha256=fingerprint(policy),
                metadata_sha256=partitions['page_sha256'], writer_range=partitions['writer_range'],
                build_sha256=inputs['build_sha256'], spl_sha256=ram.sha(inputs['spl']),
                metadata_payload_sha256=ram.sha(inputs['payload']), writer_sha256=ram.sha(inputs['writer']),
                staging_payload_sha256=ram.sha(inputs['staging_payload']), staging_build_sha256=inputs['staging_build_sha256'],
                image_sha256=ram.sha(inputs['image']), image_bytes=capacity, target=inputs['target'],
                image_name=inputs['image_name'], image_review_sha256=inputs['image_review_sha256'],
                writer_entry=reader['load_address']+layout['writer_entry_offset'],
                ram_regions=[dict(name=n,address=a,bytes=s) for n,a,s in regions],
                pattern_passes=2, pattern='SHAKE256(nonce + chunk-address), then complement; write all before comparing all',
                image_check='the region on the player: xorshift32 words from a nonce seed, then their complement, '
                            'written and compared by the staging check; the staged image read back and compared over '
                            'USB, and its first staging_sample_bytes hashed on the player against their SHA-256',
                completion_profile_sha256=fingerprint(c),
                completion_ask=c['completion_ask'], staging_check_ms=c['staging_check_ms'],
                staging_sample_ms=c['staging_sample_ms'], staging_sample_bytes=min(c['staging_sample_bytes'],capacity),
                spl_once_an_entry=True, same_entry_required=same_entry,
                protocol_call_limit=139+18*sum(math.ceil(n/65536) for _,_,n in small)+6*image_chunks+staging_calls+writer_asks,
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

    def run_staging(self, reader, layout, op, address, length, seed, nonce, limit_key):
        """One run of the staging check from the code region (plan, stage 4c): its request and a
        zero result written and compared, the execution, the ROM asked until it answers (at most
        the plan's limit_key), the result read for the caller to check against what a pass answers."""
        q = staging_request(op, address, length, seed, nonce)
        for where, data in ((reader['request_address'], q), (reader['result_address'], bytes(STAGING_RESULT_BYTES))):
            self.write(where, data)
            check(self.read(where, len(data)) == data, 'Staging check request/result comparison failed')
        self.record['staging_executions'] = self.record.get('staging_executions', 0)+1
        check(self.record['staging_executions'] <= 2, 'Repeated staging check')
        self.control(4, reader['load_address'], 'staging_execution_attempted')
        plan = self.record['plan']
        ms = self.wait_ready(plan[limit_key], plan['completion_ask'])
        raw = self.read(reader['result_address'], STAGING_RESULT_BYTES)
        return ms, raw

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
    small = [r for r in regions if r[0] != 'image']
    _, image_address, image_bytes = next(r for r in regions if r[0] == 'image')
    # The small regions as before: the host's two pattern passes over USB.
    for turn in range(2):
        for _,address,size,_ in pieces(small): session.write(address,pattern(nonce,address,size,turn))
        for _,address,size,_ in pieces(small):
            check(session.read(address,size) == pattern(nonce,address,size,turn), 'Full staging RAM pattern mismatch')
    # The image region on the player (plan, stage 4c): the staging check, from the code region,
    # writes and compares its pattern and the complement there instead of 4 x 96 MiB over USB.
    staging = inputs['staging_payload']
    for _,address,size,offset in pieces([('code',reader['load_address'],len(staging))]):
        session.write(address,staging[offset:offset+size])
    for _,address,size,offset in pieces([('code',reader['load_address'],len(staging))]):
        check(session.read(address,size) == staging[offset:offset+size], 'Staging check upload comparison failed')
    ms, raw = session.run_staging(reader,layout,STAGING_PATTERN,image_address,image_bytes,staging_seed(nonce),nonce,'staging_check_ms')
    check(raw == staging_result(STAGING_PATTERN,image_address,image_bytes,nonce), 'The image region failed its check on the player')
    record.update(full_staging_patterns_verified=True, image_region_check_ms=ms)
    # The image once over USB, all of it written before any is read back and compared.
    image = inputs['image']
    for _,address,size,offset in pieces([('image',image_address,image_bytes)]):
        session.write(address,image[offset:offset+size])
    for _,address,size,offset in pieces([('image',image_address,image_bytes)]):
        check(session.read(address,size) == image[offset:offset+size], 'The staged image differs from the reviewed image')
    record.update(image_ram_verified=True)
    # The hash on the player, measured on a sample first: its code runs uncached (kseg1), so a
    # whole image's hash there may take longer than the read back. Its time decides.
    sample = record['plan']['staging_sample_bytes']
    ms, raw = session.run_staging(reader,layout,STAGING_SHA256,image_address,sample,0,nonce,'staging_sample_ms')
    check(raw == staging_result(STAGING_SHA256,image_address,sample,nonce,hashlib.sha256(image[:sample]).digest()),
          'The staged image sample hashes differently on the player')
    record.update(image_hash_sample_verified=True, image_hash_sample_ms=ms)
    poison = hashlib.shake_256(nonce+b'writer-debug').digest(1024)
    # Explicitly poison the signature/completion/result words, independent of nonce entropy.
    poison = bytearray(poison)
    for index in (0,9,16): struct.pack_into('<I',poison,4*index,0xeeeeeeee)
    poison = bytes(poison)
    with (session.journal.output/'writer-debug-poison.bin').open('xb') as f:
        f.write(poison); f.flush(); ram.os.fsync(f.fileno())
    code = inputs['writer'].ljust(reader['code_bytes'],b'\0')
    def content(name,address,size,offset):
        if name == 'code': return code[offset:offset+size]
        if name == 'writer-debug': return poison[offset:offset+size]
        return pattern(nonce,address,size,1)
    # The writer over the staging check in the code region, the guard, the rest as before.
    for item in pieces(small): session.write(item[1],content(*item))
    for item in pieces(small):
        check(session.read(item[1],item[2]) == content(*item), 'Staged writer/guard comparison failed')
    record.update(status='writer-staging-verified',writer_ram_verified=True,completion_poison_verified=True)


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
    # The ROM answers again once the writer has returned to it (plan, stage 4c): one request asked
    # at once and held for at most writer_wait_ms; an ask changes nothing on the player, so one that
    # fails at once is no outcome, only the ROM's answer is, and none in the whole wait leaves it
    # unknown. Without the ask, the fixed wait as before.
    record['writer_ms'] = session.wait_ready(layout['writer_wait_ms'],record['plan']['completion_ask'],tolerant=True)
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
                  completion_poison_verified=False,staging_execution_attempted=False,image_hash_sample_verified=False,
                  postwrite_verified=False,boot_verified=False,flash_ready=False)
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
    for name in ('build','staging-build','diskos','artifacts','metadata-page'): parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--libusb',type=Path); parser.add_argument('--output',type=Path)
    parser.add_argument('--approved-plan-sha256')
    parser.add_argument('--installation-review',type=Path)
    parser.add_argument('--readback-build',type=Path)
    parser.add_argument('--installer-profile',type=Path,
                        help='A write without a history: the admission its review computed for the run (the package\'s proposed profile)')
    parser.add_argument('--confirm-reviewed-device-state',action='store_true',
                        help='Confirm the same player, unchanged boot/kernel/OTA/layout and target-specific write history; never resolves an unknown writer outcome')
    args = parser.parse_args()
    extras = (args.libusb,args.output,args.approved_plan_sha256)
    if (args.action == 'plan' and any(x is not None for x in extras)) or (args.action == 'acquire' and not all(extras)):
        parser.error('acquire requires libusb, fresh output and approved plan; plan accepts none')
    if (args.mode=='write' and not (args.installation_review and args.readback_build)) or (
            args.mode=='stage' and (args.installation_review or args.readback_build or args.confirm_reviewed_device_state)):
        parser.error('write requires installation review and readback build; stage accepts neither')
    if args.installer_profile and args.mode!='write':
        parser.error('Only a write takes the admission of its run')
    if args.confirm_reviewed_device_state and args.action!='acquire':
        parser.error('Evidence currency confirmation belongs only to a separately authorized write acquisition')
    try:
        base = ram.load_profile(args.version); cpu = ram.load_probe_profile(base); reader = ram.load_reader_profile(base)
        transport = ram.load_transport(base,reader)
        policy = ram.load_metadata_policy(base,reader)
        inputs = prepare(base,cpu,reader,transport,args.build,args.diskos,args.artifacts,args.target,
                         ram.read_file(args.metadata_page,policy['main_bytes']),args.staging_build)
        layout = load_layout(base,reader,transport,inputs['page_policy'])
        if args.mode=='write':
            inputs['installation_review']=installation_review.load(args.installation_review)
            ri=ram.prepare_inputs(base,cpu,reader,transport,args.readback_build,args.diskos,'rootfs')
            ri['completion']=ram.load_completion(base,transport)
            inputs['readback_plan']=installation_review.collect_rootfs.make_plan(base,cpu,reader,transport,ri,inputs['metadata'])
            if args.installer_profile:
                layout = run_layout(base,reader,transport,inputs['page_policy'],args.installer_profile,inputs['installation_review'])
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
