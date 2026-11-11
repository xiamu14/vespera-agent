#include <Python.h>
#include <pthread.h>
#include <stdlib.h>
#include <string.h>

static pthread_mutex_t initialization = PTHREAD_MUTEX_INITIALIZER;
static PyObject *bridge;
static char *data_directory;

static int prepend_path(const char *path) {
    PyObject *value = PyUnicode_DecodeFSDefault(path);
    if (value == NULL) return -1;
    int result = PyList_Insert(PySys_GetObject("path"), 0, value);
    Py_DECREF(value);
    return result;
}

static int initialize(const char *home, const char *core, const char *source,
                      const char *data) {
    PyConfig config;
    PyConfig_InitIsolatedConfig(&config);
    config.write_bytecode = 0;
    config.install_signal_handlers = 0;
    PyStatus status = PyConfig_SetBytesString(&config, &config.home, home);
    if (!PyStatus_Exception(status)) status = Py_InitializeFromConfig(&config);
    PyConfig_Clear(&config);
    if (PyStatus_Exception(status)) return -1;

    if (prepend_path(source) == 0 && prepend_path(core) == 0) {
        PyObject *module = PyImport_ImportModule("vespera_agent.bridge");
        if (module != NULL) {
            PyObject *type = PyObject_GetAttrString(module, "Bridge");
            if (type != NULL) {
                bridge = PyObject_CallFunction(type, "ss", data, source);
                Py_DECREF(type);
            }
            Py_DECREF(module);
        }
    }
    if (bridge == NULL) PyErr_Print();
    PyEval_SaveThread();
    return bridge == NULL ? -1 : 0;
}

int vespera_agent_initialize(const char *home, const char *core, const char *source,
                             const char *data) {
    static int status = -1;
    static int attempted;
    pthread_mutex_lock(&initialization);
    if (!attempted) {
        attempted = 1;
        data_directory = strdup(data);
        if (data_directory != NULL) status = initialize(home, core, source, data);
    }
    int result = data_directory != NULL && strcmp(data_directory, data) == 0 ? status : -1;
    pthread_mutex_unlock(&initialization);
    return result;
}

char *vespera_agent_request(const char *payload) {
    if (bridge == NULL) return NULL;
    PyGILState_STATE state = PyGILState_Ensure();
    PyObject *reply = PyObject_CallMethod(bridge, "request", "s", payload);
    const char *utf8 = reply == NULL ? NULL : PyUnicode_AsUTF8(reply);
    char *result = utf8 == NULL ? NULL : strdup(utf8);
    if (PyErr_Occurred()) PyErr_Print();
    Py_XDECREF(reply);
    PyGILState_Release(state);
    return result;
}

void vespera_agent_free(char *reply) {
    free(reply);
}
