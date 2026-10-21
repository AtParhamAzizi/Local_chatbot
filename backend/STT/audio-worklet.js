class PcmRecorder extends AudioWorkletProcessor {
    constructor() {
        super();
        this.ratio = sampleRate / 16000;
        this.weight = 0;
        this.sum = 0;
        this.buffer = new ArrayBuffer(3200);
        this.view = new DataView(this.buffer);
        this.offset = 0;
        this.stopped = false;
        this.port.onmessage = event => {
            if (event.data.type !== 'stop' || this.stopped) return;
            this.stopped = true;
            if (this.weight > 0) this.writeSample(this.sum / this.weight);
            this.flush();
            this.port.postMessage({type: 'flushed'});
        };
    }

    writeSample(sample) {
        const clamped = Math.max(-1, Math.min(1, sample));
        this.view.setInt16(this.offset, Math.round(clamped * (clamped < 0 ? 32768 : 32767)), true);
        this.offset += 2;
        if (this.offset === this.buffer.byteLength) this.flush();
    }

    flush() {
        if (!this.offset) return;
        const buffer = this.buffer.slice(0, this.offset);
        this.port.postMessage({type: 'audio', buffer}, [buffer]);
        this.offset = 0;
    }

    process(inputs) {
        if (this.stopped) return false;
        const channels = inputs[0];
        if (!channels?.length) return true;
        for (let index = 0; index < channels[0].length; index++) {
            let sample = 0;
            for (const channel of channels) sample += channel[index] / channels.length;
            let remaining = 1;
            while (remaining > 1e-8) {
                const weight = Math.min(remaining, this.ratio - this.weight);
                this.sum += sample * weight;
                this.weight += weight;
                remaining -= weight;
                if (this.weight >= this.ratio - 1e-8) {
                    this.writeSample(this.sum / this.ratio);
                    this.weight = 0;
                    this.sum = 0;
                }
            }
        }
        return true;
    }
}

registerProcessor('pcm-recorder', PcmRecorder);
