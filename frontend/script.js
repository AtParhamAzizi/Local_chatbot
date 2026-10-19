// Configuration - Update this URL if your backend runs on a different port
        const BACKEND_URL = 'http://localhost:5000';
        const USER_ID = 'user_' + Math.random().toString(36).substr(2, 9);

        // DOM elements
        const chatMessages = document.getElementById('chatMessages');
        const messageInput = document.getElementById('messageInput');
        const sendButton = document.getElementById('sendButton');
        const connectionStatus = document.getElementById('connectionStatus');


        // const quickQuestions = document.getElementById('quick-questions');
        // const quickQuestionsbtn = document.getElementsByClassName('quick-question-btn');

// Check backend connection on page load
        checkBackendConnection();

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


        // Function to send message to Python backend
        async function sendMessage() {

            const message = messageInput.value.trim();
            if (!message) return;



            // Add user message
            addMessage(message, true);
            messageInput.value = '';


            // Disable input while processing
            messageInput.disabled = true;
            sendButton.disabled = true;

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
                messageInput.disabled = false;
                sendButton.disabled = false;
                messageInput.focus();
            }
        }

        // Event listeners
        sendButton.addEventListener('click', sendMessage);

        messageInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                sendMessage();
            }
        });


        function sendQuickQuestion(text) {
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
                        messageInput.value = question;
                        messageInput.focus();
                    });
                    container.appendChild(questionElement);
                });
            }
        }

        // Focus on input
        messageInput.focus();
