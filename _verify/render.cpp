// _verify/render.cpp - offline driver for the parts of Hildegard the host simulator can't script.
//
// NOT part of the plugin. This mini-host loads the built native DLL and drives it like the
// MPC would: record a take, play, stop, resume, Save; bar-synced recording; the speed-knob
// curve (via the display text); presets; project state save/load; a reset mid-take; and the
// old->new chunk crossfade on a swap. Each check prints PASS/FAIL; the exit code is non-zero
// on any FAIL. It also writes a WAV of the first playback for listening.
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
#include <string>
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

// Bar-sync test: the frame position at which the plugin turned Record off by itself
// (it calls audioMasterAutomate(index 1) when a take ends).
static double g_recordOffAt = -1.0;
static double g_blockPos = 0.0;      // position of the block being processed

static VstIntPtr hostCb(AEffect*, VstInt32 opcode, VstInt32 index, VstIntPtr, void*, float opt)
{
    switch (opcode)
    {
        case audioMasterAutomate:
            if (index == 1 && opt < 0.5f && g_recordOffAt < 0.0) g_recordOffAt = g_blockPos;
            return 0;
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

    // --- bar-synced recording (Length = N bars). 120 bpm 4/4: one bar = 2 s = 88200 frames. ---
    // Position only advances while playing, like a real transport.
    const int kLength = 0;
    const double kBar = 88200.0;
    auto runTo = [&](double target) {           // process blocks until g_pos >= target
        while (g_pos < target) {
            g_blockPos = g_pos;
            for (int i = 0; i < kBlock; ++i) { inL[i] = 0.3f; inR[i] = 0.3f; }
            e->processReplacing(e, ins, outs, kBlock);
            if (g_playing) g_pos += kBlock;
            else { static int idle = 0; if (++idle > 200) { idle = 0; break; } }
        }
    };
    auto endBlockOk = [&](double expectEnd) {   // the take ends inside the block holding expectEnd-1
        return g_recordOffAt >= 0.0 && g_recordOffAt <= expectEnd - 1 && expectEnd - 1 < g_recordOffAt + kBlock;
    };
    int barFails = 0;
    auto report = [&](const char* what, bool ok) { std::printf("  bar-sync: %-52s %s\n", what, ok ? "PASS" : "FAIL"); if (!ok) ++barFails; };

    disp(effMainsChanged, 0, 0); disp(effMainsChanged, 0, 1);
    e->setParameter(e, kRecord, 0.0f);
    e->setParameter(e, kLength, 0.2f);            // choice 1 of 0..5 = "1 bar"
    g_playing = true;

    // A) arm mid-bar while playing -> starts at the next bar line, ends 1 bar later.
    g_pos = 4.0 * kBar + 30000.0;                 // 30000 frames into bar 5
    g_recordOffAt = -1.0;
    runTo(g_pos + kBlock);
    e->setParameter(e, kRecord, 1.0f);            // arm
    runTo(7.0 * kBar);
    report("arm mid-bar: ends 1 bar after the next bar line", endBlockOk(6.0 * kBar));

    // B) arm then cancel before the bar line -> nothing records, Record never auto-releases.
    g_pos = 8.0 * kBar + 10000.0;
    g_recordOffAt = -1.0;
    e->setParameter(e, kRecord, 1.0f);            // arm
    runTo(g_pos + 20000.0);
    e->setParameter(e, kRecord, 0.0f);            // cancel
    runTo(11.0 * kBar);
    report("arm then cancel: no take", g_recordOffAt < 0.0);

    // C) arm with the transport stopped -> waits; Play from bar 1 starts at once; 2 bars.
    e->setParameter(e, kLength, 0.4f);            // choice 2 = "2 bars"
    g_playing = false; g_pos = 0.0;
    g_recordOffAt = -1.0;
    e->setParameter(e, kRecord, 1.0f);            // arm while stopped
    runTo(1e9);                                   // stopped: runs 200 idle blocks then returns
    const bool waited = g_recordOffAt < 0.0;
    g_playing = true;
    runTo(3.0 * kBar);
    report("armed while stopped waits for Play", waited);
    report("Play from bar 1: 2-bar take ends at bar 3", endBlockOk(2.0 * kBar));

    std::printf("  bar-sync: %s\n", barFails == 0 ? "ALL PASS" : "FAILURES");

    // ---------------------------------------------------------------------------------
    // Speed knob mapping (SPEC 4): read the display text the MPC would show.
    // ---------------------------------------------------------------------------------
    int otherFails = 0;
    auto check = [&](const char* what, bool ok) { std::printf("  %-62s %s\n", what, ok ? "PASS" : "FAIL"); if (!ok) ++otherFails; };
    auto speedText = [&](float norm) {
        static char buf[64]; buf[0] = 0;
        e->setParameter(e, 5, norm);              // Speed 1
        disp(effGetParamDisplay, 5, 0, buf);
        return std::string(buf);
    };
    // knob x in -8..8 -> norm = (x + 8) / 16
    auto nx = [](double x) { return (float)((x + 8.0) / 16.0); };
    // Speed Scale (param 170): Octaves = 0, Ratios = 0.5, Smooth = 1 (normalised choice index).
    const int kScale = 170;
    e->setParameter(e, kScale, 0.0f);   // Octaves (default)
    check("octaves: knob centre = 0.00x",            speedText(nx(0.0))  == "0.00x  hold");
    check("octaves: very slow holds (x=0.1)",        speedText(nx(0.1))  == "0.00x  hold");
    check("octaves: x=0.5 -> 0.25x",                 speedText(nx(0.5))  == "0.25x  - 2 octaves");
    check("octaves: x=2.1 -> 1.00x",                 speedText(nx(2.1))  == "1.00x  unison");
    check("octaves: x=3 (1.5x smooth) -> 2.00x",     speedText(nx(3.0))  == "2.00x  + 1 octave");
    check("octaves: x=-4.1 -> -2.00x",               speedText(nx(-4.1)) == "-2.00x  + 1 octave");
    check("octaves: full right = 4.00x (cap)",       speedText(1.0f)     == "4.00x  + 2 octaves");
    e->setParameter(e, kScale, 0.5f);   // Ratios
    check("ratios: x=3 -> 1.50x",                    speedText(nx(3.0))  == "1.50x  + 1 fifth");
    check("ratios: x=1.4 (0.7x smooth) -> 0.67x",    speedText(nx(1.4))  == "0.67x  - 1 fifth");
    check("ratios: x=5 (2.5x smooth) -> 3.00x",      speedText(nx(5.0))  == "3.00x  + 1 octave & a fifth");
    check("ratios: x=-1.5 (0.75x smooth) -> -0.67x", speedText(nx(-1.5)) == "-0.67x  - 1 fifth");
    check("ratios: x=0.667 (1/3) -> octave & fifth down", speedText(nx(0.667)) == "0.33x  - 1 octave & a fifth");
    check("ratios: x=0.25 (1/8) -> 3 octaves down",       speedText(nx(0.25))  == "0.12x  - 3 octaves");
    e->setParameter(e, kScale, 1.0f);   // Smooth
    check("smooth: x=2.1 -> 1.05x (no snapping)",    speedText(nx(2.1))  == "1.05x");
    check("smooth: x=0.1 -> 0.05x",                  speedText(nx(0.1))  == "0.05x");
    check("smooth: x=3 -> 1.50x",                    speedText(nx(3.0))  == "1.50x");
    e->setParameter(e, kScale, 0.0f);   // back to Octaves for the tests below
    {
        // default of a fresh instance: Speed 1 = 1x, Speed 3 = -1x
        AEffect* e2 = entry(&hostCb);
        char b1[64] = {0}, b3[64] = {0};
        e2->dispatcher(e2, effOpen, 0, 0, nullptr, 0.0f);
        e2->dispatcher(e2, effGetParamDisplay, 5, 0, b1, 0.0f);
        e2->dispatcher(e2, effGetParamDisplay, 5 + 2 * 8, 0, b3, 0.0f);
        check("speed: defaults read 1.00x and -1.00x", std::string(b1) == "1.00x  unison" && std::string(b3) == "-1.00x  unison");
        e2->dispatcher(e2, effClose, 0, 0, nullptr, 0.0f);
    }
    e->setParameter(e, 5, nx(2.0));

    // Presets: "Octave Stack" (program 1) sets Speed 2 to 0.5x and unmutes stream 2.
    {
        char sp2[64] = {0}, m2[64] = {0}, name[64] = {0};
        disp(effSetProgram, 0, 1);
        disp(effGetProgramName, 0, 0, name);
        disp(effGetParamDisplay, 5 + 8, 0, sp2);
        disp(effGetParamDisplay, 5 + 8 + 7, 0, m2);
        check("preset: program 1 is \"Octave Stack\"", std::string(name) == "Octave Stack");
        check("preset: it sets Speed 2 = 0.50x and unmutes stream 2",
              std::string(sp2) == "0.50x  - 1 octave" && std::string(m2) == "Active");
        disp(effSetProgram, 0, 0);
        e->setParameter(e, 5, nx(2.0));
    }

    // ---------------------------------------------------------------------------------
    // Project state (SPEC 9): record a take, save the chunk, wipe it with another take,
    // reload -> the first take is back. Then save -> load -> save is byte-identical.
    // ---------------------------------------------------------------------------------
    auto getChunk = [&](std::vector<uint8_t>& out) {
        void* p = nullptr;
        const VstIntPtr n = disp(effGetChunk, 0, 0, &p);
        out.assign((uint8_t*)p, (uint8_t*)p + n);
    };
    auto setChunk = [&](std::vector<uint8_t>& in) { disp(effSetChunk, 0, (VstIntPtr)in.size(), in.data()); };
    auto recordFree = [&](double seconds, float level) {
        e->setParameter(e, kLength, 0.0f);       // Free
        e->setParameter(e, kRecord, 1.0f);
        for (int b = 0; b < (int)(seconds * kSR / kBlock); ++b) {
            for (int i = 0; i < kBlock; ++i) { inL[i] = level; inR[i] = -level; }
            e->processReplacing(e, ins, outs, kBlock); g_pos += kBlock;
        }
        e->setParameter(e, kRecord, 0.0f);
        e->processReplacing(e, ins, outs, kBlock); g_pos += kBlock;
    };
    auto wetLevel = [&]() {                      // steady output level, input silent, all wet
        e->setParameter(e, kDryWet, 1.0f);
        double pk = 0.0;
        for (int b = 0; b < 40; ++b) {
            for (int i = 0; i < kBlock; ++i) { inL[i] = 0.0f; inR[i] = 0.0f; }
            e->processReplacing(e, ins, outs, kBlock); g_pos += kBlock;
            if (b >= 20) for (int i = 0; i < kBlock; ++i) pk = std::fmax(pk, std::fabs((double)outL[i]));
        }
        return pk;
    };

    disp(effMainsChanged, 0, 0); disp(effMainsChanged, 0, 1);
    g_playing = true;
    for (int s = 0; s < 4; ++s) e->setParameter(e, muteIdx(s), s == 0 ? 0.0f : 1.0f);
    recordFree(0.5, 0.5f);
    const double levelA = wetLevel();
    std::vector<uint8_t> saved; getChunk(saved);
    recordFree(0.5, 0.1f);
    const double levelB = wetLevel();
    setChunk(saved);
    const double levelC = wetLevel();
    std::printf("  state: take A level %.3f, take B %.3f, after reload %.3f, chunk %zu bytes\n", levelA, levelB, levelC, saved.size());
    check("state: chunk includes the recorded audio (> 80 KB for 0.5 s)", saved.size() > 80000);
    check("state: reloading restores take A", std::fabs(levelC - levelA) < 0.02 && std::fabs(levelB - levelA) > 0.1);

    std::vector<uint8_t> again; getChunk(again);
    bool same = again == saved;
    if (!same) {   // name the parameters that differ
        size_t pos = 12; uint32_t n = saved[8] | saved[9] << 8 | saved[10] << 16 | saved[11] << 24;
        for (uint32_t k = 0; k < n && pos < saved.size() && pos < again.size(); ++k) {
            const uint8_t L = saved[pos];
            std::string id((const char*)&saved[pos + 1], L);
            if (std::memcmp(&saved[pos], &again[pos], 1 + L + 8) != 0) std::printf("    differs: %s\n", id.c_str());
            pos += 1 + L + 8;
        }
        if (std::memcmp(saved.data() + pos, again.data() + pos, std::min(saved.size(), again.size()) - pos) != 0)
            std::printf("    differs: extra state\n");
    }
    check("state: save -> load -> save is byte-identical", same);

    // A take in progress survives a reset (the MPC may reset the plugin on transport stop).
    e->setParameter(e, kRecord, 1.0f);
    for (int b = 0; b < (int)(0.3 * kSR / kBlock); ++b) {
        for (int i = 0; i < kBlock; ++i) { inL[i] = 0.4f; inR[i] = 0.4f; }
        e->processReplacing(e, ins, outs, kBlock); g_pos += kBlock;
    }
    disp(effMainsChanged, 0, 0); disp(effMainsChanged, 0, 1);   // reset() mid-take
    std::vector<uint8_t> afterReset; getChunk(afterReset);
    // 0.3 s take = ~13230 frames x 4 bytes in the chunk, vs 0.5 s (~88 KB) for take A
    check("reset mid-take keeps the take and releases Record",
          e->getParameter(e, kRecord) < 0.5f && afterReset.size() > 40000 && afterReset.size() < saved.size());

    // Swap crossfade: a new take must not click. Record a steady tone, play, record a second
    // tone of different level; the biggest sample-to-sample jump around the swap stays small.
    {
        e->setParameter(e, kDryWet, 1.0f);
        auto toneTake = [&](double amp) {
            e->setParameter(e, kRecord, 1.0f);
            double ph = 0.0;
            for (int b = 0; b < (int)(0.5 * kSR / kBlock); ++b) {
                for (int i = 0; i < kBlock; ++i) { inL[i] = inR[i] = (float)(amp * std::sin(ph)); ph += 2.0 * 3.14159265358979 * 220.0 / kSR; }
                e->processReplacing(e, ins, outs, kBlock); g_pos += kBlock;
            }
        };
        toneTake(0.5);
        e->setParameter(e, kRecord, 0.0f);
        for (int b = 0; b < 30; ++b) { for (int i = 0; i < kBlock; ++i) inL[i] = inR[i] = 0.0f; e->processReplacing(e, ins, outs, kBlock); }
        // second take recorded from a phase-shifted tone; capture output around the swap
        e->setParameter(e, kRecord, 1.0f);
        double ph = 1.3;
        for (int b = 0; b < (int)(0.5 * kSR / kBlock); ++b) {
            for (int i = 0; i < kBlock; ++i) { inL[i] = inR[i] = (float)(0.2 * std::sin(ph)); ph += 2.0 * 3.14159265358979 * 330.0 / kSR; }
            e->processReplacing(e, ins, outs, kBlock);
        }
        e->setParameter(e, kRecord, 0.0f);
        // dry/wet is 100% wet, so the output is the streams only; input silent from here
        double maxJump = 0.0; float prev = outL[kBlock - 1];
        for (int b = 0; b < 20; ++b) {
            for (int i = 0; i < kBlock; ++i) inL[i] = inR[i] = 0.0f;
            e->processReplacing(e, ins, outs, kBlock);
            for (int i = 0; i < kBlock; ++i) { maxJump = std::fmax(maxJump, std::fabs((double)outL[i] - prev)); prev = outL[i]; }
        }
        // a 220 Hz sine at <= ~0.4 peak moves at most ~0.013 per sample; a click would be >> that
        std::printf("  swap: largest sample-to-sample step after the swap = %.4f\n", maxJump);
        check("swap: old->new chunk crossfade has no click", maxJump < 0.05);
    }

    // Tone = DJ filter: the current take is a 330 Hz tone. Full right (HP to 3 kHz) must cut it
    // hard; centre must leave it alone; full left (LP to 200 Hz) must also cut it clearly.
    {
        g_playing = true;
        for (int s = 0; s < 4; ++s) e->setParameter(e, muteIdx(s), s == 0 ? 0.0f : 1.0f);
        e->setParameter(e, 5, nx(2.1));                  // Speed 1 = 1x
        auto rmsAtTone = [&](float toneNorm) {
            e->setParameter(e, 6, toneNorm);             // Tone 1 (norm 0.5 = centre)
            for (int b = 0; b < 40; ++b) { for (int i = 0; i < kBlock; ++i) inL[i] = inR[i] = 0.0f; e->processReplacing(e, ins, outs, kBlock); g_pos += kBlock; }
            double sum = 0.0; int cnt = 0;
            for (int b = 0; b < 40; ++b) {
                for (int i = 0; i < kBlock; ++i) inL[i] = inR[i] = 0.0f;
                e->processReplacing(e, ins, outs, kBlock); g_pos += kBlock;
                for (int i = 0; i < kBlock; ++i) { sum += (double)outL[i] * outL[i]; ++cnt; }
            }
            return 20.0 * std::log10(std::sqrt(sum / cnt) + 1e-12);
        };
        const double flat = rmsAtTone(0.5f), hp = rmsAtTone(1.0f), lp = rmsAtTone(0.0f);
        std::printf("  tone: centre %.1f dB, full right (HP) %.1f dB, full left (LP) %.1f dB\n", flat, hp, lp);
        check("tone: full right cuts a 330 Hz loop by > 20 dB", flat - hp > 20.0);
        check("tone: full left cuts a 330 Hz loop by > 6 dB",   flat - lp > 6.0);
        e->setParameter(e, 6, 0.5f);
    }

    // Live speed sign (device bug 2026-11-15): turning Speed negative WHILE playing must reverse
    // the head. Watch stream 1's displayed position (param 37) move forward, then backward.
    {
        g_playing = true;
        for (int s = 0; s < 4; ++s) e->setParameter(e, muteIdx(s), s == 0 ? 0.0f : 1.0f);
        auto stepsUp = [&](float speedNorm) {     // count forward vs backward moves of pos1
            e->setParameter(e, 5, speedNorm);
            int up = 0, down = 0;
            for (int b = 0; b < 30; ++b) { for (int i = 0; i < kBlock; ++i) inL[i] = inR[i] = 0.0f; e->processReplacing(e, ins, outs, kBlock); }
            float prev = e->getParameter(e, 37);
            for (int k = 0; k < 12; ++k) {
                for (int b = 0; b < 2; ++b) { for (int i = 0; i < kBlock; ++i) inL[i] = inR[i] = 0.0f; e->processReplacing(e, ins, outs, kBlock); g_pos += kBlock; }
                std::this_thread::sleep_for(std::chrono::milliseconds(60));   // let the UI worker publish
                const float p = e->getParameter(e, 37);
                if (p > prev + 1e-4f) ++up; else if (p < prev - 1e-4f) ++down;
                prev = p;
            }
            return up - down;
        };
        const int fwd = stepsUp(nx(2.1));          // +1x
        const int rev = stepsUp(nx(-2.1));         // -1x, changed while playing
        std::printf("  speed sign: +1x net steps %d, -1x net steps %d\n", fwd, rev);
        check("speed: turning Speed negative while playing reverses the head", fwd > 4 && rev < -4);
    }

    std::printf("  other checks: %s\n", otherFails == 0 ? "ALL PASS" : "FAILURES");

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
    return (bad || barFails || otherFails) ? 1 : 0;
}
