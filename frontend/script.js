// Configuration - Update this URL if your backend runs on a different port
        const BACKEND_URL = location.protocol === 'file:' ? 'http://localhost:5000' : location.origin;
        const USER_ID = 'user_' + Math.random().toString(36).substr(2, 9);

        // DOM elements
        const chatMessages = document.getElementById('chatMessages');
        const messageInput = document.getElementById('messageInput');
        const sendButton = document.getElementById('sendButton');
        const voiceButton = document.getElementById('voiceButton');
        const voiceStatus = document.getElementById('voiceStatus');
        const voiceReplies = document.getElementById('voiceReplies');
        const connectionStatus = document.getElementById('connectionStatus');
        let voiceReady = false;
        let voiceSession = null;
        let chatBusy = false;


        // const quickQuestions = document.getElementById('quick-questions');
        // const quickQuestionsbtn = document.getElementsByClassName('quick-question-btn');

// Check backend connection on page load
        checkBackendConnection();
        checkVoiceBackend();

        // Function to check backend connection
        async function checkBackendConnection() {
            try {
                const response = await fetch(`${BACKEND_URL}/health`);
                if (response.ok) {
                    connectionStatus.className = 'connection-status connected';
                    console.log('Connected to Python backend');
                } else {
                    throw new Error('Backend not healthy');
                }
            } catch (error) {
                connectionStatus.className = 'connection-status error';
                console.error('Cannot connect to Python backend:', error);

                // Show connection error message
                addMessage("⚠️ ارتباط شما با سرور قطع شده است", false);
            }
        }

        async function checkVoiceBackend() {
            if (!voiceButton || voiceSession) return;
            if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia ||
                !window.AudioWorkletNode || !(window.AudioContext || window.webkitAudioContext) ||
                location.protocol === 'file:') {
                setVoiceStatus('برای ضبط صدا، صفحه را در localhost یا HTTPS و یک مرورگر سازگار باز کنید.', true);
                return;
            }
            try {
                const response = await fetch(`${BACKEND_URL}/voice/health`, {signal: AbortSignal.timeout(5000)});
                const data = await response.json();
                if (voiceSession) return;
                voiceReady = response.ok && data.status === 'ready';
                updateVoiceControls();
                if (!voiceReady) {
                    setVoiceStatus(data.status === 'loading' ? 'مدل گفتار در حال بارگذاری است...' :
                        `گفت‌وگوی صوتی آماده نیست: ${data.error || 'گزارش سرور را بررسی کنید.'}`, data.status !== 'loading');
                } else if (!voiceStatus?.textContent || voiceStatus.dataset.waiting === 'true') {
                    setVoiceStatus('روی میکروفون بزنید؛ پس از صحبت، دوباره بزنید تا پیام ارسال شود.');
                }
                if (voiceStatus) voiceStatus.dataset.waiting = String(!voiceReady);
            } catch (error) {
                if (voiceSession) return;
                voiceReady = false;
                updateVoiceControls();
                setVoiceStatus('اتصال به سرویس گفتار برقرار نشد؛ نوشتن پیام همچنان در دسترس است.', true);
                if (voiceStatus) voiceStatus.dataset.waiting = 'true';
            }
        }





//&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&
        // Function to add a message to the chat
        // function addMessage(content, isUser) {
        //     const messageDiv = document.createElement('div');
        //     messageDiv.className = `message ${isUser ? 'user-message' : 'bot-message'}`;

        //     if (!isUser) {

        //         chatMessages.appendChild(messageDiv);

        //         let index = 0;
        //         const typingSpeed = 30;

        //         function typeChar() {
        //             if (index < content.length) {
        //                 messageDiv.textContent += content.charAt(index);
        //                 index++;
        //                 chatMessages.scrollTop = chatMessages.scrollHeight;
        //                 setTimeout(typeChar, typingSpeed);
        //             }
        //         }

        //         typeChar();
        //     }else {
        //         messageDiv.textContent = content;
        //         chatMessages.appendChild(messageDiv);
        //     }
        // }

        function addMessage(content, isUser, messageId = null) {
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${isUser ? 'user-message' : 'bot-message'}`;

            const textSpan = document.createElement('span');
            textSpan.className = 'message-text';
            messageDiv.appendChild(textSpan);
            chatMessages.appendChild(messageDiv);

            if (!isUser) {
                let index = 0;
                const typingSpeed = 30;

                function typeChar() {
                    if (index < content.length) {
                        textSpan.textContent += content.charAt(index);
                        index++;
                        chatMessages.scrollTop = chatMessages.scrollHeight;
                        setTimeout(typeChar, typingSpeed);
                    } else {
                        if (messageId) {
                            addFeedbackButtons(messageDiv, messageId);
                        }
                    }
                }

                typeChar();
            } else {
                textSpan.textContent = content;
                chatMessages.scrollTop = chatMessages.scrollHeight;
            }
        }
//&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&




        // Function to show typing indicator
        function showTyping() {
            const typingDiv = document.createElement('div');
            typingDiv.className = 'typing';
            typingDiv.id = 'typingIndicator';

            typingDiv.innerHTML = `
                در حال یافتن بهترین پاسخ
                <div class="typing-dots">
                    <span></span>
                    <span></span>
                    <span></span>
                </div>
            `;

            chatMessages.appendChild(typingDiv);
            chatMessages.scrollTop = chatMessages.scrollHeight;
        }

        // Function to hide typing indicator
        function hideTyping() {
            const typingIndicator = document.getElementById('typingIndicator');
            if (typingIndicator) {
                typingIndicator.remove();
            }
        }






//&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&
function addFeedbackButtons(messageDiv, messageId) {
    const feedbackDiv = document.createElement('div');
    feedbackDiv.className = 'feedback-buttons';
    feedbackDiv.innerHTML = `
        <button type="button" class="feedback-btn like-btn" data-feedback="like" aria-pressed="false" title="پاسخ مفید بود">👍</button>
        <button type="button" class="feedback-btn dislike-btn" data-feedback="dislike" aria-pressed="false" title="پاسخ مفید نبود">👎</button>
    `;

    const feedbackStatus = document.createElement('span');
    feedbackStatus.className = 'feedback-status';
    feedbackStatus.setAttribute('role', 'status');
    feedbackDiv.appendChild(feedbackStatus);

    const buttons = feedbackDiv.querySelectorAll('.feedback-btn');
    buttons.forEach(btn => {
        btn.addEventListener('click', async () => {
            if (btn.classList.contains('selected')) return;

            const feedback = btn.dataset.feedback;
            buttons.forEach(button => { button.disabled = true; });
            feedbackStatus.textContent = 'در حال ذخیره بازخورد...';
            feedbackStatus.classList.remove('error');

            try {
                await sendFeedback(messageId, feedback);
                buttons.forEach(button => {
                    button.classList.toggle('selected', button === btn);
                    button.setAttribute('aria-pressed', String(button === btn));
                });
                feedbackStatus.textContent = 'بازخورد شما ثبت شد.';
            } catch (error) {
                feedbackStatus.textContent = 'ثبت بازخورد ناموفق بود. دوباره تلاش کنید.';
                feedbackStatus.classList.add('error');
                console.error('Feedback error:', error);
            } finally {
                buttons.forEach(button => { button.disabled = false; });
            }
        });
    });

    messageDiv.appendChild(feedbackDiv);
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

async function sendFeedback(messageId, feedback) {
    const response = await fetch(`${BACKEND_URL}/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            message_id: messageId,
            feedback: feedback
        })
    });
    const data = await response.json();
    if (!response.ok || data?.status !== 'ok') {
        throw new Error(data?.error || 'Unable to save feedback');
    }
    return data;
}



//&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&


function releaseVoiceMicrophone(session) {
    session.captureClosed = true;
    session.source?.disconnect();
    session.worklet?.disconnect();
    session.stream?.getTracks().forEach(track => track.stop());
    if (session.context && session.context.state !== 'closed') {
        return session.context.close().catch(() => {});
    }
    return Promise.resolve();
}

function updateVoiceControls() {
    if (!voiceButton) return;
    const recording = voiceSession?.state === 'recording';
    voiceButton.classList.toggle('recording', recording);
    voiceButton.setAttribute('aria-pressed', String(recording));
    voiceButton.disabled = !voiceReady || chatBusy || (!recording && voiceSession !== null);
    const label = recording ? 'توقف ضبط و ارسال پیام' :
        voiceSession?.state === 'connecting' ? 'در حال اتصال...' :
        voiceSession?.state === 'finishing' ? 'در حال تکمیل متن...' : 'شروع ضبط صدا';
    voiceButton.title = label;
    voiceButton.setAttribute('aria-label', label);
    messageInput.disabled = chatBusy || Boolean(voiceSession);
    sendButton.disabled = chatBusy || Boolean(voiceSession);
}

function failVoiceSession(session, error) {
    const active = voiceSession === session;
    if (active) voiceSession = null;
    clearTimeout(session.finishTimer);
    void releaseVoiceMicrophone(session);
    session.socket?.close();
    if (!active) return;
    updateVoiceControls();
    setVoiceStatus(`ضبط قطع شد: ${error.message} متن را بررسی و در صورت نیاز ارسال کنید.`, true);
}

function setVoiceStatus(text, isError = false) {
    if (!voiceStatus) return;
    if (voiceStatus.textContent !== text) voiceStatus.textContent = text;
    voiceStatus.classList.toggle('error', isError);
}

function speakVoiceReply(text) {
    if (!text || !voiceReplies?.checked || !('speechSynthesis' in window)) return;
    try {
        const voices = window.speechSynthesis.getVoices().filter(candidate => candidate.lang.toLowerCase().startsWith('fa'));
        const voice = voices.find(candidate => candidate.localService) || voices[0];
        if (!voice) {
            setVoiceStatus('صدای فارسی در این مرورگر موجود نیست؛ پاسخ به صورت متن نمایش داده شد.');
            return;
        }
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.voice = voice;
        utterance.lang = voice.lang;
        utterance.rate = 0.95;
        utterance.onerror = event => {
            if (!['canceled', 'interrupted'].includes(event.error)) {
                setVoiceStatus('پخش صدا انجام نشد؛ پاسخ متنی در دسترس است.', true);
            }
        };
        window.speechSynthesis.speak(utterance);
    } catch (error) {
        setVoiceStatus('پخش صدا انجام نشد؛ پاسخ متنی در دسترس است.', true);
    }
}

function voiceTranscript(session, finalOnly = false) {
    return Array.from(session.segments.entries())
        .sort((left, right) => left[0] - right[0])
        .filter(([, segment]) => !finalOnly || segment.isFinal)
        .map(([, segment]) => segment.text).filter(Boolean).join(' ').trim();
}

function voiceMessage(session, transcript) {
    return transcript ? [session.draft.trim(), transcript].filter(Boolean).join(' ') : session.draft;
}

function connectVoiceStream(session) {
    return new Promise((resolve, reject) => {
        const streamUrl = new URL('/voice-stream', BACKEND_URL);
        streamUrl.protocol = streamUrl.protocol === 'https:' ? 'wss:' : 'ws:';
        const socket = new WebSocket(streamUrl);
        session.socket = socket;
        let ready = false;
        const timeout = setTimeout(() => {
            reject(new Error('زمان اتصال به سرور گفتار به پایان رسید.'));
            socket.close();
        }, 15000);

        const fail = error => {
            clearTimeout(timeout);
            if (!ready) reject(error);
            else failVoiceSession(session, error);
        };

        socket.onmessage = event => {
            if (voiceSession !== session) return;
            try {
                const data = JSON.parse(event.data);
                if (data.type === 'ready') {
                    if (data.sample_rate !== 16000) throw new Error('نرخ نمونه‌برداری سرور سازگار نیست.');
                    ready = true;
                    clearTimeout(timeout);
                    resolve();
                } else if (data.type === 'transcript') {
                    session.segments.set(data.segment_id, {text: data.text || '', isFinal: data.is_final === true});
                    messageInput.value = voiceMessage(session, voiceTranscript(session));
                } else if (data.type === 'done') {
                    clearTimeout(session.finishTimer);
                    voiceSession = null;
                    void releaseVoiceMicrophone(session);
                    socket.close();
                    updateVoiceControls();
                    const transcript = voiceTranscript(session, true);
                    messageInput.value = voiceMessage(session, transcript);
                    setVoiceStatus(transcript ? 'متن نهایی ارسال شد.' : 'گفتاری تشخیص داده نشد؛ دوباره تلاش کنید.');
                    if (transcript) void sendMessage(messageInput.value, true);
                } else if (data.type === 'error') {
                    fail(new Error(data.message || 'تبدیل گفتار به متن ناموفق بود.'));
                }
            } catch (error) {
                fail(error);
            }
        };
        socket.onerror = () => fail(new Error('اتصال زنده به سرور برقرار نشد.'));
        socket.onclose = () => {
            clearTimeout(timeout);
            const error = new Error('اتصال به سرور گفتار قطع شد.');
            if (!ready) reject(error);
            else if (voiceSession === session) failVoiceSession(session, error);
        };
    });
}

async function startVoiceRecording() {
    if (!voiceButton || voiceSession || !voiceReady || chatBusy) return;
    window.speechSynthesis?.cancel();
    const session = {state: 'connecting', segments: new Map(), captureClosed: false, draft: messageInput.value};
    voiceSession = session;
    updateVoiceControls();
    setVoiceStatus('در حال اتصال به میکروفون و سرور...');

    try {
        if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
            throw new Error('برای دسترسی به میکروفون از localhost یا HTTPS استفاده کنید.');
        }
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextClass || !window.AudioWorkletNode) {
            throw new Error('این مرورگر از ضبط زنده پشتیبانی نمی‌کند.');
        }
        session.context = new AudioContextClass({latencyHint: 'interactive'});
        await session.context.resume();
        session.stream = await navigator.mediaDevices.getUserMedia({
            audio: {channelCount: 1, echoCancellation: true, noiseSuppression: true},
        });
        if (voiceSession !== session) {
            await releaseVoiceMicrophone(session);
            return;
        }
        session.stream.getAudioTracks().forEach(track => {
            track.onended = () => {
                if (!session.captureClosed) failVoiceSession(session, new Error('میکروفون قطع شد.'));
            };
        });
        await session.context.audioWorklet.addModule(`${BACKEND_URL}/voice/audio-worklet.js`);
        if (voiceSession !== session) return;
        await connectVoiceStream(session);
        if (voiceSession !== session) return;

        session.worklet = new AudioWorkletNode(session.context, 'pcm-recorder', {
            numberOfInputs: 1,
            numberOfOutputs: 1,
            channelCount: 1,
            channelCountMode: 'explicit',
        });
        session.worklet.port.onmessage = event => {
            if (voiceSession !== session) return;
            if (event.data.type === 'flushed') {
                session.onFlushed?.();
                return;
            }
            if (event.data.type !== 'audio') return;
            if (session.socket.readyState !== WebSocket.OPEN || session.socket.bufferedAmount > 1024 * 1024) {
                failVoiceSession(session, new Error('ارسال صدا کند شده است؛ دوباره تلاش کنید.'));
                return;
            }
            session.socket.send(event.data.buffer);
        };
        session.worklet.onprocessorerror = () => failVoiceSession(session, new Error('پردازش صدای میکروفون متوقف شد.'));
        session.source = session.context.createMediaStreamSource(session.stream);
        session.source.connect(session.worklet);
        session.worklet.connect(session.context.destination);
        session.state = 'recording';
        setVoiceStatus('در حال شنیدن؛ برای توقف و ارسال پیام، دوباره روی میکروفون بزنید.');
        updateVoiceControls();
    } catch (error) {
        if (error.name === 'NotAllowedError') error = new Error('اجازه دسترسی به میکروفون را در مرورگر فعال کنید.');
        if (error.name === 'NotFoundError') error = new Error('میکروفونی پیدا نشد.');
        failVoiceSession(session, error);
    }
}

async function stopVoiceRecording() {
    const session = voiceSession;
    if (!session || session.state !== 'recording') return;
    session.state = 'finishing';
    updateVoiceControls();
    setVoiceStatus('در حال تکمیل متن نهایی...');
    try {
        await new Promise((resolve, reject) => {
            const timeout = setTimeout(() => reject(new Error('تخلیه صدای میکروفون انجام نشد.')), 2000);
            session.onFlushed = () => {
                clearTimeout(timeout);
                resolve();
            };
            session.worklet.port.postMessage({type: 'stop'});
        });
        if (voiceSession !== session) return;
        session.socket.send(JSON.stringify({type: 'stop'}));
        session.finishTimer = setTimeout(() => {
            failVoiceSession(session, new Error('تکمیل متن بیش از حد طول کشید.'));
        }, 120000);
        await releaseVoiceMicrophone(session);
    } catch (error) {
        failVoiceSession(session, error);
    }
}

if (voiceButton) {
    voiceButton.addEventListener('click', () => {
        if (voiceSession?.state === 'recording') void stopVoiceRecording();
        else void startVoiceRecording();
    });
}

if (voiceReplies) {
    voiceReplies.disabled = !('speechSynthesis' in window);
    if (voiceReplies.disabled) voiceReplies.checked = false;
    else window.speechSynthesis.getVoices();
    voiceReplies.addEventListener('change', () => {
        if (!voiceReplies.checked) window.speechSynthesis?.cancel();
    });
}

window.addEventListener('pagehide', () => {
    window.speechSynthesis?.cancel();
    const session = voiceSession;
    voiceSession = null;
    if (!session) return;
    clearTimeout(session.finishTimer);
    void releaseVoiceMicrophone(session);
    session.socket?.close();
});

window.addEventListener('pageshow', () => {
    updateVoiceControls();
    void checkVoiceBackend();
});

        // Function to send message to Python backend
        async function sendMessage(messageOverride = null, speakReply = false) {

            if (chatBusy || voiceSession) return;
            const message = (messageOverride ?? messageInput.value).trim();
            if (!message) return;
            window.speechSynthesis?.cancel();



            // Add user message
            addMessage(message, true);
            messageInput.value = '';


            // Disable input while processing
            chatBusy = true;
            messageInput.disabled = true;
            sendButton.disabled = true;
            updateVoiceControls();

            // Show typing indicator
            showTyping();

            try {
                const response = await fetch(`${BACKEND_URL}/chat`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({
                        message: message,
                        user_id: USER_ID
                    })
                });

                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }


                //&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&
                const data = await response.json();

                // hideTyping();
                // addMessage(data.reply, false);
                // if (data.recommended_questions && data.recommended_questions.length > 0) {
                //     showRecommendedQuestions(data.recommended_questions);
                // } else {
                //     document.getElementById('recommendedQuestions').innerHTML = '';
                // }

                hideTyping();
                addMessage(data.reply, false, data.message_id);   // <-- pass message_id
                if (speakReply) speakVoiceReply(data.reply);
                if (data.recommended_questions && data.recommended_questions.length > 0) {
                    showRecommendedQuestions(data.recommended_questions);
                } else {
                    document.getElementById('recommendedQuestions').innerHTML = '';
                }
                //&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&&

            } catch (error) {
                hideTyping();
                addMessage("متاسفانه پاسخ دقیقی برای سوالتون پیدا نشد. برای ارتباط تلفنی میتوانید با شماره 1649 تماس بگیرید.", false);
                console.error('Backend error:', error);
                document.getElementById('recommendedQuestions').innerHTML = '';


                // Update connection status
                connectionStatus.className = 'connection-status error';
            } finally {
                // Re-enable input
                chatBusy = false;
                messageInput.disabled = false;
                sendButton.disabled = false;
                updateVoiceControls();
                messageInput.focus();
            }
        }

        // Event listeners
        sendButton.addEventListener('click', () => { void sendMessage(); });

        messageInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                sendMessage();
            }
        });


        function sendQuickQuestion(text) {
            if (chatBusy || voiceSession) return;
            messageInput.value = text;

            // sendMessage();
        }


        // Function to display recommended questions
        function showRecommendedQuestions(questions) {
            const container = document.getElementById('recommendedQuestions');
            container.innerHTML = '';

            if (questions && questions.length > 0) {
                // Show only first 3 questions
                const questionsToShow = questions.slice(0, 3);

                questionsToShow.forEach(question => {
                    const questionElement = document.createElement('div');
                    questionElement.className = 'recommended-question';
                    questionElement.textContent = question;
                    questionElement.addEventListener('click', () => {
                        if (chatBusy || voiceSession) return;
                        messageInput.value = question;
                        messageInput.focus();
                    });
                    container.appendChild(questionElement);
                });
            }
        }

        // Focus on input
        messageInput.focus();
        setInterval(checkVoiceBackend, 3000);
