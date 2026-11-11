#include <assert.h>
#include <dlfcn.h>
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {
    assert(argc == 6);
    void *library = dlopen(argv[1], RTLD_NOW | RTLD_GLOBAL);
    if (library == NULL) {
        fprintf(stderr, "%s\n", dlerror());
        return 1;
    }
    int (*initialize)(const char *, const char *, const char *, const char *) =
        dlsym(library, "vespera_agent_initialize");
    char *(*request)(const char *) = dlsym(library, "vespera_agent_request");
    void (*release)(char *) = dlsym(library, "vespera_agent_free");
    assert(initialize && request && release);
    assert(initialize(argv[2], argv[3], argv[4], argv[5]) == 0);
    char *reply = request("{\"command\":\"list\"}");
    assert(reply && strstr(reply, "\"ok\": []"));
    release(reply);
    reply = request("{\"command\":\"create\"}");
    assert(reply && strstr(reply, "\"conversation_id\"") && strstr(reply, "\"work_path\": null"));
    puts(reply);
    release(reply);
    reply = request("{\"command\":\"list\"}");
    assert(reply && strstr(reply, "\"conversation_id\"") && strstr(reply, "\"updated_at\""));
    release(reply);
    reply = request("{\"command\":\"open\",\"conversation_id\":\"missing\"}");
    assert(reply && strstr(reply, "\"code\": \"not_found\""));
    release(reply);
    return 0;
}
