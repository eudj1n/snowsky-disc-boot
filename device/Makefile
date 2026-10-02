CC ?= cc
OUT ?= ../build/host
CFLAGS ?= -Os -g -std=c11 -Wall -Wextra -Werror
CPPFLAGS += -Isrc -D_DEFAULT_SOURCE
.PHONY: all test
all: $(OUT)/disc-usb-console
$(OUT):
	mkdir -p $@
$(OUT)/disc-usb-console: src/usb_console.c | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) $< $(LDFLAGS) -o $@
$(OUT)/usb-console-fixture: src/usb_console.c | $(OUT)
	$(CC) $(CPPFLAGS) $(CFLAGS) -DDISC_USB_FIXTURE $< $(LDFLAGS) -o $@
$(OUT)/nand-reader-test: tests/nand_reader_test.c acquisition/nand_reader.c acquisition/nand_reader.h | $(OUT)
	$(CC) $(CFLAGS) -Iacquisition tests/nand_reader_test.c acquisition/nand_reader.c $(LDFLAGS) -o $@
$(OUT)/sfc-identity-test: tests/sfc_identity_test.c acquisition/sfc_x2000.c acquisition/sfc_x2000.h acquisition/nand_reader.c acquisition/nand_reader.h acquisition/identity.c acquisition/identity.h acquisition/batch.c acquisition/batch.h | $(OUT)
	$(CC) $(CFLAGS) -Iacquisition tests/sfc_identity_test.c acquisition/sfc_x2000.c acquisition/nand_reader.c acquisition/identity.c acquisition/batch.c $(LDFLAGS) -o $@
test: $(OUT)/usb-console-fixture $(OUT)/nand-reader-test $(OUT)/sfc-identity-test
	$(OUT)/nand-reader-test
	$(OUT)/sfc-identity-test
