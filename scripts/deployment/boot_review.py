#!/usr/bin/env python3
"""Assess a reviewed SPL's static boot choice from saved records. Never accesses USB.

This is not live-root evidence, capture authentication, freshness or flash admission.
The acquisition classifier and its historical reports remain unchanged.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from firmware_profile import PROFILES, fingerprint, require, sha
from deployment import boot_evidence as boot
from deployment.review import regular
from deployment.nand_records import encode_request, decode_result


def digest(data):return hashlib.sha256(data).hexdigest()


def validate_profile(p,base,policy):
    require(p.get('schema_version')==1 and p.get('format')=='ingenic-spl-ota-prefix-v1'
            and p.get('version')==base['version'] and p.get('firmware_sha256')==fingerprint(base)
            and p.get('boot_policy_sha256')==fingerprint(policy),'Bootloader review/profile mismatch')
    require(p.get('writes_admitted') is False,'Offline review cannot admit writes')
    require(type(p.get('boot_bytes')) is int and p['boot_bytes']==policy['profile']['partitions'][0]['size']
            and sha(p.get('boot_sha256')),'Invalid reviewed boot image')
    require(p.get('selector_read_bytes')==128 and p.get('selector_storage_bytes')==256,
            'Unreviewed selector read/storage contract')
    prefix=p.get('secondary_prefix');tokens=p.get('accepted_tokens')
    require(isinstance(prefix,str) and re.fullmatch(r'ota:[a-z0-9]+',prefix) and len(prefix)<128,
            'Invalid secondary prefix')
    require(isinstance(tokens,list) and 1<=len(tokens)<=16
            and all(isinstance(x,str) and re.fullmatch(r'ota:[a-z0-9]+',x) and len(x)<128 for x in tokens)
            and len(tokens)==len(set(tokens)) and prefix in tokens,'Invalid reviewed selectors')
    for name in ('primary','secondary'):
        r=p.get(name,{})
        require(all(isinstance(r.get(k),str) and re.fullmatch(r'[a-z][a-z0-9_-]*',r[k]) for k in ('kernel','rootfs')),
                'Invalid route names')
        require(isinstance(r.get('root_device'),str) and re.fullmatch(r'/dev/mtdblock_bbt_ro[0-9]+',r['root_device']),
                'Invalid root device')
        require(type(r.get('bootargs_offset')) is int and 0<=r['bootargs_offset']<p['boot_bytes'],
                'Invalid boot argument offset')
    require(type(p.get('image_base')) is int and 0<=p['image_base']<=0xffffffff
            and type(p.get('entry_offset')) is int and 0<=p['entry_offset']<p['boot_bytes']
            and p['image_base']+p['boot_bytes']<=0x100000000,'Invalid SPL address range')
    return p


def load_bootloader(base,policy,directory=PROFILES):
    path=directory/'bootloaders'/f'v{base["version"]}.json';regular(path,16384)
    return validate_profile(json.loads(path.read_text()),base,policy)


def assess(path,nonce,base,reader,policy,profile):
    validate_profile(profile,base,policy)
    page=policy['page_policy'];main=page['main_bytes'];ppb=page['chip']['pages_per_block']
    spans=boot.scopes(policy);uboot,ota=spans
    require(uboot['first_page']==0,'This reviewed SPL requires the original zero-based boot map')
    count=2*((uboot['end_page']//ppb)+(ota['end_page']-ota['first_page'])//ppb)+1+uboot['end_page']
    require(regular(path,count*4476)==count*4476,'Incomplete or extra boot records')
    sequence=1;capture=hashlib.sha256();hist=[0]*16
    with path.open('rb') as stream:
        def read(number):
            nonlocal sequence
            q=encode_request(2,nonce,sequence,number);record=stream.read(4476)
            require(len(record)==4476 and record[:48]==q,'Unexpected boot record order/nonce/scope')
            d=decode_result(record[48:],q,main+page['oob_bytes'])
            require(0<d['observed_id']<0xffffff and d['observed_id']&page['id_mask']==page['expected_id']
                    and all(0<=d[k]<=255 for k in ('feature','protect','status'))
                    and not d['status']&1 and d['feature']&page['feature_mask']==page['feature_value']
                    and 2<=d['polls']<=2*reader['nand_polls'] and d['transfers']==d['polls']+8,
                    'Untrusted page ID/configuration/counters')
            ecc=(d['status']&page['ecc_mask'])>>page['ecc_shift']
            require(page['ecc_admitted']&(1<<ecc),'Untrusted ECC status')
            hist[ecc]+=1;capture.update(record);sequence+=1
            return d['data']
        def markers(span):
            good={};bad=[]
            for number in range(span['first_page'],span['end_page'],ppb):
                a,b=read(number),read(number)
                require(a[main]==b[main],'Unstable marker')
                if a[main]==255:
                    require(a[:main]==b[:main],'Unstable first-page main bytes')
                    good[number//ppb]=a[:main]
                else:bad.append(number//ppb)
            require(good,'No good blocks in evidence partition')
            return good,bad
        boot_good,boot_bad=markers(uboot)
        require(not boot_bad,'Reviewed boot image requires all boot blocks good; repeat boot-chain review')
        metadata=read(page['page'])[:main];image=bytearray()
        for number in range(uboot['end_page']):
            data=read(number)
            if number%ppb==0:
                require(data[main]==255 and data[:main]==boot_good[number//ppb],'Boot main/marker changed')
            if number==page['page']:require(data[:main]==metadata,'Metadata changed during capture')
            image+=data[:main]
        ota_good,ota_bad=markers(ota)
        require(not stream.read(1),'Extra boot records')
    require(len(image)==profile['boot_bytes'] and digest(image)==profile['boot_sha256'],
            'Unreviewed bootloader bytes; repeat RE before acceptance')
    table=boot.ram.review_partitions(metadata,page['kernel'],page['chip'],page['writer'])
    parts=table['partitions']
    for expected in policy['profile']['partitions']:
        actual=next((x for x in parts if x['name']==expected['name']),None)
        require(actual is not None and all(actual[k]==v for k,v in expected.items())
                and actual['mask_flags']==actual['manager_mode']==0,'Evidence partition mismatch')
    # Stock uses a first-match prefix lookup, so reject misleading earlier names.
    def exact_lookup(name):
        found=next((x for x in parts if x['name'].startswith(name)),None)
        require(found is not None and found['name']==name,'Missing or shadowed boot partition name')
        return found
    exact_lookup('ota')
    for name in ('primary','secondary'):
        route=profile[name];exact_lookup(route['kernel'])
        part=exact_lookup(route['rootfs']);index=parts.index(part)
        require(route['root_device']==f'/dev/mtdblock_bbt_ro{index}', 'Root device/partition index mismatch')
        start=route['bootargs_offset'];end=image.find(0,start,min(start+512,len(image)))
        require(end>=start,'Unterminated reviewed boot arguments')
        args=bytes(image[start:end]).decode('ascii')
        require([x for x in args.split() if x.startswith('root=')]==['root='+route['root_device']],
                'Boot arguments do not name the reviewed root device')
    block=min(ota_good);raw=ota_good[block][:profile['selector_storage_bytes']]
    tokens=[t for t in profile['accepted_tokens'] if raw==t.encode().ljust(len(raw),b' ')]
    require(len(tokens)==1,'Unknown or malformed selector; stock fallback is not acceptance')
    token=tokens[0]
    route_name='secondary' if raw[:profile['selector_read_bytes']].startswith(profile['secondary_prefix'].encode()) else 'primary'
    route=profile[route_name]
    return dict(status='offline-boot-selection-reviewed',firmware_version=base['version'],
        firmware_sha256=fingerprint(base),bootloader_profile_sha256=fingerprint(profile),
        boot_policy_sha256=fingerprint(policy),boot_sha256=digest(image),metadata_sha256=digest(metadata),
        capture_sha256=capture.hexdigest(),nonce_hex=nonce.hex(),records=sequence-1,ecc_histogram=hist,
        boot_good_blocks=list(boot_good),ota_good_blocks=list(ota_good),ota_bad_blocks=ota_bad,
        selector_block=block,selector_page=block*ppb,selector_sha256=digest(raw),selector_token=token,
        ignored_ota_selector_blocks=[b for b in ota_good if b!=block],selected_route=route_name,
        selected_kernel=route['kernel'],selected_rootfs=route['rootfs'],root_device=route['root_device'],
        static_selection_reviewed=True,active_boot_verified=False,physical_provenance_verified=False,
        physical_device_accessed=False,writes_admitted=False,flash_ready=False)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--version')
    p.add_argument('--records',required=True,type=Path);p.add_argument('--nonce-hex',required=True)
    a=p.parse_args()
    try:
        base=boot.ram.load_profile(a.version);reader=boot.ram.load_reader_profile(base)
        policy=boot.load_policy(base,reader);profile=load_bootloader(base,policy)
        print(json.dumps(assess(a.records,bytes.fromhex(a.nonce_hex),base,reader,policy,profile),indent=2))
    except (ValueError,OSError,KeyError,TypeError) as e:p.exit(1,f'Boot review refused: {e}\n')


if __name__=='__main__':main()
