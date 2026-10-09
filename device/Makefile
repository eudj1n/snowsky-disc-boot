CC ?= cc
OUT ?= ../build/host
CFLAGS ?= -Os -g -std=c11 -Wall -Wextra -Werror
CPPFLAGS += -Icommon -Ivendor/jsmn -D_DEFAULT_SOURCE
# The source commit (scripts/build.sh passes it), shown in the boot status.
DISC_BUILD ?= unknown
# A new build ID rebuilds disc-boot even when no source changed, so the status never shows a
# stale one (a commit of an already built change). Checked while the makefile is read.
ifneq ($(DISC_BUILD),$(shell cat $(OUT)/build-id 2>/dev/null))
$(shell mkdir -p $(OUT) && rm -f $(OUT)/disc-boot && printf '%s\n' '$(DISC_BUILD)' > $(OUT)/build-id)
endif
BOOT_SOURCES = boot/boot.c common/boot_util.c common/manifest.c common/sha256.c
BOOT_HEADERS = common/boot_util.h common/manifest.h common/sha256.h vendor/jsmn/jsmn.h
# The boot menu (disc-menu), a package of its own built beside the boot program.
MENU_SOURCES = menu/menu.c menu/draw.c common/boot_util.c common/sha256.c
MENU_HEADERS = menu/draw.h menu/font.h common/boot_util.h common/sha256.h vendor/jsmn/jsmn.h
# The health journal (disc-health), a service of its own built beside the boot program.
HEALTH_SOURCES = health/health.c common/boot_util.c common/sha256.c
HEALTH_HEADERS = common/boot_util.h common/sha256.h vendor/jsmn/jsmn.h
.PHONY: all test
all: $(OUT)/disc-usb-console $(OUT)/disc-boot $(OUT)/disc-menu $(OUT)/disc-health
$(OUT):
	mkdir -p $@
$(OUT)/disc-usb-console: console/usb_console.c | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) $< $(LDFLAGS) -o $@
$(OUT)/disc-boot: $(BOOT_SOURCES) $(BOOT_HEADERS) | $(OUT)
	$(CC) $(CPPFLAGS) -DDISC_BUILD='"$(DISC_BUILD)"' $(CFLAGS) $(BOOT_SOURCES) $(LDFLAGS) -o $@
$(OUT)/disc-boot-fixture: $(BOOT_SOURCES) $(BOOT_HEADERS) | $(OUT)
	$(CC) $(CPPFLAGS) -DDISC_BUILD='"fixture"' -DDISC_BOOT_FIXTURE $(CFLAGS) $(BOOT_SOURCES) $(LDFLAGS) -o $@
$(OUT)/disc-menu: $(MENU_SOURCES) $(MENU_HEADERS) | $(OUT)
	$(CC) $(CPPFLAGS) -Imenu $(CFLAGS) $(MENU_SOURCES) $(LDFLAGS) -lm -o $@
$(OUT)/disc-menu-fixture: $(MENU_SOURCES) $(MENU_HEADERS) | $(OUT)
	$(CC) $(CPPFLAGS) -Imenu -DDISC_MENU_FIXTURE $(CFLAGS) $(MENU_SOURCES) $(LDFLAGS) -lm -o $@
$(OUT)/disc-health: $(HEALTH_SOURCES) $(HEALTH_HEADERS) | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(HEALTH_SOURCES) $(LDFLAGS) -o $@
$(OUT)/disc-health-fixture: $(HEALTH_SOURCES) $(HEALTH_HEADERS) | $(OUT)
	$(CC) $(CPPFLAGS) -DDISC_HEALTH_FIXTURE $(CFLAGS) $(HEALTH_SOURCES) $(LDFLAGS) -o $@
$(OUT)/sha256-test: tests/sha256_test.c common/sha256.c common/sha256.h | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) tests/sha256_test.c common/sha256.c $(LDFLAGS) -o $@
$(OUT)/usb-console-fixture: console/usb_console.c | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) -DDISC_USB_FIXTURE $< $(LDFLAGS) -o $@
$(OUT)/nand-reader-test: tests/nand_reader_test.c usbboot/nand_reader.c usbboot/nand_reader.h | $(OUT)
	$(CC) $(CFLAGS) -Iusbboot tests/nand_reader_test.c usbboot/nand_reader.c $(LDFLAGS) -o $@
$(OUT)/sfc-identity-test: tests/sfc_identity_test.c usbboot/sfc_x2000.c usbboot/sfc_x2000.h usbboot/nand_reader.c usbboot/nand_reader.h usbboot/identity.c usbboot/identity.h usbboot/batch.c usbboot/batch.h usbboot/digest.c usbboot/digest.h common/sha256.c common/sha256.h | $(OUT)
	$(CC) $(CFLAGS) -Iusbboot -Icommon tests/sfc_identity_test.c usbboot/sfc_x2000.c usbboot/nand_reader.c usbboot/identity.c usbboot/batch.c usbboot/digest.c common/sha256.c $(LDFLAGS) -o $@
$(OUT)/restart-test: tests/restart_test.c usbboot/restart.c usbboot/restart.h | $(OUT)
	$(CC) $(CFLAGS) -Iusbboot tests/restart_test.c usbboot/restart.c $(LDFLAGS) -o $@
test: $(OUT)/usb-console-fixture $(OUT)/disc-boot-fixture $(OUT)/disc-menu-fixture $(OUT)/disc-health-fixture $(OUT)/sha256-test $(OUT)/nand-reader-test $(OUT)/sfc-identity-test $(OUT)/restart-test
	$(OUT)/sha256-test
	$(OUT)/nand-reader-test
	$(OUT)/sfc-identity-test
	$(OUT)/restart-test
