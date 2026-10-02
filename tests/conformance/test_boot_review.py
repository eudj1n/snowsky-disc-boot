"""Synthetic SPL/OTA record fixtures; no firmware, USB, Ghidra or sibling repo."""
import copy
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'scripts'))
from deployment import boot_review as review
from deployment.nand_records import encode_request
import test_kernel_review


class BootReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.base=review.boot.ram.load_profile();self.reader=review.boot.ram.load_reader_profile(self.base)
        self.policy=review.boot.load_policy(self.base,self.reader)
        self.profile=review.load_bootloader(self.base,self.policy)
        # Reduce only the synthetic fixture's boot extent. Its bytes are generated here.
        self.policy['profile']['partitions'][0]['size']=131072
        k=test_kernel_review.KernelReviewTests();k.setUp();self.metadata=k.page({0:{1:131072}})
        self.image=bytearray([0x5a])*131072
        self.image[11*2048:12*2048]=self.metadata
        for route in ('primary','secondary'):
            p=self.profile[route];s=('console=synthetic root='+p['root_device']+' ro\0').encode()
            a=p['bootargs_offset'];self.image[a:a+len(s)]=s
        self.profile.update(boot_bytes=len(self.image),boot_sha256=review.digest(self.image),
                            boot_policy_sha256=review.fingerprint(self.policy))
        self.nonce=bytes(range(16));self.path=self.root/'records.bin'

    def fixture(self,token='ota:backup',bad=(),later=None,alter=None):
        selector=token.encode().ljust(256,b' ')+bytes([255])*(2048-256)
        first=self.policy['profile']['partitions'][1]['offset']//2048
        pages=[0,0,11]+list(range(64))+[n for n in range(first,first+512,64) for _ in range(2)]
        rows=[]
        for i,n in enumerate(pages,1):
            if n<64:data=bytes(self.image[n*2048:(n+1)*2048])
            elif n==first or n//64 in bad:data=selector
            else:data=(later.encode().ljust(256,b' ')+bytes([255])*(2048-256)) if later else bytes([255])*2048
            # First good page following bad markers has the reviewed selector.
            if n>=first and n//64==next(b for b in range(first//64,first//64+8) if b not in bad):data=selector
            data+=bytes([0 if n//64 in bad else 255])+bytes(127)
            q=encode_request(2,self.nonce,i,n)
            w=[0x3153524e,1,0x454e4f44,0,*struct.unpack('<4I',self.nonce),i,2,n,2176,zlib.crc32(data),0x120b,0x38,0x10,0,2,10]
            if alter:data,w,q=alter(i,n,data,w,q)
            rows.append(q+struct.pack('<19I',*w)+data+bytes(4352-len(data)))
        self.path.write_bytes(b''.join(rows))
        return rows

    def assess(self):return review.assess(self.path,self.nonce,self.base,self.reader,self.policy,self.profile)

    def test_backup_is_primary_without_live_or_flash_claim(self):
        self.fixture();r=self.assess()
        self.assertEqual(r['selected_kernel'],'kernel');self.assertEqual(r['selected_rootfs'],'rootfs')
        self.assertEqual(r['selector_token'],'ota:backup');self.assertEqual(r['selector_block'],1368)
        self.assertTrue(r['static_selection_reviewed'])
        for n in ('active_boot_verified','physical_device_accessed','physical_provenance_verified','flash_ready','writes_admitted'):
            self.assertFalse(r[n])

    def test_primary_secondary_and_later_blocks(self):
        for token,target in [('ota:kernel','rootfs'),('ota:kernel2','rootfs2'),('ota:backup','rootfs')]:
            self.fixture(token,later='ota:kernel2' if target=='rootfs' else 'ota:kernel')
            self.assertEqual(self.assess()['selected_rootfs'],target)

    def test_bad_first_ota_block_selects_next_good_block(self):
        self.fixture(bad=(1368,1369));self.assertEqual(self.assess()['selector_block'],1370)

    def test_unknown_truncated_substring_and_prefix_extensions_refused(self):
        for token in ('ota:','garbage','ota:kernel2extra','xota:kernel2','ota:backup\0','ota:kernel2\0'):
            self.fixture(token)
            with self.subTest(token=token),self.assertRaises(ValueError):self.assess()

    def test_erased_zero_and_bad_padding_refused(self):
        for value in (bytes([255])*2048,bytes(2048),b'ota:backup'.ljust(256,b'\0')+bytes(1792)):
            def alter(i,n,data,w,q):
                if n>=87552:data=value+data[2048:];w[12]=zlib.crc32(data)
                return data,w,q
            self.fixture(alter=alter)
            with self.assertRaises(ValueError):self.assess()

    def test_incomplete_extra_and_symlink_records_refused(self):
        self.fixture();raw=self.path.read_bytes()
        for b in (raw[:-1],raw+bytes(4476)):
            self.path.write_bytes(b)
            with self.assertRaises(ValueError):self.assess()
        other=self.root/'real.bin';other.write_bytes(raw);self.path.unlink();self.path.symlink_to(other)
        with self.assertRaises(ValueError):self.assess()

    def test_nonce_sequence_page_crc_ecc_otp_counters_and_identity_refused(self):
        for index,value in ((4,0),(8,55),(10,111),(12,0),(13,0x110b),(15,0x50),(16,0xf0),(16,0x90),(17,129),(18,1)):
            def alter(i,n,data,w,q):
                if i==1:w[index]=value
                return data,w,q
            self.fixture(alter=alter)
            with self.subTest(index=index,value=value),self.assertRaises(ValueError):self.assess()

    def test_bad_boot_or_unstable_marker_main_metadata_refused(self):
        for seq,column in ((1,2048),(2,0),(3,0),(4,0),(15,0)):
            def alter(i,n,data,w,q):
                if i==seq:
                    data=bytearray(data);data[column]^=1;data=bytes(data);w[12]=zlib.crc32(data)
                return data,w,q
            self.fixture(alter=alter)
            with self.subTest(seq=seq,column=column),self.assertRaises(ValueError):self.assess()

    def test_profile_drift_hash_size_and_root_argument_refused(self):
        self.fixture();original=copy.deepcopy(self.profile)
        for key,value in [('boot_sha256','0'*64),('boot_bytes',262144),('boot_policy_sha256','0'*64),('writes_admitted',True)]:
            self.profile=copy.deepcopy(original);self.profile[key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):self.assess()
        self.profile=copy.deepcopy(original);self.profile['primary']['root_device']='/dev/mtdblock_bbt_ro4'
        with self.assertRaises(ValueError):self.assess()

    def test_prefix_partition_collision_refused(self):
        # A bootloader prefix lookup could pick an earlier kernelXYZ record.
        self.image[11*2048+8:11*2048+8+32]=b'kernelXYZ'.ljust(32,b'\0')
        self.metadata=bytes(self.image[11*2048:12*2048]);self.profile['boot_sha256']=review.digest(self.image)
        self.fixture()
        with self.assertRaises(ValueError):self.assess()

    def test_future_profile_selection_and_review_binding(self):
        base=copy.deepcopy(self.base);base['version']='9.99';p=copy.deepcopy(self.profile)
        p.update(version='9.99',firmware_sha256=review.fingerprint(base))
        (self.root/'bootloaders').mkdir();f=self.root/'bootloaders/v9.99.json';f.write_text(json.dumps(p))
        self.assertEqual(review.load_bootloader(base,self.policy,self.root)['version'],'9.99')
        for key,value in [('format','unknown'),('firmware_sha256','0'*64),('selector_read_bytes',256),('accepted_tokens',['ota:backup','ota:backup'])]:
            bad=copy.deepcopy(p);bad[key]=value;f.write_text(json.dumps(bad))
            with self.subTest(key=key),self.assertRaises(ValueError):review.load_bootloader(base,self.policy,self.root)


if __name__=='__main__':unittest.main()
