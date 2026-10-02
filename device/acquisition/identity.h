#ifndef DISC_IDENTITY_H
#define DISC_IDENTITY_H
#include "nand_reader.h"
#include "sfc_x2000.h"
/* Nonnull buffers must be distinct. The caller supplies a fresh zeroed SFC
   context with valid callbacks/configuration and uncached hardware output RAM. */
void identity_run(struct sfc_identity *, const struct nr_profile *,
                  const struct nr_request *, volatile struct nr_result *);
void page_run(struct sfc_identity *, const struct nr_profile *,
              const struct nr_request *, volatile struct nr_result *, uint32_t);
#endif
