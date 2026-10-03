CC ?= cc
OUT ?= ../build/host
CFLAGS ?= -Os -g -std=c11 -Wall -Wextra -Werror
CPPFLAGS += -Isrc -Ivendor/jsmn -D_DEFAULT_SOURCE
# The source commit (scripts/build.sh passes it), shown in the boot status.
DISC_BUILD ?= unknown
# A new build ID rebuilds disc-boot even when no source changed, so the status never shows a
# stale one (a commit of an already built change). Checked while the makefile is read.
ifneq ($(DISC_BUILD),$(shell cat $(OUT)/build-id 2>/dev/null))
$(shell mkdir -p $(OUT) && rm -f $(OUT)/disc-boot && printf '%s\n' '$(DISC_BUILD)' > $(OUT)/build-id)
endif
BOOT_SOURCES = src/boot.c src/boot_util.c src/manifest.c src/sha256.c
BOOT_HEADERS = src/boot_util.h src/manifest.h src/sha256.h vendor/jsmn/jsmn.h
# The boot menu (disc-menu), a package of its own built beside the boot program.
MENU_SOURCES = menu/menu.c menu/draw.c src/boot_util.c src/sha256.c
MENU_HEADERS = menu/draw.h menu/font.h src/boot_util.h src/sha256.h vendor/jsmn/jsmn.h
.PHONY: all test
all: $(OUT)/disc-usb-console $(OUT)/disc-boot $(OUT)/disc-menu
$(OUT):
	mkdir -p $@
$(OUT)/disc-usb-console: src/usb_console.c | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) $< $(LDFLAGS) -o $@
$(OUT)/disc-boot: $(BOOT_SOURCES) $(BOOT_HEADERS) | $(OUT)
	$(CC) $(CPPFLAGS) -DDISC_BUILD='"$(DISC_BUILD)"' $(CFLAGS) $(BOOT_SOURCES) $(LDFLAGS) -o $@
$(OUT)/disc-boot-fixture: $(BOOT_SOURCES) $(BOOT_HEADERS) | $(OUT)
	$(CC) $(CPPFLAGS) -DDISC_BUILD='"fixture"' -DDISC_BOOT_FIXTURE $(CFLAGS) $(BOOT_SOURCES) $(LDFLAGS) -o $@
$(OUT)/disc-menu: $(MENU_SOURCES) $(MENU_HEADERS) | $(OUT)
	$(CC) $(CPPFLAGS) -Imenu $(CFLAGS) $(MENU_SOURCES) $(LDFLAGS) -lm -o $@
$(OUT)/disc-menu-fixture: $(MENU_SOURCES) $(MENU_HEADERS) | $(OUT)
	$(CC) $(CPPFLAGS) -Imenu -DDISC_MENU_FIXTURE $(CFLAGS) $(MENU_SOURCES) $(LDFLAGS) -lm -o $@
$(OUT)/sha256-test: tests/sha256_test.c src/sha256.c src/sha256.h | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) tests/sha256_test.c src/sha256.c $(LDFLAGS) -o $@
$(OUT)/usb-console-fixture: src/usb_console.c | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) -DDISC_USB_FIXTURE $< $(LDFLAGS) -o $@
$(OUT)/nand-reader-test: tests/nand_reader_test.c acquisition/nand_reader.c acquisition/nand_reader.h | $(OUT)
	$(CC) $(CFLAGS) -Iacquisition tests/nand_reader_test.c acquisition/nand_reader.c $(LDFLAGS) -o $@
$(OUT)/sfc-identity-test: tests/sfc_identity_test.c acquisition/sfc_x2000.c acquisition/sfc_x2000.h acquisition/nand_reader.c acquisition/nand_reader.h acquisition/identity.c acquisition/identity.h acquisition/batch.c acquisition/batch.h | $(OUT)
	$(CC) $(CFLAGS) -Iacquisition tests/sfc_identity_test.c acquisition/sfc_x2000.c acquisition/nand_reader.c acquisition/identity.c acquisition/batch.c $(LDFLAGS) -o $@
test: $(OUT)/usb-console-fixture $(OUT)/disc-boot-fixture $(OUT)/disc-menu-fixture $(OUT)/sha256-test $(OUT)/nand-reader-test $(OUT)/sfc-identity-test
	$(OUT)/sha256-test
	$(OUT)/nand-reader-test
	$(OUT)/sfc-identity-test
