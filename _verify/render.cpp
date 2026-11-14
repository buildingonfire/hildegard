// _verify/render.cpp - throwaway offline driver to hear Hildegard's engine core.
//
// NOT part of the plugin. The host simulator can't script parameter changes, so this
// mini-host loads the built native DLL, records a 0.5 s test tone into Hildegard, stops
// (triggering the buffer swap), opens the dry/wet fully wet, unmutes all four streams,
// then renders ~2 s of the streams playing the recorded chunk back. It writes a WAV and
// prints peak/RMS so we can confirm the engine makes finite, non-silent sound before
// putting it on the MPC. Delete _verify/ once the engine is trusted.
//
// Build (from repo root), then run:
//   <zig> c++ -std=c++17 -I framework plugins/Hildegard/_verify/render.cpp -o plugins/Hildegard/build/verify.exe
//   plugins/Hildegard/build/verify.exe plugins/Hildegard/build/native/hildegard.dll plugins/Hildegard/build/play.wav

#include "mpc_vst2_abi.h"

#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <thread>
#include <vector>

#if defined(_WIN32)
  #include <windows.h>
#else
  #include <dlfcn.h>
#endif

typedef AEffect* (*EntryProc)(audioMasterCallback);

static const double kSR = 44100.0;
static const int    kBlock = 512;

// A transport the plugin can follow: toggle g_playing, advance g_pos each block.
static VstTimeInfo g_time;
static double      g_pos = 0.0;
static bool        g_playing = true;

static VstIntPtr hostCb(AEffect*, VstInt32 opcode, VstInt32, VstIntPtr, void*, float)
{
    switch (opcode)
    {
        case audioMasterVersion:       return 2400;
        case audioMasterGetSampleRate: return (VstIntPtr)kSR;
        case audioMasterGetBlockSize:  return kBlock;
        case audioMasterGetTime:
        {
            std::memset(&g_time, 0, sizeof(g_time));
            g_time.samplePos = g_pos;
            g_time.sampleRate = kSR;
            g_time.tempo = 120.0;
            g_time.ppqPos = (g_pos / kSR) * (120.0 / 60.0);
            g_time.timeSigNumerator = 4; g_time.timeSigDenominator = 4;
            g_time.flags = kVstTempoValid | kVstPpqPosValid | kVstTimeSigValid |
                           (g_playing ? kVstTransportPlaying : 0);
            return (VstIntPtr)&g_time;
        }
        default: return 0;
    }
}

int main(int argc, char** argv)
{
    if (argc < 3) { std::fprintf(stderr, "usage: render <plugin.dll> <out.wav>\n"); return 2; }

#if defined(_WIN32)
    HMODULE h = LoadLibraryA(argv[1]);
    if (!h) { std::fprintf(stderr, "LoadLibrary failed (%lu)\n", (unsigned long)GetLastError()); return 2; }
    EntryProc entry = (EntryProc)GetProcAddress(h, "VSTPluginMain");
    if (!entry) entry = (EntryProc)GetProcAddress(h, "main");
#else
    void* h = dlopen(argv[1], RTLD_NOW);
    if (!h) { std::fprintf(stderr, "dlopen failed: %s\n", dlerror()); return 2; }
    EntryProc entry = (EntryProc)dlsym(h, "VSTPluginMain");
#endif
    if (!entry) { std::fprintf(stderr, "no VSTPluginMain\n"); return 2; }

    AEffect* e = entry(&hostCb);
    if (!e || e->magic != kEffectMagic) { std::fprintf(stderr, "bad AEffect\n"); return 2; }

    auto disp = [&](VstInt32 op, VstInt32 idx = 0, VstIntPtr val = 0, void* ptr = nullptr, float opt = 0.0f)
    { return e->dispatcher(e, op, idx, val, ptr, opt); };

    disp(effOpen);
    disp(effSetSampleRate, 0, 0, nullptr, (float)kSR);
    disp(effSetBlockSize, 0, kBlock);
    disp(effMainsChanged, 0, 1);

    // Parameter indices (SPEC Section 5): record=1, drywet=3; mute = 5 + s*8 + 7.
    const int kRecord = 1, kDryWet = 3;
    auto muteIdx = [](int s) { return 5 + s * 8 + 7; };

    std::vector<float> inL(kBlock), inR(kBlock), outL(kBlock), outR(kBlock);
    float* ins[2]  = { inL.data(),  inR.data()  };
    float* outs[2] = { outL.data(), outR.data() };

    double phase = 0.0;
    const double w = 2.0 * 3.14159265358979 * 330.0 / kSR;   // 330 Hz test tone

    auto runBlock = [&](bool feedTone, std::vector<float>* capL, std::vector<float>* capR)
    {
        for (int i = 0; i < kBlock; ++i)
        {
            float s = feedTone ? 0.6f * (float)std::sin(phase) : 0.0f;
            if (feedTone) phase += w;
            inL[i] = s; inR[i] = s;
        }
        e->processReplacing(e, ins, outs, kBlock);
        g_pos += kBlock;
        if (capL) for (int i = 0; i < kBlock; ++i) { capL->push_back(outL[i]); capR->push_back(outR[i]); }
    };
    auto peakOf = [](const std::vector<float>& L, const std::vector<float>& R) {
        double pk = 0.0; for (size_t i = 0; i < L.size(); ++i) { pk = std::fmax(pk, std::fabs((double)L[i])); pk = std::fmax(pk, std::fabs((double)R[i])); } return pk;
    };

    const int kSave = 4;

    // --- record 0.5 s of the tone, transport PLAYING (Record toggles on/off) ---
    g_playing = true;
    e->setParameter(e, kRecord, 1.0f);           // toggle ON -> start
    const int recBlocks = (int)(0.5 * kSR / kBlock);
    for (int b = 0; b < recBlocks; ++b) runBlock(true, nullptr, nullptr);
    e->setParameter(e, kRecord, 0.0f);           // toggle OFF -> stop + swap
    runBlock(true, nullptr, nullptr);

    // --- playback: full wet, all four streams unmuted, transport PLAYING ---
    e->setParameter(e, kDryWet, 1.0f);
    for (int s = 0; s < 4; ++s) e->setParameter(e, muteIdx(s), 0.0f);
    std::vector<float> pL, pR;
    for (int b = 0; b < (int)(1.5 * kSR / kBlock); ++b) runBlock(false, &pL, &pR);

    // --- item 3: STOP the transport -> streams must fade to silence ---
    g_playing = false;
    std::vector<float> stopL, stopR;
    for (int b = 0; b < (int)(0.4 * kSR / kBlock); ++b) runBlock(false, &stopL, &stopR);
    // tail = the last 0.1 s, after the fade-out should be complete
    std::vector<float> tailL(stopL.end() - (int)(0.1 * kSR), stopL.end());
    std::vector<float> tailR(stopR.end() - (int)(0.1 * kSR), stopR.end());

    // --- item 2: suspend/resume (triggers reset()) then PLAY -> loop must still be there ---
    disp(effMainsChanged, 0, 0);
    disp(effMainsChanged, 0, 1);
    g_playing = true;
    e->setParameter(e, kSave, 1.0f);             // also exercise Save (captures the bounce)
    std::vector<float> rL, rR;
    for (int b = 0; b < (int)(1.0 * kSR / kBlock); ++b) runBlock(false, &rL, &rR);
    std::this_thread::sleep_for(std::chrono::milliseconds(400));   // let the save worker write

    std::printf("segment peaks:  playing=%.4f   after-stop-tail=%.4f   after-resume=%.4f\n",
                peakOf(pL, pR), peakOf(tailL, tailR), peakOf(rL, rR));
    std::printf("  item 3 (stop silences): %s\n", peakOf(tailL, tailR) < 0.01 ? "PASS" : "FAIL");
    std::printf("  item 2 (loop survives stop/resume): %s\n", peakOf(rL, rR) > 0.05 ? "PASS" : "FAIL");

    disp(effMainsChanged, 0, 0);
    disp(effClose);

    // --- stats ---
    double peak = 0.0, sum = 0.0; bool bad = false;
    for (size_t i = 0; i < pL.size(); ++i)
    {
        for (float v : { pL[i], pR[i] })
        {
            if (!std::isfinite(v)) bad = true;
            peak = std::fmax(peak, std::fabs((double)v));
            sum += (double)v * v;
        }
    }
    const double rms = pL.empty() ? 0.0 : std::sqrt(sum / (2.0 * pL.size()));
    std::printf("playback frames=%zu  peak=%.4f (%.1f dBFS)  rms=%.4f (%.1f dBFS)  finite=%s\n",
                pL.size(), peak, 20.0 * std::log10(peak + 1e-12), rms, 20.0 * std::log10(rms + 1e-12),
                bad ? "NO" : "yes");

    // --- write 16-bit stereo WAV ---
    FILE* f = std::fopen(argv[2], "wb");
    if (f)
    {
        const uint32_t frames = (uint32_t)pL.size();
        const uint32_t dataBytes = frames * 4;
        auto w32 = [&](uint32_t v){ uint8_t b[4]={(uint8_t)v,(uint8_t)(v>>8),(uint8_t)(v>>16),(uint8_t)(v>>24)}; std::fwrite(b,1,4,f); };
        auto w16 = [&](uint16_t v){ uint8_t b[2]={(uint8_t)v,(uint8_t)(v>>8)}; std::fwrite(b,1,2,f); };
        std::fwrite("RIFF",1,4,f); w32(36+dataBytes); std::fwrite("WAVE",1,4,f);
        std::fwrite("fmt ",1,4,f); w32(16); w16(1); w16(2); w32((uint32_t)kSR); w32((uint32_t)kSR*4); w16(4); w16(16);
        std::fwrite("data",1,4,f); w32(dataBytes);
        for (uint32_t i = 0; i < frames; ++i)
            for (float v : { pL[i], pR[i] })
            { int s = (int)std::lround(v * 32767.0f); s = s < -32768 ? -32768 : (s > 32767 ? 32767 : s); w16((uint16_t)(int16_t)s); }
        std::fclose(f);
        std::printf("wrote %s\n", argv[2]);
    }
    return bad ? 1 : 0;
}
