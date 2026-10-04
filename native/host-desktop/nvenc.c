/* Private synchronous NVENC worker. No display, input, filesystem or network
 * authority. Its parent owns one request at a time and kills stalled workers.
 * Wire: FDV1 + width,height,fps,bitrate (all uint32 big endian); then repeated
 * [uint32 byte length, uint32 sequence, packed BGRA]. Reply is [length,sequence,
 * complete Annex-B access unit]. EOF ends the session; invalid input fails. */
#include <arpa/inet.h>
#include <dlfcn.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "vendor/dynlink_cuda.h"
#include "vendor/nvEncodeAPI.h"

#define LIMIT (64u << 20)
static int read_all(void *data, size_t size) {
    uint8_t *p = data;
    while (size) {
        ssize_t n = read(STDIN_FILENO, p, size);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return -1;
        p += n; size -= (size_t)n;
    }
    return 0;
}
static int write_all(const void *data, size_t size) {
    const uint8_t *p = data;
    while (size) {
        ssize_t n = write(STDOUT_FILENO, p, size);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return -1;
        p += n; size -= (size_t)n;
    }
    return 0;
}
#define CHECK(call) do { int status = (call); if (status) { \
    fprintf(stderr, "NVENC %s: %d\n", #call, status); goto cleanup; } } while (0)
#define LOAD(target, library, symbol) do { *(void **)(&(target)) = dlsym(library, symbol); \
    if (!(target)) goto cleanup; } while (0)

int main(int argc, char **argv) {
    if (argc == 2 && strcmp(argv[1], "--check") == 0) return 0;
    if (argc != 1) return 2;
    uint32_t header[5];
    if (read_all(header, sizeof(header)) || ntohl(header[0]) != 0x46445631) return 2;
    uint32_t width = ntohl(header[1]), height = ntohl(header[2]);
    uint32_t fps = ntohl(header[3]), bitrate = ntohl(header[4]);
    if (width < 2 || height < 2 || width > 8192 || height > 8192 ||
        width % 2 || height % 2 || (uint64_t)width * height > LIMIT / 4 ||
        (fps != 15 && fps != 30 && fps != 60) || bitrate < 1000000 || bitrate > 100000000) return 2;
    int result = 1;
    void *cuda = NULL, *library = NULL, *encoder = NULL;
    CUcontext context = NULL;
    tcuInit *init = NULL; tcuDeviceGet *device_get = NULL;
    tcuCtxCreate_v2 *context_create = NULL; tcuCtxDestroy_v2 *context_destroy = NULL;
    NV_ENCODE_API_FUNCTION_LIST api = {0};
    NV_ENC_INPUT_PTR input[4] = {0}; NV_ENC_OUTPUT_PTR output[4] = {0};
    cuda = dlopen("libcuda.so.1", RTLD_NOW | RTLD_LOCAL);
    library = dlopen("libnvidia-encode.so.1", RTLD_NOW | RTLD_LOCAL);
    if (!cuda || !library) goto cleanup;
    LOAD(init, cuda, "cuInit"); LOAD(device_get, cuda, "cuDeviceGet");
    LOAD(context_create, cuda, "cuCtxCreate_v2"); LOAD(context_destroy, cuda, "cuCtxDestroy_v2");
    NVENCSTATUS (NVENCAPI *create_api)(NV_ENCODE_API_FUNCTION_LIST *) = NULL;
    NVENCSTATUS (NVENCAPI *max_version)(uint32_t *) = NULL;
    LOAD(create_api, library, "NvEncodeAPICreateInstance");
    LOAD(max_version, library, "NvEncodeAPIGetMaxSupportedVersion");
    uint32_t supported = 0;
    CHECK(max_version(&supported));
    if (supported < ((NVENCAPI_MAJOR_VERSION << 4) | NVENCAPI_MINOR_VERSION)) goto cleanup;
    api.version = NV_ENCODE_API_FUNCTION_LIST_VER;
    CHECK(create_api(&api));
    CHECK(init(0));
    CUdevice device;
    CHECK(device_get(&device, 0));
    CHECK(context_create(&context, 0, device));
    NV_ENC_OPEN_ENCODE_SESSION_EX_PARAMS open = {0};
    open.version = NV_ENC_OPEN_ENCODE_SESSION_EX_PARAMS_VER;
    open.deviceType = NV_ENC_DEVICE_TYPE_CUDA; open.device = context;
    open.apiVersion = NVENCAPI_VERSION;
    CHECK(api.nvEncOpenEncodeSessionEx(&open, &encoder));
    NV_ENC_PRESET_CONFIG preset = {0};
    preset.version = NV_ENC_PRESET_CONFIG_VER; preset.presetCfg.version = NV_ENC_CONFIG_VER;
    CHECK(api.nvEncGetEncodePresetConfigEx(encoder, NV_ENC_CODEC_H264_GUID,
        NV_ENC_PRESET_P1_GUID, NV_ENC_TUNING_INFO_ULTRA_LOW_LATENCY, &preset));
    NV_ENC_CONFIG config = preset.presetCfg;
    config.profileGUID = NV_ENC_H264_PROFILE_HIGH_GUID;
    config.gopLength = 120; config.frameIntervalP = 1;
    config.rcParams.rateControlMode = NV_ENC_PARAMS_RC_CBR;
    config.rcParams.averageBitRate = bitrate; config.rcParams.maxBitRate = bitrate;
    config.rcParams.vbvBufferSize = bitrate / fps;
    config.rcParams.vbvInitialDelay = bitrate / fps;
    config.rcParams.enableLookahead = 0; config.rcParams.lookaheadDepth = 0;
    config.rcParams.zeroReorderDelay = 1; config.rcParams.multiPass = NV_ENC_MULTI_PASS_DISABLED;
    config.encodeCodecConfig.h264Config.idrPeriod = 120;
    config.encodeCodecConfig.h264Config.repeatSPSPPS = 1;
    config.encodeCodecConfig.h264Config.chromaFormatIDC = 1;
    NV_ENC_CONFIG_H264_VUI_PARAMETERS *vui = &config.encodeCodecConfig.h264Config.h264VUIParameters;
    vui->colourDescriptionPresentFlag = 1; vui->videoSignalTypePresentFlag = 1;
    vui->colourPrimaries = 1; vui->transferCharacteristics = 1; vui->colourMatrix = 1;
    NV_ENC_INITIALIZE_PARAMS params = {0};
    params.version = NV_ENC_INITIALIZE_PARAMS_VER;
    params.encodeGUID = NV_ENC_CODEC_H264_GUID; params.presetGUID = NV_ENC_PRESET_P1_GUID;
    params.encodeWidth = params.darWidth = width; params.encodeHeight = params.darHeight = height;
    params.frameRateNum = fps; params.frameRateDen = 1;
    params.enablePTD = 1; params.enableEncodeAsync = 0;
    params.tuningInfo = NV_ENC_TUNING_INFO_ULTRA_LOW_LATENCY; params.encodeConfig = &config;
    CHECK(api.nvEncInitializeEncoder(encoder, &params));
    for (unsigned i = 0; i < 4; i++) {
        NV_ENC_CREATE_INPUT_BUFFER in = {0}; in.version = NV_ENC_CREATE_INPUT_BUFFER_VER;
        in.width = width; in.height = height; in.bufferFmt = NV_ENC_BUFFER_FORMAT_ARGB;
        CHECK(api.nvEncCreateInputBuffer(encoder, &in)); input[i] = in.inputBuffer;
        NV_ENC_CREATE_BITSTREAM_BUFFER out = {0}; out.version = NV_ENC_CREATE_BITSTREAM_BUFFER_VER;
        CHECK(api.nvEncCreateBitstreamBuffer(encoder, &out)); output[i] = out.bitstreamBuffer;
    }
    uint32_t previous = 0;
    for (;;) {
        uint32_t request[2];
        ssize_t count;
        do { count = read(STDIN_FILENO, request, 1); } while (count < 0 && errno == EINTR);
        if (count == 0) { result = 0; break; }
        if (count != 1 || read_all((uint8_t *)request + 1, sizeof(request) - 1)) break;
        uint32_t length = ntohl(request[0]), sequence = ntohl(request[1]);
        if (length != width * height * 4 || sequence == 0 || sequence <= previous) break;
        unsigned slot = (sequence - 1) % 4;
        NV_ENC_LOCK_INPUT_BUFFER locked = {0}; locked.version = NV_ENC_LOCK_INPUT_BUFFER_VER;
        locked.inputBuffer = input[slot];
        CHECK(api.nvEncLockInputBuffer(encoder, &locked));
        int bad = locked.pitch < width * 4;
        for (uint32_t y = 0; !bad && y < height; y++)
            bad = read_all((uint8_t *)locked.bufferDataPtr + (size_t)y * locked.pitch, width * 4);
        CHECK(api.nvEncUnlockInputBuffer(encoder, input[slot]));
        if (bad) break;
        NV_ENC_PIC_PARAMS picture = {0}; picture.version = NV_ENC_PIC_PARAMS_VER;
        picture.inputBuffer = input[slot]; picture.bufferFmt = NV_ENC_BUFFER_FORMAT_ARGB;
        picture.inputWidth = width; picture.inputHeight = height; picture.inputPitch = locked.pitch;
        picture.outputBitstream = output[slot]; picture.pictureStruct = NV_ENC_PIC_STRUCT_FRAME;
        picture.inputTimeStamp = sequence; picture.frameIdx = sequence;
        if (!previous) picture.encodePicFlags = NV_ENC_PIC_FLAG_FORCEIDR | NV_ENC_PIC_FLAG_OUTPUT_SPSPPS;
        CHECK(api.nvEncEncodePicture(encoder, &picture));
        NV_ENC_LOCK_BITSTREAM stream = {0}; stream.version = NV_ENC_LOCK_BITSTREAM_VER;
        stream.outputBitstream = output[slot];
        CHECK(api.nvEncLockBitstream(encoder, &stream));
        uint32_t response[2] = {htonl(stream.bitstreamSizeInBytes), htonl(sequence)};
        bad = !stream.bitstreamSizeInBytes || stream.bitstreamSizeInBytes > LIMIT ||
            stream.outputTimeStamp != sequence || write_all(response, sizeof(response)) ||
            write_all(stream.bitstreamBufferPtr, stream.bitstreamSizeInBytes);
        CHECK(api.nvEncUnlockBitstream(encoder, output[slot]));
        if (bad) break;
        previous = sequence;
    }
cleanup:
    if (encoder) {
        for (unsigned i = 0; i < 4; i++) {
            if (input[i]) api.nvEncDestroyInputBuffer(encoder, input[i]);
            if (output[i]) api.nvEncDestroyBitstreamBuffer(encoder, output[i]);
        }
        api.nvEncDestroyEncoder(encoder);
    }
    if (context && context_destroy) context_destroy(context);
    if (library) dlclose(library);
    if (cuda) dlclose(cuda);
    return result;
}
