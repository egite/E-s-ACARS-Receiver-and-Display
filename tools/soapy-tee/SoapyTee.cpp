// SoapyTee: a SoapySDR device that wraps another device and, while the host program
// streams from it, copies a down-converted, decimated slice of the same capture into a
// FIFO as 16-bit I/Q. One radio can then feed two decoders at once: acarsdec (or xng)
// opens driver=tee and owns the radio, dumpvdl2 reads the FIFO with --iq-file.
//
// Device args:
//   driver=tee
//   tee_driver=<real driver, e.g. sdrplay>     plus that driver's own args (serial=...)
//   tee_fifo=<path>        FIFO to write (created if missing); S16LE interleaved I/Q
//   tee_center=<Hz>        centre frequency of the slice
//   tee_rate=<Hz>          sample rate of the slice; the host's rate must be a multiple
//
// Only the CF32 RX stream of channel 0 is teed, which is what acarsdec and xng use.
// Writes never block the host: with no reader, or a reader that falls behind, samples
// for the slice are dropped (and counted), the host stream is unaffected.
#include <SoapySDR/Device.hpp>
#include <SoapySDR/Registry.hpp>
#include <SoapySDR/Formats.hpp>
#include <SoapySDR/Logger.hpp>

#include <cerrno>
#include <cmath>
#include <complex>
#include <condition_variable>
#include <cstdint>
#include <cstring>
#include <deque>
#include <mutex>
#include <thread>
#include <vector>

#include <fcntl.h>
#include <signal.h>
#include <sys/stat.h>
#include <unistd.h>

namespace {

const char *TEE_KEYS[] = {"driver", "tee_driver", "tee_fifo", "tee_center", "tee_rate"};

bool isTeeKey(const std::string &k)
{
    for (const char *t : TEE_KEYS) if (k == t) return true;
    return false;
}

class SoapyTee : public SoapySDR::Device
{
public:
    explicit SoapyTee(const SoapySDR::Kwargs &args)
    {
        SoapySDR::Kwargs inner;
        for (const auto &kv : args) if (!isTeeKey(kv.first)) inner[kv.first] = kv.second;
        if (args.count("tee_driver") == 0) throw std::runtime_error("tee: tee_driver is required");
        inner["driver"] = args.at("tee_driver");
        _fifoPath = args.count("tee_fifo") ? args.at("tee_fifo") : "";
        _teeCenter = args.count("tee_center") ? std::stod(args.at("tee_center")) : 0.0;
        _teeRate = args.count("tee_rate") ? std::stod(args.at("tee_rate")) : 0.0;
        if (_fifoPath.empty() or _teeCenter <= 0 or _teeRate <= 0)
            throw std::runtime_error("tee: tee_fifo, tee_center and tee_rate are required");
        if (access(_fifoPath.c_str(), F_OK) != 0 and mkfifo(_fifoPath.c_str(), 0660) != 0)
            throw std::runtime_error("tee: cannot create FIFO " + _fifoPath + ": " + strerror(errno));
        // A write to a FIFO whose reader has gone raises SIGPIPE; we want EPIPE instead.
        signal(SIGPIPE, SIG_IGN);
        _inner = SoapySDR::Device::make(inner);
        SoapySDR_logf(SOAPY_SDR_INFO, "tee: wrapping %s; slice %.6f MHz at %.0f S/s -> %s",
                      _inner->getDriverKey().c_str(), _teeCenter / 1e6, _teeRate, _fifoPath.c_str());
    }

    ~SoapyTee() override
    {
        stopWorker();
        closeFifo();
        if (_inner) SoapySDR::Device::unmake(_inner);
    }

    // ---- identification
    std::string getDriverKey() const override { return "tee"; }
    std::string getHardwareKey() const override { return _inner->getHardwareKey(); }
    SoapySDR::Kwargs getHardwareInfo() const override { return _inner->getHardwareInfo(); }

    // ---- channels
    size_t getNumChannels(const int dir) const override { return _inner->getNumChannels(dir); }
    SoapySDR::Kwargs getChannelInfo(const int dir, const size_t ch) const override { return _inner->getChannelInfo(dir, ch); }
    bool getFullDuplex(const int dir, const size_t ch) const override { return _inner->getFullDuplex(dir, ch); }

    // ---- stream
    std::vector<std::string> getStreamFormats(const int dir, const size_t ch) const override { return _inner->getStreamFormats(dir, ch); }
    std::string getNativeStreamFormat(const int dir, const size_t ch, double &fs) const override { return _inner->getNativeStreamFormat(dir, ch, fs); }
    SoapySDR::ArgInfoList getStreamArgsInfo(const int dir, const size_t ch) const override { return _inner->getStreamArgsInfo(dir, ch); }

    SoapySDR::Stream *setupStream(const int dir, const std::string &format, const std::vector<size_t> &channels,
                                  const SoapySDR::Kwargs &args) override
    {
        SoapySDR::Stream *s = _inner->setupStream(dir, format, channels, args);
        bool teeable = dir == SOAPY_SDR_RX and format == SOAPY_SDR_CF32 and (channels.empty() or channels[0] == 0);
        if (teeable) _teeStream = s;
        else SoapySDR_logf(SOAPY_SDR_WARNING, "tee: stream is not CF32 RX channel 0; nothing will be teed");
        return s;
    }
    void closeStream(SoapySDR::Stream *s) override
    {
        if (s == _teeStream) { _teeStream = nullptr; stopWorker(); closeFifo(); }
        _inner->closeStream(s);
    }
    size_t getStreamMTU(SoapySDR::Stream *s) const override { return _inner->getStreamMTU(s); }
    int activateStream(SoapySDR::Stream *s, const int flags, const long long timeNs, const size_t numElems) override
    {
        if (s == _teeStream) prepare();
        return _inner->activateStream(s, flags, timeNs, numElems);
    }
    int deactivateStream(SoapySDR::Stream *s, const int flags, const long long timeNs) override
    {
        return _inner->deactivateStream(s, flags, timeNs);
    }
    int readStream(SoapySDR::Stream *s, void *const *buffs, const size_t numElems, int &flags,
                   long long &timeNs, const long timeoutUs) override
    {
        int n = _inner->readStream(s, buffs, numElems, flags, timeNs, timeoutUs);
        if (n > 0 and s == _teeStream and _ready) enqueue(static_cast<const std::complex<float> *>(buffs[0]), n);
        return n;
    }

    // ---- antenna
    std::vector<std::string> listAntennas(const int dir, const size_t ch) const override { return _inner->listAntennas(dir, ch); }
    void setAntenna(const int dir, const size_t ch, const std::string &name) override { _inner->setAntenna(dir, ch, name); }
    std::string getAntenna(const int dir, const size_t ch) const override { return _inner->getAntenna(dir, ch); }

    // ---- gain
    std::vector<std::string> listGains(const int dir, const size_t ch) const override { return _inner->listGains(dir, ch); }
    bool hasGainMode(const int dir, const size_t ch) const override { return _inner->hasGainMode(dir, ch); }
    void setGainMode(const int dir, const size_t ch, const bool automatic) override { _inner->setGainMode(dir, ch, automatic); }
    bool getGainMode(const int dir, const size_t ch) const override { return _inner->getGainMode(dir, ch); }
    void setGain(const int dir, const size_t ch, const double value) override { _inner->setGain(dir, ch, value); }
    void setGain(const int dir, const size_t ch, const std::string &name, const double value) override { _inner->setGain(dir, ch, name, value); }
    double getGain(const int dir, const size_t ch) const override { return _inner->getGain(dir, ch); }
    double getGain(const int dir, const size_t ch, const std::string &name) const override { return _inner->getGain(dir, ch, name); }
    SoapySDR::Range getGainRange(const int dir, const size_t ch) const override { return _inner->getGainRange(dir, ch); }
    SoapySDR::Range getGainRange(const int dir, const size_t ch, const std::string &name) const override { return _inner->getGainRange(dir, ch, name); }

    // ---- frequency
    void setFrequency(const int dir, const size_t ch, const double f, const SoapySDR::Kwargs &args) override
    {
        _inner->setFrequency(dir, ch, f, args);
        if (dir == SOAPY_SDR_RX and ch == 0) prepare();
    }
    void setFrequency(const int dir, const size_t ch, const std::string &name, const double f, const SoapySDR::Kwargs &args) override
    {
        _inner->setFrequency(dir, ch, name, f, args);
        if (dir == SOAPY_SDR_RX and ch == 0) prepare();
    }
    double getFrequency(const int dir, const size_t ch) const override { return _inner->getFrequency(dir, ch); }
    double getFrequency(const int dir, const size_t ch, const std::string &name) const override { return _inner->getFrequency(dir, ch, name); }
    std::vector<std::string> listFrequencies(const int dir, const size_t ch) const override { return _inner->listFrequencies(dir, ch); }
    SoapySDR::RangeList getFrequencyRange(const int dir, const size_t ch) const override { return _inner->getFrequencyRange(dir, ch); }
    SoapySDR::RangeList getFrequencyRange(const int dir, const size_t ch, const std::string &name) const override { return _inner->getFrequencyRange(dir, ch, name); }
    SoapySDR::ArgInfoList getFrequencyArgsInfo(const int dir, const size_t ch) const override { return _inner->getFrequencyArgsInfo(dir, ch); }

    // ---- sample rate / bandwidth / correction
    void setSampleRate(const int dir, const size_t ch, const double rate) override
    {
        _inner->setSampleRate(dir, ch, rate);
        if (dir == SOAPY_SDR_RX and ch == 0) prepare();
    }
    double getSampleRate(const int dir, const size_t ch) const override { return _inner->getSampleRate(dir, ch); }
    std::vector<double> listSampleRates(const int dir, const size_t ch) const override { return _inner->listSampleRates(dir, ch); }
    SoapySDR::RangeList getSampleRateRange(const int dir, const size_t ch) const override { return _inner->getSampleRateRange(dir, ch); }
    void setBandwidth(const int dir, const size_t ch, const double bw) override { _inner->setBandwidth(dir, ch, bw); }
    double getBandwidth(const int dir, const size_t ch) const override { return _inner->getBandwidth(dir, ch); }
    std::vector<double> listBandwidths(const int dir, const size_t ch) const override { return _inner->listBandwidths(dir, ch); }
    SoapySDR::RangeList getBandwidthRange(const int dir, const size_t ch) const override { return _inner->getBandwidthRange(dir, ch); }
    bool hasFrequencyCorrection(const int dir, const size_t ch) const override { return _inner->hasFrequencyCorrection(dir, ch); }
    void setFrequencyCorrection(const int dir, const size_t ch, const double v) override { _inner->setFrequencyCorrection(dir, ch, v); }
    double getFrequencyCorrection(const int dir, const size_t ch) const override { return _inner->getFrequencyCorrection(dir, ch); }
    bool hasDCOffsetMode(const int dir, const size_t ch) const override { return _inner->hasDCOffsetMode(dir, ch); }
    void setDCOffsetMode(const int dir, const size_t ch, const bool automatic) override { _inner->setDCOffsetMode(dir, ch, automatic); }
    bool getDCOffsetMode(const int dir, const size_t ch) const override { return _inner->getDCOffsetMode(dir, ch); }

    // ---- settings
    SoapySDR::ArgInfoList getSettingInfo() const override { return _inner->getSettingInfo(); }
    void writeSetting(const std::string &key, const std::string &value) override { _inner->writeSetting(key, value); }
    std::string readSetting(const std::string &key) const override { return _inner->readSetting(key); }
    SoapySDR::ArgInfoList getSettingInfo(const int dir, const size_t ch) const override { return _inner->getSettingInfo(dir, ch); }
    void writeSetting(const int dir, const size_t ch, const std::string &key, const std::string &value) override { _inner->writeSetting(dir, ch, key, value); }
    std::string readSetting(const int dir, const size_t ch, const std::string &key) const override { return _inner->readSetting(dir, ch, key); }

private:
    // ---- hand-off from the host's read thread to the slice worker
    void enqueue(const std::complex<float> *in, size_t n)
    {
        std::lock_guard<std::mutex> lock(_qMutex);
        if (_queue.size() >= MAX_QUEUED) { _dropped += n / _decim; return; }  // worker is behind
        _queue.emplace_back(in, in + n);
        _qCond.notify_one();
    }

    void startWorker()
    {
        if (_worker.joinable()) return;
        _stop = false;
        _worker = std::thread([this] {
            std::vector<std::complex<float>> block;
            for (;;)
            {
                {
                    std::unique_lock<std::mutex> lock(_qMutex);
                    _qCond.wait(lock, [this] { return _stop or !_queue.empty(); });
                    if (_stop) return;
                    block.swap(_queue.front());
                    _queue.pop_front();
                }
                tee(block.data(), block.size());
            }
        });
    }

    void stopWorker()
    {
        {
            std::lock_guard<std::mutex> lock(_qMutex);
            _stop = true;
        }
        _qCond.notify_all();
        if (_worker.joinable()) _worker.join();
        std::lock_guard<std::mutex> lock(_qMutex);
        _queue.clear();
    }

    // Work out the mixer and decimator for the host's current rate and centre frequency.
    void prepare()
    {
        std::lock_guard<std::mutex> lock(_dspMutex);
        _ready = false;
        double fs = _inner->getSampleRate(SOAPY_SDR_RX, 0);
        double fc = _inner->getFrequency(SOAPY_SDR_RX, 0);
        if (fs <= 0 or fc <= 0) return;
        double ratio = fs / _teeRate;
        _decim = static_cast<unsigned>(std::lround(ratio));
        if (_decim < 1 or std::fabs(ratio - _decim) > 1e-6)
        {
            // Hosts tune and set the rate in some order, so this is normal until the last
            // call lands; it only matters if it is still the case when streaming starts.
            SoapySDR_logf(SOAPY_SDR_INFO, "tee: host rate %.0f is not a multiple of tee_rate %.0f; "
                          "slice off until it is", fs, _teeRate);
            return;
        }
        double offset = _teeCenter - fc;
        if (std::fabs(offset) + 0.4 * _teeRate > fs / 2)
            SoapySDR_logf(SOAPY_SDR_WARNING, "tee: slice at %+.0f Hz from centre reaches past the capture edge", offset);
        _phaseStep = std::polar(1.0, -2.0 * M_PI * offset / fs);
        _phase = 1.0;
        // Windowed-sinc low-pass at 0.4 x slice rate: the slice's band is well inside, and
        // what aliases onto it after decimation lies deep in the stop band. Taps are kept
        // in reverse order so the inner loop is a plain dot product the compiler vectorises.
        size_t taps = 8 * _decim + 1;
        _taps.assign(taps, 0.0f);
        double cutoff = 0.4 * _teeRate / fs, sum = 0;  // cycles per input sample
        for (size_t i = 0; i < taps; i++)
        {
            double n = static_cast<double>(i) - (taps - 1) / 2.0;
            double sinc = n == 0 ? 2 * cutoff : std::sin(2 * M_PI * cutoff * n) / (M_PI * n);
            double w = 0.54 - 0.46 * std::cos(2 * M_PI * i / (taps - 1));
            _taps[i] = static_cast<float>(sinc * w);
            sum += _taps[i];
        }
        for (auto &t : _taps) t = static_cast<float>(t / sum);
        _histRe.assign(taps - 1, 0.0f);
        _histIm.assign(taps - 1, 0.0f);
        _countdown = 0;
        _dropped = _written = _lastDropped = 0;
        _ready = true;
        startWorker();
        SoapySDR_logf(SOAPY_SDR_INFO, "tee: %.0f S/s capture at %.6f MHz, slice offset %+.0f Hz, decimate by %u, %zu taps",
                      fs, fc / 1e6, offset, _decim, taps);
    }

    void tee(const std::complex<float> *in, size_t n)
    {
        std::lock_guard<std::mutex> lock(_dspMutex);
        if (!_ready) return;
        // Mix the block down so the slice sits at DC, into split real/imaginary arrays
        // with the filter history in front. The oscillator runs in double and is
        // re-normalised now and then so it can't drift in amplitude.
        const size_t h = _histRe.size();
        _workRe.resize(h + n);
        _workIm.resize(h + n);
        std::copy(_histRe.begin(), _histRe.end(), _workRe.begin());
        std::copy(_histIm.begin(), _histIm.end(), _workIm.begin());
        for (size_t i = 0; i < n; i++)
        {
            const float pr = static_cast<float>(_phase.real()), pi = static_cast<float>(_phase.imag());
            const float xr = in[i].real(), xi = in[i].imag();
            _workRe[h + i] = xr * pr - xi * pi;
            _workIm[h + i] = xr * pi + xi * pr;
            _phase *= _phaseStep;
        }
        if ((_renorm += n) > (1u << 20)) { _phase /= std::abs(_phase); _renorm = 0; }

        // Filter only at the output instants: every _decim-th input sample. The filter is
        // symmetric, so the tap order doesn't matter and the loop is a straight dot product.
        _out.clear();
        const size_t taps = _taps.size();
        const float *hp = _taps.data();
        for (size_t i = _countdown; i < n; i += _decim)
        {
            const float *xr = &_workRe[i], *xi = &_workIm[i];
            float accRe = 0, accIm = 0;
            for (size_t k = 0; k < taps; k++) { accRe += xr[k] * hp[k]; accIm += xi[k] * hp[k]; }
            _out.push_back(toS16(accRe));
            _out.push_back(toS16(accIm));
        }
        if (n > _countdown) _countdown = _decim - ((n - _countdown) % _decim);
        else _countdown -= n;
        if (_countdown == _decim) _countdown = 0;
        std::copy(_workRe.end() - h, _workRe.end(), _histRe.begin());
        std::copy(_workIm.end() - h, _workIm.end(), _histIm.begin());
        if (!_out.empty()) emit();
    }

    static int16_t toS16(float v)
    {
        float s = v * 32767.0f;
        return static_cast<int16_t>(s > 32767.0f ? 32767.0f : (s < -32768.0f ? -32768.0f : s));
    }

    void emit()
    {
        if (_fd < 0)
        {
            // Try the FIFO roughly once a second; without a reader the open fails with ENXIO.
            if (++_openTries % 64 != 1) { _dropped += _out.size() / 2; return; }
            _fd = open(_fifoPath.c_str(), O_WRONLY | O_NONBLOCK);
            if (_fd < 0) { _dropped += _out.size() / 2; return; }
            fcntl(_fd, F_SETPIPE_SZ, 1 << 20);
            SoapySDR_logf(SOAPY_SDR_INFO, "tee: reader connected on %s", _fifoPath.c_str());
            _openTries = 0;
        }
        const char *p = reinterpret_cast<const char *>(_out.data());
        size_t left = _out.size() * sizeof(int16_t);
        while (left > 0)
        {
            ssize_t w = write(_fd, p, left);
            if (w > 0) { p += w; left -= w; continue; }
            if (w < 0 and errno == EAGAIN) { _dropped += left / 4; break; }  // reader behind: drop the rest
            if (w < 0 and errno == EINTR) continue;
            SoapySDR_logf(SOAPY_SDR_WARNING, "tee: reader on %s went away (%s)", _fifoPath.c_str(), strerror(errno));
            closeFifo();
            break;
        }
        _written += _out.size() / 2;
        if (_dropped != _lastDropped and (_written - _lastReport) > 10 * static_cast<size_t>(_teeRate))
        {
            SoapySDR_logf(SOAPY_SDR_WARNING, "tee: dropped %zu slice samples so far (no reader, or reader too slow)", _dropped);
            _lastReport = _written;
            _lastDropped = _dropped;
        }
    }

    void closeFifo()
    {
        if (_fd >= 0) close(_fd);
        _fd = -1;
    }

    SoapySDR::Device *_inner = nullptr;
    SoapySDR::Stream *_teeStream = nullptr;
    std::string _fifoPath;
    double _teeCenter = 0, _teeRate = 0;
    int _fd = -1;
    unsigned _openTries = 0;

    bool _ready = false;
    unsigned _decim = 1, _renorm = 0;
    size_t _countdown = 0;
    std::complex<double> _phase{1.0}, _phaseStep{1.0};
    std::vector<float> _taps;
    std::vector<float> _histRe, _histIm, _workRe, _workIm;
    std::vector<int16_t> _out;
    size_t _dropped = 0, _written = 0, _lastReport = 0, _lastDropped = 0;

    static constexpr size_t MAX_QUEUED = 64;  // blocks; at the host's MTU this is well under a second
    std::deque<std::vector<std::complex<float>>> _queue;
    std::mutex _qMutex, _dspMutex;
    std::condition_variable _qCond;
    std::thread _worker;
    bool _stop = false;
};

std::vector<SoapySDR::Kwargs> findTee(const SoapySDR::Kwargs &args)
{
    std::vector<SoapySDR::Kwargs> results;
    if (args.count("driver") and args.at("driver") == "tee" and args.count("tee_driver"))
    {
        SoapySDR::Kwargs r = args;
        r["label"] = "Tee over " + args.at("tee_driver");
        results.push_back(r);
    }
    return results;
}

SoapySDR::Device *makeTee(const SoapySDR::Kwargs &args) { return new SoapyTee(args); }

SoapySDR::Registry registerTee("tee", &findTee, &makeTee, SOAPY_SDR_ABI_VERSION);

}  // namespace
