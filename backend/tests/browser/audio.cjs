async function installAudio(context, mode = 'ready') {
    await context.addInitScript(selectedMode => {
        const state = { requests: 0, stopped: 0, closed: 0, workletURLs: [], sockets: [] };
        window.__audioMock = state;
        const track = {
            ended: false,
            stop() {
                if (!this.ended) state.stopped += 1;
                this.ended = true;
            },
        };
        state.disconnectMicrophone = () => track.onended?.();
        const stream = { getTracks: () => [track], getAudioTracks: () => [track] };
        Object.defineProperty(navigator.mediaDevices, 'getUserMedia', {
            configurable: true,
            value: async constraints => {
                state.requests += 1;
                state.constraints = constraints;
                if (selectedMode === 'denied') throw new DOMException('Mock permission denial', 'NotAllowedError');
                if (selectedMode === 'missing') throw new DOMException('Mock missing device', 'NotFoundError');
                return stream;
            },
        });
        class MockAudioContext {
            constructor() {
                this.state = 'suspended';
                this.destination = {};
                this.audioWorklet = { addModule: async url => {
                    state.workletURLs.push(url);
                    if (selectedMode === 'worklet-error') throw new Error('Mock worklet failure');
                } };
            }
            async resume() { this.state = 'running'; }
            async close() {
                if (this.state !== 'closed') state.closed += 1;
                this.state = 'closed';
            }
            createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
        }
        class MockWorklet {
            constructor() {
                state.worklet = this;
                this.port = { postMessage: () => {
                    queueMicrotask(() => this.port.onmessage?.({ data: { type: 'flushed' } }));
                } };
            }
            connect() {}
            disconnect() {}
        }
        class MockSocket {
            static OPEN = 1;
            constructor(url) {
                this.url = String(url);
                this.readyState = 0;
                this.bufferedAmount = 0;
                state.sockets.push(this);
                queueMicrotask(() => {
                    if (selectedMode === 'socket-error') this.onerror?.();
                    else {
                        this.readyState = 1;
                        this.message({ type: 'ready', sample_rate: 16000 });
                    }
                });
            }
            message(payload) { this.onmessage?.({ data: JSON.stringify(payload) }); }
            send(payload) {
                if (typeof payload === 'string' && JSON.parse(payload).type === 'stop') {
                    queueMicrotask(() => {
                        this.message({ type: 'transcript', segment_id: 1, text: '\u06af\u0641\u062a\u0627\u0631 \u0622\u0632\u0645\u0627\u06cc\u0634\u06cc', is_final: true });
                        this.message({ type: 'done' });
                    });
                }
            }
            close() {
                if (this.readyState === 3) return;
                this.readyState = 3;
                queueMicrotask(() => this.onclose?.());
            }
        }
        Object.defineProperty(window, 'AudioContext', { configurable: true, value: MockAudioContext });
        Object.defineProperty(window, 'AudioWorkletNode', {
            configurable: true, value: selectedMode === 'unsupported' ? undefined : MockWorklet,
        });
        Object.defineProperty(window, 'WebSocket', { configurable: true, value: MockSocket });
        Object.defineProperty(window, 'speechSynthesis', {
            configurable: true, value: { cancel() {}, getVoices: () => [], speak() {} },
        });
        state.played = 0;
        HTMLMediaElement.prototype.play = function () {
            state.played += 1;
            if (selectedMode === 'autoplay-denied' && state.played === 1) {
                return Promise.reject(new DOMException('User gesture needed', 'NotAllowedError'));
            }
            if (selectedMode !== 'keep-playing') setTimeout(() => this.dispatchEvent(new Event('ended')), 50);
            return Promise.resolve();
        };
        HTMLMediaElement.prototype.pause = function () {};
        HTMLMediaElement.prototype.load = function () {};
        state.packet = amplitude => {
            const samples = new Int16Array(1600).fill(Math.round(amplitude * 32767));
            state.worklet.port.onmessage?.({data: {type: 'audio', buffer: samples.buffer}});
        };
    }, mode);
}

module.exports = { installAudio };
