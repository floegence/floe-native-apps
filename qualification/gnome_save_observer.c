/* Qualification only: observe GNOME's public save-action readiness unchanged. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

void gtk_widget_action_set_enabled(void *widget, const char *name, int enabled) {
  static void (*original)(void *, const char *, int);
  static unsigned disabled;
  if (!original) original = dlsym(RTLD_NEXT, "gtk_widget_action_set_enabled");
  if (!original) abort();
  original(widget, name, enabled);
  const char *path = getenv("FLOE_GNOME_SAVE_RECEIPT");
  if (!path || strcmp(name, "page.save")) return;
  if (!enabled) disabled++;
  char *temporary = NULL;
  if (asprintf(&temporary, "%s.pending", path) < 0) abort();
  FILE *file = fopen(temporary, "w");
  if (!file) abort();
  fprintf(file, "{\"enabled\":%s,\"disabled\":%u}\n", enabled ? "true" : "false", disabled);
  if (fclose(file) || rename(temporary, path)) abort();
  free(temporary);
}
