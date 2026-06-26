// ============================================================================
// gpb_binary_recorder.c
// GP Bikes / PiBoSo plugin: records GP Bikes plugin callbacks to MXBMRP3-style
// .mxbrec binary files, then optionally auto-converts to CSV after RunDeinit.
//
// Build from x64 Native Tools Command Prompt for VS 2022:
//   cl /LD /O2 gpb_binary_recorder.c /Fe:gpb_binary_recorder.dll
//   copy /Y gpb_binary_recorder.dll gpb_binary_recorder.dlo
//
// Put in:
//   ...\Steam\steamapps\common\GP Bikes\plugins\gpb_binary_recorder.dlo
//   ...\Steam\steamapps\common\GP Bikes\plugins\gpb_binary_recorder.ini
//
// Recommended INI:
// [params]
// disable=0
// sample_rate=100
// output_dir=C:\Users\g04590\Documents\PiBoSo\GP Bikes\LOGGER
// flush_each_event=0
// auto_convert=1
// converter_python=py
// converter_script=C:\Users\g04590\projects\GPBikesPluginLogger\mxbrec_gpb_to_piboso_csv_lap_scaled_v2.py
// ============================================================================

#define _CRT_SECURE_NO_WARNINGS
#include <windows.h>
#include <direct.h>
#include <stdio.h>
#include <stdint.h>
#include <string.h>

// ----------------------------- GP Bikes structs -----------------------------
typedef struct
{
    char m_szRiderName[100];
    char m_szBikeID[100];
    char m_szBikeName[100];
    int m_iNumberOfGears;
    int m_iMaxRPM;
    int m_iLimiter;
    int m_iShiftRPM;
    float m_fEngineOptTemperature;
    float m_afEngineTemperatureAlarm[2];
    float m_fMaxFuel;
    float m_afSuspMaxTravel[2];
    float m_fSteerLock;
    char m_szCategory[100];
    char m_szTrackID[100];
    char m_szTrackName[100];
    float m_fTrackLength;
    int m_iType;
} SPluginsBikeEvent_t;

typedef struct
{
    int m_iSession;
    int m_iConditions;
    float m_fAirTemperature;
    float m_fTrackTemperature;
    char m_szSetupFileName[100];
} SPluginsBikeSession_t;

typedef struct
{
    int m_iRPM;
    float m_fEngineTemperature;
    float m_fWaterTemperature;
    int m_iGear;
    float m_fFuel;
    float m_fSpeedometer;
    float m_fPosX, m_fPosY, m_fPosZ;
    float m_fVelocityX, m_fVelocityY, m_fVelocityZ;
    float m_fAccelerationX, m_fAccelerationY, m_fAccelerationZ;
    float m_aafRot[3][3];
    float m_fYaw, m_fPitch, m_fRoll;
    float m_fYawVelocity, m_fPitchVelocity, m_fRollVelocity;
    float m_fPitchRel, m_fRollRel;
    float m_afSuspLength[2];
    float m_afSuspVelocity[2];
    int m_iCrashed;
    float m_fSteer;
    float m_fInputThrottle;
    float m_fThrottle;
    float m_fFrontBrake;
    float m_fRearBrake;
    float m_fClutch;
    float m_afWheelSpeed[2];
    int m_aiWheelMaterial[2];
    float m_aafTreadTemperature[2][3];
    float m_afBrakePressure[2];
    float m_fSteerTorque;
    int m_iPitLimiter;
    int m_iECUMode;
    char m_szEngineMapping[3];
    int m_iTractionControl;
    int m_iEngineBraking;
    int m_iAntiWheeling;
    int m_iECUState;
    float m_fRiderLRLean;
} SPluginsBikeData_t;

typedef struct
{
    int m_iLapNum;
    int m_iInvalid;
    int m_iLapTime;
    int m_iBest;
} SPluginsBikeLap_t;

typedef struct
{
    int m_iSplit;
    int m_iSplitTime;
    int m_iBestDiff;
} SPluginsBikeSplit_t;

// ------------------------ MXBMRP3-style recording format ---------------------
typedef struct
{
    char magic[8];          // "MXBHREC\0"
    uint32_t version;       // This writer uses 1
    uint32_t numEvents;
    uint64_t startTimeUs;
    uint64_t endTimeUs;
    uint32_t flags;
    char reserved[32];
} RecordingHeader;

typedef struct
{
    uint32_t eventType;
    uint32_t dataSize;
    uint64_t timestampUs;   // Microseconds since this recording file started
} EventHeader;

typedef enum EventType
{
    EventType_None = 0,
    EventType_Startup = 1,
    EventType_Shutdown = 2,
    EventType_EventInit = 3,
    EventType_EventDeinit = 4,
    EventType_RunInit = 5,
    EventType_RunDeinit = 6,
    EventType_RunStart = 7,
    EventType_RunStop = 8,
    EventType_RunLap = 9,
    EventType_RunSplit = 10,
    EventType_RunTelemetry = 11,
    EventType_DrawInit = 12,
    EventType_Draw = 13,
    EventType_TrackCenterline = 14,
    EventType_RaceEvent = 15,
    EventType_RaceDeinit = 16,
    EventType_RaceSession = 17,
    EventType_RaceSessionState = 18,
    EventType_RaceAddEntry = 19,
    EventType_RaceRemoveEntry = 20,
    EventType_RaceLap = 21,
    EventType_RaceSplit = 22,
    EventType_RaceHoleshot = 23,
    EventType_RaceClassification = 24,
    EventType_RaceTrackPosition = 25,
    EventType_RaceCommunication = 26,
    EventType_RaceVehicleData = 27
} EventType;

// ------------------------------- globals ------------------------------------
static FILE* g_file = NULL;
static char g_filePath[MAX_PATH] = "";
static char g_savePath[MAX_PATH] = "";
static uint32_t g_numEvents = 0;
static LARGE_INTEGER g_qpcFreq;
static LARGE_INTEGER g_qpcStart;
static uint64_t g_startEpochUs = 0;

static int g_flushEachEvent = 0;
static int g_autoConvert = 0;
static char g_converterPython[MAX_PATH] = "py";
static char g_converterScript[MAX_PATH] = "";

static int g_hasCachedEvent = 0;
static SPluginsBikeEvent_t g_cachedEvent;

// Set to 1 if you want simple debug breadcrumbs in output_dir.
static int g_debugLog = 0;
static char g_outputDir[MAX_PATH] = "";

// ------------------------------- helpers ------------------------------------
static uint64_t unix_time_us(void)
{
    FILETIME ft;
    ULARGE_INTEGER uli;
    GetSystemTimeAsFileTime(&ft);
    uli.LowPart = ft.dwLowDateTime;
    uli.HighPart = ft.dwHighDateTime;
    return (uli.QuadPart - 116444736000000000ULL) / 10ULL;
}

static uint64_t elapsed_us(void)
{
    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);
    uint64_t ticks = (uint64_t)(now.QuadPart - g_qpcStart.QuadPart);
    return (ticks / (uint64_t)g_qpcFreq.QuadPart) * 1000000ULL +
           ((ticks % (uint64_t)g_qpcFreq.QuadPart) * 1000000ULL) / (uint64_t)g_qpcFreq.QuadPart;
}

static void debug_log(const char* msg)
{
    if (!g_debugLog) return;

    char path[MAX_PATH];
    const char* dir = g_outputDir[0] ? g_outputDir : ".";
    snprintf(path, sizeof(path), "%s\\gpb_binary_recorder_debug.txt", dir);

    FILE* f = fopen(path, "a");
    if (f) {
        fprintf(f, "%s\n", msg ? msg : "(null)");
        fclose(f);
    }
}

static void ensure_dir(const char* path)
{
    if (path && path[0]) {
        _mkdir(path); // One-level mkdir. Create parent dirs manually if needed.
    }
}

static void make_recording_path(char* out, size_t outSize, const char* savePath)
{
    char timestamp[64];
    SYSTEMTIME st;

    if (g_outputDir[0] == '\0') {
        char cwd[MAX_PATH];
        _getcwd(cwd, MAX_PATH);

        if (savePath && savePath[0]) {
            snprintf(g_outputDir, sizeof(g_outputDir), "%s\\mxbmrp3_recordings", savePath);
        } else {
            snprintf(g_outputDir, sizeof(g_outputDir), "%s\\mxbmrp3_recordings", cwd);
        }
    }

    ensure_dir(g_outputDir);

    GetLocalTime(&st);
    snprintf(timestamp, sizeof(timestamp), "%04u%02u%02u_%02u%02u%02u",
             st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond);

    snprintf(out, outSize, "%s\\gpb_%s.mxbrec", g_outputDir, timestamp);
}

static int write_event(uint32_t eventType, const void* data, uint32_t dataSize)
{
    EventHeader eh;
    if (!g_file) return 0;

    eh.eventType = eventType;
    eh.dataSize = dataSize;
    eh.timestampUs = elapsed_us();

    if (fwrite(&eh, sizeof(eh), 1, g_file) != 1) return 0;
    if (dataSize > 0 && data) {
        if (fwrite(data, dataSize, 1, g_file) != 1) return 0;
    }

    ++g_numEvents;
    if (g_flushEachEvent) fflush(g_file);
    return 1;
}

static int open_recording(const char* savePath)
{
    RecordingHeader hdr;

    QueryPerformanceFrequency(&g_qpcFreq);
    QueryPerformanceCounter(&g_qpcStart);
    g_startEpochUs = unix_time_us();
    g_numEvents = 0;

    make_recording_path(g_filePath, sizeof(g_filePath), savePath);

    g_file = fopen(g_filePath, "wb+");
    if (!g_file) {
        debug_log("ERROR: fopen failed for recording file");
        debug_log(g_filePath);
        return 0;
    }

    memset(&hdr, 0, sizeof(hdr));
    memcpy(hdr.magic, "MXBHREC\0", 8);
    hdr.version = 1;
    hdr.numEvents = 0;
    hdr.startTimeUs = g_startEpochUs;
    hdr.endTimeUs = g_startEpochUs;
    hdr.flags = 0x47504252; // 'GPBR'

    if (fwrite(&hdr, sizeof(hdr), 1, g_file) != 1) {
        fclose(g_file);
        g_file = NULL;
        debug_log("ERROR: failed to write recording header");
        return 0;
    }

    fflush(g_file);
    debug_log("Recording opened:");
    debug_log(g_filePath);
    return 1;
}

static void launch_converter_async(const char* recordingPath)
{
    if (!g_autoConvert) return;
    if (!recordingPath || !recordingPath[0]) return;
    if (!g_converterScript[0]) return;

    char cmdLine[MAX_PATH * 4];
    STARTUPINFOA si;
    PROCESS_INFORMATION pi;

    snprintf(cmdLine, sizeof(cmdLine),
             "\"%s\" \"%s\" \"%s\"",
             g_converterPython,
             g_converterScript,
             recordingPath);

    memset(&si, 0, sizeof(si));
    memset(&pi, 0, sizeof(pi));
    si.cb = sizeof(si);

    debug_log("Launching converter:");
    debug_log(cmdLine);

    BOOL ok = CreateProcessA(
        NULL,
        cmdLine,
        NULL,
        NULL,
        FALSE,
        CREATE_NO_WINDOW,
        NULL,
        NULL,
        &si,
        &pi
    );

    if (ok) {
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
    } else {
        debug_log("ERROR: CreateProcessA failed launching converter");
    }
}

static void finalize_recording_and_maybe_convert(int doConvert)
{
    RecordingHeader hdr;
    char closedPath[MAX_PATH];

    if (!g_file) return;

    strncpy(closedPath, g_filePath, sizeof(closedPath) - 1);
    closedPath[sizeof(closedPath) - 1] = '\0';

    memset(&hdr, 0, sizeof(hdr));
    memcpy(hdr.magic, "MXBHREC\0", 8);
    hdr.version = 1;
    hdr.numEvents = g_numEvents;
    hdr.startTimeUs = g_startEpochUs;
    hdr.endTimeUs = g_startEpochUs + elapsed_us();
    hdr.flags = 0x47504252; // 'GPBR'

    fseek(g_file, 0, SEEK_SET);
    fwrite(&hdr, sizeof(hdr), 1, g_file);
    fflush(g_file);
    fclose(g_file);
    g_file = NULL;

    debug_log("Recording closed:");
    debug_log(closedPath);

    if (doConvert) {
        launch_converter_async(closedPath);
    }
}

static void read_ini_settings(const char* iniPath)
{
    g_flushEachEvent = GetPrivateProfileIntA("params", "flush_each_event", 0, iniPath);
    g_autoConvert = GetPrivateProfileIntA("params", "auto_convert", 0, iniPath);
    g_debugLog = GetPrivateProfileIntA("params", "debug_log", 0, iniPath);

    GetPrivateProfileStringA("params", "output_dir", "", g_outputDir, sizeof(g_outputDir), iniPath);
    GetPrivateProfileStringA("params", "converter_python", "py", g_converterPython, sizeof(g_converterPython), iniPath);
    GetPrivateProfileStringA("params", "converter_script", "", g_converterScript, sizeof(g_converterScript), iniPath);
}

static void find_ini_path(char* out, size_t outSize)
{
    char cwd[MAX_PATH];
    _getcwd(cwd, MAX_PATH);
    snprintf(out, outSize, "%s\\gpb_binary_recorder.ini", cwd);
}

// ---------------------------- PiBoSo exports --------------------------------
__declspec(dllexport) char* GetModID(void)
{
    return "gpbikes";
}

__declspec(dllexport) int GetModDataVersion(void)
{
    return 12;
}

__declspec(dllexport) int GetInterfaceVersion(void)
{
    return 9;
}

__declspec(dllexport) int Startup(char* _szSavePath)
{
    char iniPath[MAX_PATH];
    int disable;
    int sampleRate;
    int ret;

    find_ini_path(iniPath, sizeof(iniPath));
    read_ini_settings(iniPath);

    disable = GetPrivateProfileIntA("params", "disable", 0, iniPath);
    if (disable) return -1;

    sampleRate = GetPrivateProfileIntA("params", "sample_rate", 100, iniPath);
    switch (sampleRate) {
        case 50: ret = 1; break;
        case 20: ret = 2; break;
        case 10: ret = 3; break;
        case 100:
        default: ret = 0; break;
    }

    if (_szSavePath && _szSavePath[0]) {
        strncpy(g_savePath, _szSavePath, sizeof(g_savePath) - 1);
        g_savePath[sizeof(g_savePath) - 1] = '\0';
    } else {
        g_savePath[0] = '\0';
    }

    debug_log("Startup called");
    debug_log("INI path:");
    debug_log(iniPath);

    if (!open_recording(g_savePath)) {
        return -1;
    }

    write_event(EventType_Startup, NULL, 0);
    return ret;
}

__declspec(dllexport) void Shutdown(void)
{
    if (g_file) {
        write_event(EventType_Shutdown, NULL, 0);
        finalize_recording_and_maybe_convert(1);
    }
}

__declspec(dllexport) void EventInit(void* _pData, int _iDataSize)
{
    if (_pData && _iDataSize > 0) {
        if (_iDataSize >= (int)sizeof(SPluginsBikeEvent_t)) {
            memcpy(&g_cachedEvent, _pData, sizeof(SPluginsBikeEvent_t));
            g_hasCachedEvent = 1;
        }
        write_event(EventType_EventInit, _pData, (uint32_t)_iDataSize);
    }
}

__declspec(dllexport) void EventDeinit(void)
{
    write_event(EventType_EventDeinit, NULL, 0);

    // Fallback in case RunDeinit is not called when leaving the event.
    finalize_recording_and_maybe_convert(1);
}

__declspec(dllexport) void RunInit(void* _pData, int _iDataSize)
{
    // If RunDeinit closed the previous file, start a new one for this run.
    if (!g_file) {
        if (!open_recording(g_savePath)) {
            return;
        }

        write_event(EventType_Startup, NULL, 0);

        // Preserve track/bike metadata in each new run file.
        if (g_hasCachedEvent) {
            write_event(EventType_EventInit, &g_cachedEvent, (uint32_t)sizeof(SPluginsBikeEvent_t));
        }
    }

    if (_pData && _iDataSize > 0) {
        write_event(EventType_RunInit, _pData, (uint32_t)_iDataSize);
    }
}

__declspec(dllexport) void RunDeinit(void)
{
    write_event(EventType_RunDeinit, NULL, 0);

    // Main auto-convert trigger: leaving the on-track run/session.
    finalize_recording_and_maybe_convert(1);
}

__declspec(dllexport) void RunStart(void)
{
    write_event(EventType_RunStart, NULL, 0);
}

__declspec(dllexport) void RunStop(void)
{
    // Do not finalize here: RunStop may occur on pause/stop without leaving the session.
    write_event(EventType_RunStop, NULL, 0);
}

__declspec(dllexport) void RunLap(void* _pData, int _iDataSize)
{
    if (_pData && _iDataSize > 0) {
        write_event(EventType_RunLap, _pData, (uint32_t)_iDataSize);
    }
}

__declspec(dllexport) void RunSplit(void* _pData, int _iDataSize)
{
    if (_pData && _iDataSize > 0) {
        write_event(EventType_RunSplit, _pData, (uint32_t)_iDataSize);
    }
}

__declspec(dllexport) void RunTelemetry(void* _pData, int _iDataSize, float _fTime, float _fPos)
{
    // Matches mxbmrp3 replay_tool expectation:
    // [bikeData bytes][float time][float pos]
    if (_pData && _iDataSize > 0) {
        uint32_t payloadSize = (uint32_t)_iDataSize + 2U * sizeof(float);
        unsigned char stackBuf[1024];
        unsigned char* payload = stackBuf;

        if (payloadSize > sizeof(stackBuf)) {
            payload = (unsigned char*)HeapAlloc(GetProcessHeap(), 0, payloadSize);
            if (!payload) return;
        }

        memcpy(payload, _pData, (size_t)_iDataSize);
        memcpy(payload + _iDataSize, &_fTime, sizeof(float));
        memcpy(payload + _iDataSize + sizeof(float), &_fPos, sizeof(float));

        write_event(EventType_RunTelemetry, payload, payloadSize);

        if (payload != stackBuf) {
            HeapFree(GetProcessHeap(), 0, payload);
        }
    }
}
