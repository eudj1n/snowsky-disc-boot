"""Stock BusyBox + packaged reporter with synthetic mounts in private namespaces."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time

def wait_for_report(target, deadline, max_bytes):
    # Noclobber publication creates the path before the final copy completes.
    while time.monotonic()<deadline:
        try:
            with target.open('rb') as source:data=source.read(max_bytes+1)
        except FileNotFoundError:
            data=b''
        assert len(data)<=max_bytes, 'Packaged report exceeds profile limit'
        if data.endswith(b'END_DISC_WEB_BOOT_REPORT_V1\n'):return data
        time.sleep(.1)
    raise AssertionError('Packaged reporter did not finish export')


def run(output):
    from selected_firmware import PROFILE, profiles
    assert os.environ.get('CI_DISPOSABLE')=='1' and os.getpid()==1
    root=output/'verified-tree'; report=json.loads((output/'report.json').read_text())
    # The boot layer's image always carries the console and the report (no package inside).
    assert report['variant']=='boot' and report['packages']==[] and report['fullRoundTrip']
    usb=profiles.load_usb_profile(PROFILE)
    assert report['usbDiagnostic']['profileSha256']==profiles.fingerprint(usb)
    subprocess.run(['mount','--make-rprivate','/'],check=True)
    for name in ('run','proc','sys','tmp'):
        subprocess.run(['mount','-t','tmpfs','tmpfs',str(root/name)],check=True)
    if not (root/'dev/null').exists():os.mknod(root/'dev/null',0o20666,os.makedev(1,3))
    # Synthetic mount observations, never an exposed physical controller/card.
    (root/'tmp/sdcard').mkdir();(root/'proc/net').mkdir()
    (root/'proc/mounts').write_text('/dev/mmcblk0p1 /tmp/sdcard exfat rw 0 0\n/dev/root / squashfs ro 0 0\n')
    (root/'proc/net/tcp').write_text('0: 0100007F:1EBE 00000000:0000 0A\n')
    (root/'proc/sys/kernel/random').mkdir(parents=True)
    (root/'proc/sys/kernel/random/boot_id').write_text('synthetic-packed-boot-id\n')
    controller=root/'sys/class/udc'/usb['udc'];controller.mkdir(parents=True)
    (controller/'state').write_text('not attached\n')
    assert '--udc '+usb['udc']+' ' in (root/'etc/init.d/S99disc-usb').read_text()
    (root/'tmp/sdcard/.disc/dev').mkdir(parents=True,exist_ok=True)
    (root/'tmp/sdcard/.disc/dev/boot-report').write_bytes(b'DISC_WEB_LOCAL_BOOT_REPORT\n')
    # Deliberately make the native USB executable unavailable in this namespace.
    # An empty bind-mounted file leaves the underlying packed file unchanged.
    dummy=root/'run/native-unavailable';dummy.touch(mode=0o600)
    subprocess.run(['mount','--bind',str(dummy),str(root/'opt/disc-boot/disc-usb-console')],check=True)
    command=['chroot',str(root),'/bin/sh','/etc/init.d/S99disc-usb','start']
    started=time.monotonic();subprocess.run(command,check=True,timeout=5)
    target=root/'tmp/sdcard/.disc/dev/boot-report.txt'
    deadline=started+report['usbDiagnostic']['bootReport']['wait_seconds']+10
    data=wait_for_report(target,deadline,report['usbDiagnostic']['bootReport']['max_bytes'])
    assert b'usb helper exit=126' in data,data
    assert b'0100007F:1EBE 0A' in data
    assert b'synthetic-packed-boot-id' in data
    assert ('usb_profile_sha256='+report['usbDiagnostic']['profileSha256']).encode() in data
    assert ('[usb_controllers]\n'+usb['udc']+'\n').encode() in data
    assert b'[expected_controller_state]\nnot attached\n' in data
    assert data.endswith(b'END_DISC_WEB_BOOT_REPORT_V1\n')
    assert len(data)<=report['usbDiagnostic']['bootReport']['max_bytes']
    assert not (root/'tmp/sdcard/.disc/dev/usb-console').exists()
    subprocess.run(command,check=True,timeout=5);time.sleep(.3)
    assert target.read_bytes()==data,'Duplicate hook changed saved evidence'
    (output/'boot-report-example.txt').write_bytes(data)
    result=dict(status='passed',stockBusyBox=True,packagedScript=True,nativeExecFailureRecorded=True,
                separateOptIn=True,noUsbMarker=True,duplicatePreserved=True,bytes=len(data),
                configuredController=usb['udc'],controllerStateObserved=True,
                elapsedSeconds=round(time.monotonic()-started,2),
                scope='Synthetic proc/card/controller paths in private namespaces; not physical USB or SD acceptance')
    (output/'boot-report-test.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();assert args.output.resolve().is_relative_to('/work');run(args.output)
