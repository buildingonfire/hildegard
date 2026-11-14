// Hildegard.cpp - insert effect for standalone Akai MPC. Building on Fire, MIT.
//
// Hildegard records a chunk of the track into a frozen buffer, then plays that buffer
// back through four independent tape-style playheads ("streams"), each with its own
// speed, direction, loop region, tone, pan and volume. See SPEC.md.
//
// Signal flow (per block, in place):
//   input --+----------------------------------------------------------> dry ---+
//           |                                                                   |
//           +--> [record source] --> back buffer                               |
//                                                                               v
//   front buffer (frozen chunk) --> Stream 1..4 --> tone --> pan --> vol --> mute --> Sigma
//                                                                               |
//                                        wet = Sigma --> soft clip --> [dry/wet mix] --> output
//
// THIS FILE = Build Order steps 2-3 (SPEC Section 14): the engine core.
//   Done here: 30 s double buffer, Free-length record + buffer swap, four streams with
//     4-point Hermite interpolation, tape speed (incl. reverse and hold-at-zero),
//     loop + ping-pong with declick crossfades, tilt tone, equal-power pan, perceptual
//     volume, muting, dry/wet, wet soft clip, parameter smoothing.
//   Deliberately NOT here yet (later build-order steps): display parameters (37-169),
//     transport-follow / bar-synced record / Resample timing, the speed-knob snapping
//     curve, save-to-disk, project-chunk state, and the hand-written skin. Speed is a
//     plain linear parameter for now; the plateau-snapping mapping comes with the UI.
//   Also simplified for now: the buffer swap uses a short wet fade-in rather than a
//     per-stream old->new chunk crossfade.
//
// Realtime: process() does no allocation, locks, I/O or logging. The two 30 s buffers
// are allocated once in prepare(). All transcendental functions (pow/exp/cos/sin) run
// at most once per block; the per-sample inner loop is only cheap arithmetic plus
// one-pole filtering and Hermite reads. Recording and buffer swap happen on the audio
// thread, so there is no cross-thread buffer hazard.

#include "mpc_plugin.h"
#include "dsp/math.h"
#include "dsp/onepole.h"
#include "dsp/smoother.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <ctime>
#include <string>
#include <thread>
#include <vector>

#if defined(_WIN32)
  #include <direct.h>
  #define MPC_MKDIR(p) _mkdir(p)
#else
  #include <sys/stat.h>
  #define MPC_MKDIR(p) ::mkdir((p), 0777)
#endif

using namespace mpc::dsp;

namespace {

// ---------------------------------------------------------------------------
// Parameter layout (SPEC Section 5). ORDER AND IDS ARE PERMANENT once released.
// Global 0-4, then 8 per stream for streams 1..4 (indices 5..36).
// Display-only parameters (37-169) are appended in a later build-order step.
// ---------------------------------------------------------------------------
enum Global { kLength = 0, kRecord, kResample, kDryWet, kSave, kNumGlobal };
enum StreamOff { oSpeed = 0, oTone, oPan, oVolume, oIn, oOut, oMode, oMute, kPerStream };

constexpr int kNumStreams = 4;
constexpr int kNumParams  = kNumGlobal + kNumStreams * kPerStream;   // 5 + 32 = 37
constexpr int streamBase(int s) { return kNumGlobal + s * kPerStream; }   // s = 0..3

constexpr int   kMaxFrames    = 1323000;   // 30 s * 44100
constexpr float kMinRegionMs  = 10.0f;     // shortest loop region
constexpr float kLoopXfadePct = 0.10f;     // loop end->start crossfade = 10% of the region (SPEC)
constexpr float kMinXfadeMs   = 2.0f;      // but never shorter than this
constexpr float kRecFadeMs    = 5.0f;      // fade in/out on the recorded chunk edges
constexpr float kSwapFadeMs   = 20.0f;     // wet fade-in after a buffer swap
constexpr float kPlayInMs     = 5.0f;      // stream fade-in when the transport starts
constexpr float kPlayOutMs    = 10.0f;     // stream fade-out when the transport stops

// 4-point Hermite (Catmull-Rom). x in [0,1) between y1 and y2.
inline float hermite(float y0, float y1, float y2, float y3, float x)
{
    const float c1 = 0.5f * (y2 - y0);
    const float c2 = y0 - 2.5f * y1 + 2.0f * y2 - 0.5f * y3;
    const float c3 = 0.5f * (y3 - y0) + 1.5f * (y1 - y2);
    return ((c3 * x + c2) * x + c1) * x + y1;
}

struct Chunk
{
    std::vector<float> L, R;
    int length = 0;
    void alloc() { L.assign(kMaxFrames, 0.0f); R.assign(kMaxFrames, 0.0f); }
};

inline float sampleAt(const std::vector<float>& buf, int len, double pos)
{
    if (len <= 0) return 0.0f;
    const int i = (int)std::floor(pos);
    const float frac = (float)(pos - i);
    auto at = [&](int k) { return buf[(size_t)clamp(k, 0, len - 1)]; };
    return hermite(at(i - 1), at(i), at(i + 1), at(i + 2), frac);
}

// One tape playhead reading the front chunk. All smoothers below hold *gains* (or the
// speed value), so the per-sample loop never calls a transcendental function.
struct Stream
{
    double head = 0.0;
    int    dir = 1;             // +1 / -1 for ping-pong
    bool   started = false;

    Smoother speedS;            // speed value (frames/frame)
    Smoother volG, panLG, panRG, gLowG, gHighG, muteG;   // smoothed gains
    OnePoleLP toneLP_L, toneLP_R;   // fixed ~700 Hz tilt crossover

    // Per-block loop region, crossfade length and mode (plain arithmetic, set once per block).
    float a = 0.0f, b = 0.0f, xf = 0.0f;
    int   mode = 0;

    void prepare(double sr)
    {
        speedS.setTime(20.0f, sr);
        gLowG.setTime(20.0f, sr); gHighG.setTime(20.0f, sr);
        panLG.setTime(50.0f, sr); panRG.setTime(50.0f, sr);
        volG.setTime(50.0f, sr);
        muteG.setTime(10.0f, sr);
        toneLP_L.setCutoff(700.0f, sr); toneLP_R.setCutoff(700.0f, sr);
    }
    void reset()
    {
        head = 0.0; dir = 1; started = false;
        toneLP_L.reset(); toneLP_R.reset();
    }
};

// Perceptual volume: 0..100 (%) -> gain via a square-law taper (100% = unity, 50% = -12 dB).
// Cheap (no transcendental) and musically even; the exact law is a SPEC Section 16 "confirm".
inline float volumeGain(float pct)
{
    const float p = clamp(pct, 0.0f, 100.0f) * 0.01f;
    return p * p;
}

// ---- Save-to-disk helpers (SPEC Section 8). Called only from the save worker thread. ----
inline bool fileExistsLocal(const std::string& p)
{
    if (std::FILE* f = std::fopen(p.c_str(), "rb")) { std::fclose(f); return true; }
    return false;
}

inline std::string parentDir(const std::string& p)
{
    const size_t i = p.find_last_of('/');
    if (i == std::string::npos) return std::string();
    return i == 0 ? std::string("/") : p.substr(0, i);
}

// Write `frames` of a stereo float buffer as a 24-bit PCM WAV. Returns false on I/O error.
inline bool writeWav24(const std::string& path, const std::vector<float>& L,
                       const std::vector<float>& R, int frames, uint32_t sampleRate)
{
    if (frames <= 0) return false;
    const uint16_t channels = 2, bits = 24, blockAlign = channels * (bits / 8);
    const uint32_t dataBytes = (uint32_t)frames * blockAlign;

    std::FILE* f = std::fopen(path.c_str(), "wb");
    if (f == nullptr) return false;
    auto w32 = [&](uint32_t v){ uint8_t b[4]={(uint8_t)v,(uint8_t)(v>>8),(uint8_t)(v>>16),(uint8_t)(v>>24)}; std::fwrite(b,1,4,f); };
    auto w16 = [&](uint16_t v){ uint8_t b[2]={(uint8_t)v,(uint8_t)(v>>8)}; std::fwrite(b,1,2,f); };

    std::fwrite("RIFF",1,4,f); w32(36 + dataBytes); std::fwrite("WAVE",1,4,f);
    std::fwrite("fmt ",1,4,f); w32(16); w16(1); w16(channels);
    w32(sampleRate); w32(sampleRate * blockAlign); w16(blockAlign); w16(bits);
    std::fwrite("data",1,4,f); w32(dataBytes);
    for (int i = 0; i < frames; ++i)
        for (int ch = 0; ch < 2; ++ch)
        {
            float s = clamp(ch == 0 ? L[(size_t)i] : R[(size_t)i], -1.0f, 1.0f);
            int32_t v = (int32_t)std::lround((double)s * 8388607.0);
            uint8_t b[3] = {(uint8_t)v, (uint8_t)(v >> 8), (uint8_t)(v >> 16)};
            std::fwrite(b, 1, 3, f);
        }
    const bool ok = std::ferror(f) == 0;
    std::fclose(f);
    return ok;
}

} // namespace

class Hildegard : public mpc::Plugin
{
public:
    Hildegard() : mpc::Plugin(makeInfo())
    {
        using namespace mpc;

        // ----- Global (0-4) -----
        addParam(choiceParam("length",   "Length",   {"Free", "1 bar", "2 bars", "4 bars", "8 bars", "16 bars"}, 0));
        addParam(boolParam  ("record",   "Record",   false));
        addParam(boolParam  ("resample", "Resample", false));
        addParam(floatParam ("drywet",   "Dry/Wet",  0.0f, 100.0f, 50.0f, "%", 0));
        addParam(boolParam  ("save",     "Save",     false));

        // ----- Per stream (5-36) -----
        static const float defSpeed[4] = { 1.0f, 0.5f, -1.0f, 2.0f };
        static const float defPan[4]   = { 0.0f, -30.0f, 30.0f, 0.0f };
        static const float defVol[4]   = { 80.0f, 65.0f, 60.0f, 50.0f };
        static const bool  defMute[4]  = { false, true, true, true };   // S1 active, S2-4 muted
        for (int s = 0; s < kNumStreams; ++s)
        {
            const std::string n = std::to_string(s + 1);
            addParam(floatParam (("s" + n + "_speed").c_str(),  ("Speed " + n).c_str(),  -8.0f, 8.0f, defSpeed[s], "x", 2));
            addParam(floatParam (("s" + n + "_tone").c_str(),   ("Tone " + n).c_str(),   -100.0f, 100.0f, 0.0f, "", 0));
            addParam(floatParam (("s" + n + "_pan").c_str(),    ("Pan " + n).c_str(),    -100.0f, 100.0f, defPan[s], "", 0));
            addParam(floatParam (("s" + n + "_volume").c_str(), ("Volume " + n).c_str(), 0.0f, 100.0f, defVol[s], "%", 0));
            addParam(floatParam (("s" + n + "_in").c_str(),     ("In " + n).c_str(),     0.0f, 100.0f, 0.0f, "%", 0));
            addParam(floatParam (("s" + n + "_out").c_str(),    ("Out " + n).c_str(),    0.0f, 100.0f, 100.0f, "%", 0));
            addParam(choiceParam(("s" + n + "_mode").c_str(),   ("Mode " + n).c_str(),   {"Loop", "Ping-pong"}, 0));
            addParam(boolParam  (("s" + n + "_mute").c_str(),   ("Mute " + n).c_str(),   defMute[s]));
        }

        if (numParams() != kNumParams) mpc::log("Hildegard: parameter count mismatch");

        // Owned background thread for Save-to-disk (never writes files on the audio thread).
        saveWorker_ = std::thread([this] { saveLoop(); });
    }

    ~Hildegard() override
    {
        saveStop_.store(true, std::memory_order_release);
        if (saveWorker_.joinable()) saveWorker_.join();
    }

    void prepare(double sampleRate, int /*maxBlockSize*/) override
    {
        sampleRate_ = sampleRate;
        if (chunk_[0].L.empty()) { chunk_[0].alloc(); chunk_[1].alloc(); saveBuf_.alloc(); }
        for (Stream& s : streams_) s.prepare(sampleRate);
        dryS_.setTime(50.0f, sampleRate);
        wetS_.setTime(50.0f, sampleRate);
        tpUp_   = 1.0f / std::max(1.0f, msToSamples(kPlayInMs, sampleRate));
        tpDown_ = 1.0f / std::max(1.0f, msToSamples(kPlayOutMs, sampleRate));
        reset();
    }

    void reset() override
    {
        // IMPORTANT: do NOT reset front_ or the chunk lengths here. reset() runs on every
        // transport stop/suspend and after a state load; zeroing front_ orphaned the user's
        // recorded loop (it pointed at the empty buffer). The chunk must survive.
        recording_ = recPrev_ = false;
        recLen_ = 0;
        capturing_ = false;                       // abort any in-progress save capture
        swapFade_ = 1.0f; swapRamp_ = 0.0f; swapRising_ = false;
        tpGain_ = 0.0f; wasPlaying_ = false;      // streams fade in when the transport starts
        for (Stream& s : streams_) s.reset();
        float gD, gW; equalPowerMix(get(kDryWet) * 0.01f, gD, gW);
        dryS_.snap(gD); wetS_.snap(gW);
        for (int s = 0; s < kNumStreams; ++s) snapStreamGains(s);
    }

    void process(mpc::ProcessContext& ctx) override
    {
        const int n = ctx.numFrames;
        const float* inL = ctx.inputs && ctx.numInputs > 0 ? ctx.inputs[0] : nullptr;
        const float* inR = ctx.inputs && ctx.numInputs > 1 ? ctx.inputs[1] : inL;
        float* outL = ctx.outputs[0];
        float* outR = ctx.numOutputs > 1 ? ctx.outputs[1] : ctx.outputs[0];

        // ----- transport: streams follow play/stop (SPEC 3.7) -----
        const mpc::Transport& tr = ctx.transport;
        const bool playing = !tr.valid || tr.playing;   // if the host gives no transport, keep running
        const bool playEdge = playing && !wasPlaying_;
        if (playEdge) for (Stream& s : streams_) s.started = false;   // re-seek to in/out on start

        // ----- per-block setup (all transcendentals happen here, once) -----
        handleRecordButton();
        handleSaveButton();
        { float gD, gW; equalPowerMix(get(kDryWet) * 0.01f, gD, gW); dryS_.setTarget(gD); wetS_.setTarget(gW); }
        const bool resample = getBool(kResample);

        Chunk& front = chunk_[front_];
        Chunk& back  = chunk_[1 - front_];
        const int flen = front.length;

        for (int s = 0; s < kNumStreams; ++s)
        {
            Stream& st = streams_[s];
            const int b = streamBase(s);
            st.speedS.setTarget(get(b + oSpeed));
            st.volG.setTarget(volumeGain(get(b + oVolume)));
            float pl, pr; equalPowerPan(clamp(get(b + oPan) * 0.01f, -1.0f, 1.0f), pl, pr);
            st.panLG.setTarget(pl); st.panRG.setTarget(pr);
            const float amt = clamp(get(b + oTone) * 0.01f, -1.0f, 1.0f);
            st.gLowG.setTarget(dbToGain(-6.0f * amt));
            st.gHighG.setTarget(dbToGain(+6.0f * amt));
            st.muteG.setTarget(getBool(b + oMute) ? 0.0f : 1.0f);

            // Loop region in frames, min length enforced.
            float a = get(b + oIn)  * 0.01f * (flen - 1);
            float bb = get(b + oOut) * 0.01f * (flen - 1);
            if (a > bb) std::swap(a, bb);
            const float minLen = msToSamples(kMinRegionMs, sampleRate_);
            if (bb - a < minLen) bb = clamp(a + minLen, 0.0f, (float)(flen > 0 ? flen - 1 : 0));
            st.a = a; st.b = bb;
            const float region = bb - a;
            st.xf = clamp(kLoopXfadePct * region, msToSamples(kMinXfadeMs, sampleRate_), 0.5f * region);
            st.mode = getInt(b + oMode);
        }

        const float recFade = msToSamples(kRecFadeMs, sampleRate_);
        const float tpTarget = playing ? 1.0f : 0.0f;

        for (int i = 0; i < n; ++i)
        {
            const float dryL = inL ? inL[i] : 0.0f;
            const float dryR = inR ? inR[i] : 0.0f;

            float wetL = 0.0f, wetR = 0.0f;
            for (int s = 0; s < kNumStreams; ++s)
                renderStream(streams_[s], front, flen, playing, wetL, wetR);

            wetL = softClip(wetL);
            wetR = softClip(wetR);

            if (swapRising_)
            {
                swapFade_ += swapRamp_;
                if (swapFade_ >= 1.0f) { swapFade_ = 1.0f; swapRising_ = false; }
            }

            // Transport fade: streams fade in on start, out on stop; heads hold while stopped.
            if (tpGain_ < tpTarget) { tpGain_ += tpUp_; if (tpGain_ > tpTarget) tpGain_ = tpTarget; }
            else if (tpGain_ > tpTarget) { tpGain_ -= tpDown_; if (tpGain_ < tpTarget) tpGain_ = tpTarget; }

            const float wetGain = swapFade_ * tpGain_;
            wetL *= wetGain; wetR *= wetGain;

            const float gDry = dryS_.process();
            const float gWet = wetS_.process();
            const float yL = dryL * gDry + wetL * gWet;
            const float yR = dryR * gDry + wetR * gWet;

            // ----- recording (input, or the output when Resample is on) -----
            if (recording_ && recLen_ < kMaxFrames)
            {
                float rl = resample ? yL : dryL;
                float rr = resample ? yR : dryR;
                if (recLen_ < (int)recFade) { const float g = recLen_ / recFade; rl *= g; rr *= g; }
                back.L[(size_t)recLen_] = rl;
                back.R[(size_t)recLen_] = rr;
                if (++recLen_ >= kMaxFrames) finishRecording();
            }

            // ----- Save capture: bounce the effected OUTPUT (what you hear) for one chunk length -----
            if (capturing_)
            {
                saveBuf_.L[(size_t)capIdx_] = yL;
                saveBuf_.R[(size_t)capIdx_] = yR;
                if (++capIdx_ >= capLen_)
                {
                    capturing_ = false;
                    saveBuf_.length = capLen_;
                    saveReq_.store(0, std::memory_order_release);   // tell the worker to write it
                }
            }

            outL[i] = yL;
            outR[i] = yR;
        }

        wasPlaying_ = playing;
    }

    bool formatValue(int index, float plain, std::string& out) override
    {
        if (index < kNumGlobal) return false;
        const int off = (index - kNumGlobal) % kPerStream;
        char buf[32];
        if (off == oSpeed) { std::snprintf(buf, sizeof(buf), "%.2fx", (double)plain); out = buf; return true; }
        if (off == oPan)
        {
            const int p = (int)std::lround(plain);
            out = p == 0 ? "C" : (p < 0 ? "L" + std::to_string(-p) : "R" + std::to_string(p));
            return true;
        }
        if (off == oTone)
        {
            const int t = (int)std::lround(plain);
            if (t == 0) out = "flat";
            else { std::snprintf(buf, sizeof(buf), "%s %d", t < 0 ? "dark" : "bright", t < 0 ? -t : t); out = buf; }
            return true;
        }
        if (off == oMute) { out = plain >= 0.5f ? "Muted" : "Active"; return true; }
        return false;
    }

private:
    void handleRecordButton()
    {
        // Record is a TOGGLE, not a momentary button: it stays ON (lit) for as long as the
        // user is recording, so they can hold a chunk of any length up to the 30 s limit.
        // Tap on = start; tap off = stop. (The real plugin's lit "recording" look will come
        // from a status film strip; until then the button value itself shows the state.)
        const bool rec = getBool(kRecord);
        if (rec && !recPrev_)        { if (!recording_) { recording_ = true; recLen_ = 0; } }  // start
        else if (!rec && recPrev_)   { if (recording_) finishRecording(); }                     // stop
        recPrev_ = rec;
    }

    void finishRecording()
    {
        recording_ = false;
        Chunk& back = chunk_[1 - front_];
        back.length = recLen_;
        const int fade = (int)msToSamples(kRecFadeMs, sampleRate_);
        for (int k = 0; k < fade && k < back.length; ++k)
        {
            const float g = (float)k / (float)fade;
            back.L[(size_t)(back.length - 1 - k)] *= g;
            back.R[(size_t)(back.length - 1 - k)] *= g;
        }
        const int oldLen = chunk_[front_].length;
        for (Stream& s : streams_)
        {
            const double prop = oldLen > 1 ? s.head / (double)(oldLen - 1) : 0.0;
            s.head = prop * (double)(back.length > 1 ? back.length - 1 : 0);
            s.started = false;   // re-place at in/out point against the new chunk
        }
        front_ = 1 - front_;
        swapFade_ = 0.0f;                                   // fade the wet back in after the swap
        swapRamp_ = 1.0f / std::max(1.0f, msToSamples(kSwapFadeMs, sampleRate_));
        swapRising_ = true;

        // If recording stopped on its own (hit the 30 s limit) while the button is still
        // held on, turn the button off so it reflects that recording has ended.
        if (getBool(kRecord)) { set(kRecord, 0.0f); notifyHostParameterChanged(kRecord); recPrev_ = false; }
    }

    // ----- Save to disk (SPEC Section 8): bounce the EFFECTED output, captured live -----
    // Save writes what you hear - the four streams processed and mixed - not the raw chunk.
    // The capture runs in process() for one chunk length into saveBuf_, then the worker writes
    // it. (A clean/dirty switch could be added later; dirty is the useful default.)
    void handleSaveButton()
    {
        const bool save = getBool(kSave);
        if (save && !savePrev_ && !capturing_ && saveReq_.load(std::memory_order_acquire) < 0
            && chunk_[front_].length > 0)
        {
            capLen_ = chunk_[front_].length;
            capIdx_ = 0;
            capturing_ = true;
        }
        savePrev_ = save;
        if (save) { set(kSave, 0.0f); notifyHostParameterChanged(kSave); }   // momentary
    }

    void saveLoop()
    {
        using namespace std::chrono_literals;
        while (!saveStop_.load(std::memory_order_acquire))
        {
            std::this_thread::sleep_for(5ms);
            if (saveReq_.load(std::memory_order_acquire) >= 0) { doSave(); saveReq_.store(-1, std::memory_order_release); }
        }
    }

    // Write the captured bounce (already in saveBuf_) to disk. No audio-thread copy needed:
    // process() filled saveBuf_ before raising saveReq_, and no new capture can start until
    // this completes (guarded by saveReq_/capturing_).
    void doSave()
    {
        const int len = saveBuf_.length;
        if (len <= 0) return;

        const std::string file = timestampName();
        const uint32_t sr = (uint32_t)sampleRate_;

        // Primary target: <drive root>/Hildegard Loops/ (two levels up from the plugin folder).
        const std::string pf = mpc::pluginFolder();
        if (!pf.empty())
        {
            const std::string root = parentDir(parentDir(pf));
            if (!root.empty())
            {
                const std::string dir = root + "/Hildegard Loops";
                MPC_MKDIR(dir.c_str());
                if (writeWav24(uniquePath(dir, file), saveBuf_.L, saveBuf_.R, len, sr)) return;
            }
        }
        // Fallback (SPEC 13.2): Content/Saved/ inside the plugin folder.
        const std::string content = mpc::contentFolder();
        MPC_MKDIR(content.c_str());
        const std::string saved = content + "/Saved";
        MPC_MKDIR(saved.c_str());
        writeWav24(uniquePath(saved, file), saveBuf_.L, saveBuf_.R, len, sr);
    }

    static std::string timestampName()
    {
        std::time_t t = std::time(nullptr);
        std::tm* lt = std::localtime(&t);
        char buf[64];
        if (lt && std::strftime(buf, sizeof(buf), "Hildegard_%Y%m%d_%H%M%S", lt) > 0) return buf;
        return "Hildegard_loop";
    }

    static std::string uniquePath(const std::string& dir, const std::string& base)
    {
        std::string p = dir + "/" + base + ".wav";
        for (int i = 2; fileExistsLocal(p) && i < 1000; ++i) p = dir + "/" + base + "_" + std::to_string(i) + ".wav";
        return p;
    }

    void renderStream(Stream& st, const Chunk& front, int flen, bool advance, float& wetL, float& wetR)
    {
        const float muteGain = st.muteG.process();
        const float volGain  = st.volG.process();
        const float pgl = st.panLG.process();
        const float pgr = st.panRG.process();
        const float gl  = st.gLowG.process();
        const float gh  = st.gHighG.process();
        const float speed = st.speedS.process();

        if (flen <= 0) return;

        if (!st.started)
        {
            st.head = speed >= 0.0f ? st.a : st.b;
            st.dir  = speed >= 0.0f ? 1 : -1;
            st.started = true;
        }

        // Speed near zero holds the head and fades the stream to silence (no clicks/DC).
        const float speedGain = clamp(std::fabs(speed) / 0.02f, 0.0f, 1.0f);

        float sL, sR;
        readStream(st, front, flen, sL, sR);

        // Tilt tone: one-pole split, cheap per-sample; the +-6 dB gains were set per block.
        const float lowL = st.toneLP_L.process(sL); const float highL = sL - lowL;
        const float lowR = st.toneLP_R.process(sR); const float highR = sR - lowR;
        sL = lowL * gl + highL * gh;
        sR = lowR * gl + highR * gh;

        const float g = muteGain * volGain * speedGain;
        wetL += sL * g * pgl;
        wetR += sR * g * pgr;

        if (!advance) return;   // transport stopped: hold the head in place

        // Advance and wrap. Loop wrapping accounts for the end->start overlap crossfade length.
        st.head += (double)std::fabs(speed) * st.dir;
        const double a = st.a, b = st.b, xf = st.xf;
        if (st.mode == 0)   // Loop
        {
            if (st.dir > 0 && st.head >= b)      st.head = (a + xf) + (st.head - b);
            else if (st.dir < 0 && st.head <= a) st.head = (b - xf) - (a - st.head);
        }
        else                // Ping-pong: reflect at the ends (position-continuous, low click)
        {
            if (st.dir > 0 && st.head >= b)      { st.head = b - (st.head - b); st.dir = -1; }
            else if (st.dir < 0 && st.head <= a) { st.head = a + (a - st.head); st.dir = +1; }
        }
        st.head = clamp(st.head, 0.0, (double)(flen - 1));
    }

    // Read one stereo sample. In Loop mode, the last `xf` frames before the region end are
    // equal-power crossfaded with the region start, so the loop point is seamless (SPEC item:
    // ~10% of the loop runtime). sqrt() is a cheap hardware op and only runs inside the fade zone.
    void readStream(const Stream& st, const Chunk& front, int flen, float& outL, float& outR)
    {
        const double hp = st.head, a = st.a, b = st.b, xf = st.xf;
        float mL = sampleAt(front.L, flen, hp);
        float mR = sampleAt(front.R, flen, hp);
        if (st.mode == 0 && xf > 1.0)
        {
            if (st.dir > 0 && hp > b - xf)
            {
                const float t = clamp((float)((hp - (b - xf)) / xf), 0.0f, 1.0f);
                const double wp = a + (hp - (b - xf));
                const float gM = std::sqrt(1.0f - t), gW = std::sqrt(t);
                mL = gM * mL + gW * sampleAt(front.L, flen, wp);
                mR = gM * mR + gW * sampleAt(front.R, flen, wp);
            }
            else if (st.dir < 0 && hp < a + xf)
            {
                const float t = clamp((float)(((a + xf) - hp) / xf), 0.0f, 1.0f);
                const double wp = b - ((a + xf) - hp);
                const float gM = std::sqrt(1.0f - t), gW = std::sqrt(t);
                mL = gM * mL + gW * sampleAt(front.L, flen, wp);
                mR = gM * mR + gW * sampleAt(front.R, flen, wp);
            }
        }
        outL = mL; outR = mR;
    }

    static void equalPowerMix(float mix, float& gDry, float& gWet)
    {
        mix = clamp(mix, 0.0f, 1.0f);
        gDry = std::cos(mix * kHalfPi);
        gWet = std::sin(mix * kHalfPi);
    }

    void snapStreamGains(int s)
    {
        Stream& st = streams_[s];
        const int b = streamBase(s);
        st.speedS.snap(get(b + oSpeed));
        st.volG.snap(volumeGain(get(b + oVolume)));
        float pl, pr; equalPowerPan(clamp(get(b + oPan) * 0.01f, -1.0f, 1.0f), pl, pr);
        st.panLG.snap(pl); st.panRG.snap(pr);
        const float amt = clamp(get(b + oTone) * 0.01f, -1.0f, 1.0f);
        st.gLowG.snap(dbToGain(-6.0f * amt)); st.gHighG.snap(dbToGain(+6.0f * amt));
        st.muteG.snap(getBool(b + oMute) ? 0.0f : 1.0f);
    }

    static mpc::PluginInfo makeInfo()
    {
        mpc::PluginInfo info;
        info.name         = "Hildegard";
        info.vendor       = "Building on Fire";
        info.uniqueId     = MPC_FOURCC('B', 'f', 'H', 'g');   // == plugin.json "uid"
        info.version      = 100;                              // 0.1.0
        info.isInstrument = false;
        info.numInputs    = 2;
        info.numOutputs   = 2;
        info.tailSamples  = 0;
        return info;
    }

    double sampleRate_ = 44100.0;
    Chunk  chunk_[2];
    int    front_ = 0;

    bool recording_ = false, recPrev_ = false;
    int  recLen_ = 0;

    float swapFade_ = 1.0f, swapRamp_ = 0.0f;
    bool  swapRising_ = false;

    // Transport-follow fade (SPEC 3.7).
    float tpGain_ = 0.0f, tpUp_ = 0.0f, tpDown_ = 0.0f;
    bool  wasPlaying_ = false;

    Stream   streams_[kNumStreams];
    Smoother dryS_, wetS_;

    // Save-to-disk (SPEC Section 8): bounce the effected output captured live into saveBuf_.
    std::thread       saveWorker_;
    std::atomic<bool> saveStop_{false};
    std::atomic<int>  saveReq_{-1};   // >=0 = saveBuf_ ready to write, -1 = idle
    bool              savePrev_ = false;
    bool              capturing_ = false;
    int               capLen_ = 0, capIdx_ = 0;
    Chunk             saveBuf_;        // the captured bounce, written by the worker
};

MPC_PLUGIN_ENTRY(Hildegard)
