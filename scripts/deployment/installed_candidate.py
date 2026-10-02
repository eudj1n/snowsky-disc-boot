"""Offline evidence for replacing a known, exactly read-back candidate.

Saved audits attest historical transport provenance; page equality is recomputed.
This does not establish current device identity/state or authorize a new write.
"""
from datetime import datetime

from deployment import installation_review as install
from deployment import readback, review

check = install.check
# USB diagnostic content and host source/build manifests may change. The NAND,
# boot selection, reader/writer payloads and RAM contract must remain identical.
STABLE_CONTEXT = ('version', 'firmware_sha256', 'cpu_sha256', 'reader_sha256',
                  'transport_sha256', 'page_policy_sha256', 'layout',
                  'metadata_payload_sha256', 'metadata_sha256', 'spl_sha256', 'writer_sha256')
STABLE_PROFILES = ('boot', 'bootloaders', 'preinstall', 'collectors')
PLAN_CONTEXT = {'firmware_profile_sha256':'firmware_sha256', 'cpu_profile_sha256':'cpu_sha256',
                'reader_profile_sha256':'reader_sha256', 'transport_profile_sha256':'transport_sha256',
                **{k:k for k in ('metadata_sha256', 'page_policy_sha256', 'metadata_payload_sha256',
                                 'spl_sha256', 'writer_sha256', 'build_sha256', 'image_review_sha256')}}


def timestamp(value):
    result = datetime.fromisoformat(value)
    check(result.tzinfo is not None, 'Historical timestamps require a timezone')
    return result


def session(folder, writing):
    result = install.load(folder/'result.json'); request = install.load(folder/'request.json')
    audit = install.load(folder/'offline-review.json'); plan = result['plan']
    status = 'writer-completion-observed' if writing else 'rootfs-collected'
    check(result.get('status') == status and result.get('cleanup_errors') == []
          and result.get('physical_device_accessed') is True, 'Incomplete installed-candidate session')
    check(request['plan'] == plan and request['approved_plan_sha256'] ==
          result['approved_plan_sha256'] == install.fingerprint(plan)
          and request['session_id'] == result['session_id']
          and request['nonce_hex'] == result['nonce_hex'], 'Historical request/result mismatch')
    files = {n: install.file_pin(folder/n, 32*1024*1024) for n in
             ('request.json', 'result.json', 'transfers.jsonl', 'offline-review.json', 'dependency.json')}
    check(audit['result_sha256'] == files['result.json']['sha256'] and
          audit['journal_sha256'] == files['transfers.jsonl']['sha256'] and
          audit['session_id'] == result['session_id'] and
          audit['plan_sha256'] == result['approved_plan_sha256'] and
          0 < audit['calls'] <= plan['protocol_call_limit'], 'Historical audit binding mismatch')
    if writing:
        check(plan.get('operation') == 'writer-write' and plan.get('target') == 'candidate'
              and plan.get('nand_writes') is True and plan.get('writer_executions') == 1
              and plan.get('host_retries') == 0 and plan.get('reconnect') is False
              and plan.get('physical_write_admitted') is True,
              'Expected one admitted candidate write')
        check(all(result.get(k) is True for k in ('writer_execution_attempted', 'writer_return_observed',
              'full_staging_patterns_verified', 'image_ram_verified', 'writer_ram_verified',
              'completion_poison_verified')), 'Unresolved writer or incomplete historical staging')
        check(audit.get('status') == 'saved-candidate-write-trace-matches' and
              audit.get('writer_executions') == 1 and audit.get('nand_writes') is True and
              audit['image_sha256'] == plan['image_sha256'] and
              audit['metadata_sha256'] == plan['metadata_sha256'], 'Incomplete candidate write audit')
        files['writer-result.bin'] = install.file_pin(folder/'writer-result.bin', 1024)
    else:
        check(plan.get('nand_writes') is False and result.get('writer_execution_attempted', False) is False
              and audit.get('nand_writes') is False and audit.get('writer_executions', 0) == 0
              and audit.get('status') == 'saved-postwrite-trace-matches' and audit.get('spl_executions') == 1
              and audit['records'] == result['records_completed'] and
              audit['batch_executions'] == result['batch_executions'], 'Incomplete postwrite audit')
        files['records.bin'] = install.file_pin(folder/'records.bin', 256*1024*1024)
        check(files['records.bin']['sha256'] == result['capture_sha256'] == audit['capture_sha256']
              and result['logical_image_sha256'] == audit['image_sha256'], 'Historical capture mismatch')
    check(timestamp(result['started_at']) < timestamp(result['finished_at']), 'Invalid session chronology')
    return result, dict(session_id=result['session_id'], approved_plan_sha256=install.fingerprint(plan),
                        files=files, dependency_sha256=install.load(folder/'dependency.json')['sha256'])


def assess(prior_path, image_path, write_folder, read_folder, current_context, restore,
           historical_pins, base, reader, policy, metadata, libusb_sha256):
    prior = install.load(prior_path)
    write, wpins = session(write_folder, True); read, rpins = session(read_folder, False)
    plan = write['plan']; old = prior['context']; prior_hash = install.fingerprint(prior)
    check(prior.get('schema_version') == 1 and prior.get('status') == 'installation-inputs-reviewed'
          and prior_hash == plan.get('installation_review_sha256') and
          all(prior.get(k) is False for k in ('physical_device_accessed', 'flash_ready', 'freshness_verified')),
          'Previous write is not bound to the supplied installation review')
    check(all(old[k] == current_context[k] for k in STABLE_CONTEXT) and
          all(old['additional_profile_pins'][k] == current_context['additional_profile_pins'][k]
              for k in STABLE_PROFILES), 'Installed-candidate hardware/boot/RAM contract changed')
    admitted = dict(old['layout'], physical_write_admitted=True, installation_review_sha256=prior_hash)
    check(plan.get('installer_profile_sha256') == install.fingerprint(admitted) and
          all(plan.get(k) == old[v] for k,v in PLAN_CONTEXT.items()) and
          bool(plan.get('transport_sources_sha256')) and
          all(prior['source_pins'].get(k) == v for k,v in plan['transport_sources_sha256'].items()),
          'Previous writer plan differs from its reviewed context/sources')
    check(prior['images']['restore'] == restore and
          all(prior['evidence'][k] == historical_pins[k] for k in ('boot', 'stock')),
          'Original restore or boot/stock provenance changed')
    check(prior['boot']['static_selection_reviewed'] is True and
          prior['boot']['selected_rootfs'] == policy['kernel']['metadata']['target'], 'Previous boot target mismatch')
    check(prior['libusb_sha256'] == wpins['dependency_sha256'] == rpins['dependency_sha256'] == libusb_sha256,
          'Historical USB dependency mismatch')
    image = prior['images']['candidate']
    check(image == dict(name=plan['image_name'], bytes=plan['image_bytes'], sha256=plan['image_sha256']) and
          install.file_pin(image_path, image['bytes']) == {k: image[k] for k in ('bytes', 'sha256')},
          'Previous approved image mismatch')
    check(read['plan'] == prior['postwrite_collection'], 'Postwrite collection was not in previous review')
    exact_plan = readback.make_plan(base, reader, policy, metadata, image['sha256'])
    check(exact_plan == prior['exact_readback']['candidate'], 'Previous exact readback contract changed')
    exact = readback.verify(read_folder/'records.bin', image_path, exact_plan, base, reader, policy,
                            metadata, bytes.fromhex(read['nonce_hex']))
    check(exact == install.load(read_folder/'exact-candidate-review.json') and
          exact['image_sha256'] == read['logical_image_sha256'] and
          all(exact[k] == read[other] for k, other in (('capture_sha256', 'capture_sha256'),
              ('records', 'records_completed'), ('bad_blocks', 'bad_blocks'),
              ('logical_to_physical', 'logical_to_physical'), ('ecc_histogram', 'ecc_histogram'))),
          'Recomputed exact comparison differs from historical readback')
    debug = review.check_debug(install.ram.read_file(write_folder/'writer-result.bin', 1024), 0, policy['writer'])
    end = debug['physicalEndExclusive']; start = policy['writer']['start_block']
    check(debug == write['writer_result'] and debug['badBlocks'] == [b for b in exact['bad_blocks'] if b < end]
          and exact['logical_to_physical'] == [b for b in range(start, end) if b not in debug['badBlocks']],
          'Writer completion and independent readback maps disagree')
    owner = install.load(read_folder/'owner-boot-confirmation.json')
    # The owner confirms the written image's first boot. Through combined-007 the readback came
    # first and the owner then rebooted into the system; with combined-008 leaving USB Boot after
    # the write booted the new system, so the owner confirmed that boot before the separate
    # readback session (the squashfs rootfs is mounted read-only; the readback still checks the
    # written bytes). Either order is accepted, each with its own chronology. The owner's own
    # installations keep the earlier order; the first-boot order is the public installer's.
    reboot = owner.get('observation') == 'owner-confirmed-normal-first-reboot'
    first_boot = owner.get('observation') == 'owner-confirmed-normal-first-boot'
    flags = ('automated_boot_test', 'native_process_verified') + (('live_root_verified',) if reboot else ())
    check((reboot or first_boot) and
          isinstance(owner.get('owner_answer'), str) and bool(owner['owner_answer'].strip()) and
          owner['write_session_id'] == write['session_id'] and owner['readback_session_id'] == read['session_id']
          and write['session_id'] != read['session_id'] and write['nonce_hex'] != read['nonce_hex'] and
          all(owner.get(k) is False for k in flags) and owner.get('live_root_verified', False) is False,
          'Missing or mixed owner boot confirmation')
    written, reported = timestamp(write['finished_at']), timestamp(owner['reported_at'])
    read_start, read_end = timestamp(read['started_at']), timestamp(read['finished_at'])
    check(written < read_start and (read_end < reported if reboot else written < reported < read_start),
          'Write/readback/boot chronology mismatch')
    for name in ('owner-boot-confirmation.json', 'exact-candidate-review.json'):
        rpins['files'][name] = install.file_pin(read_folder/name, 1024*1024)
    state = dict(kind='installed-candidate', image=image, previous_review_sha256=prior_hash,
                 previous_review_file=install.file_pin(prior_path, 1024*1024), exact_readback=exact,
                 owner_boot=owner, new_image_staged=False, freshness_verified=False)
    return state, plan, dict(write=wpins, readback=rpins)
