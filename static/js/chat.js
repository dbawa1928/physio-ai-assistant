let isProcessing = false;
let currentConsultationId = null;
let typingIndicatorElement = null;

function showTypingIndicator() {
    const container = document.getElementById('chatMessages');
    if (typingIndicatorElement) typingIndicatorElement.remove();
    typingIndicatorElement = document.createElement('div');
    typingIndicatorElement.className = 'flex justify-start animate-fadeIn';
    typingIndicatorElement.innerHTML = `
        <div class="bg-white border border-gray-200 rounded-2xl px-4 py-3 shadow-sm">
            <div class="flex items-center space-x-2">
                <i class="fas fa-user-md text-purple-600"></i>
                <span class="text-xs font-semibold text-purple-600">Dr. Physio (PT)</span>
                <div class="flex space-x-1">
                    <div class="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style="animation-delay:0s"></div>
                    <div class="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style="animation-delay:0.2s"></div>
                    <div class="w-2 h-2 bg-gray-400 rounded-full animate-bounce" style="animation-delay:0.4s"></div>
                </div>
            </div>
        </div>
    `;
    container.appendChild(typingIndicatorElement);
    container.scrollTop = container.scrollHeight;
}

function hideTypingIndicator() {
    if (typingIndicatorElement) {
        typingIndicatorElement.remove();
        typingIndicatorElement = null;
    }
}

async function loadChatState() {
    try {
        const response = await fetch('/get_chat_state');
        const data = await response.json();
        if (data.error) {
            window.location.href = '/';
            return;
        }
        currentConsultationId = data.consultation_id;
        renderMessages(data.messages);
        if (data.current_step) {
            updateProgress(data.current_step);
        }
        if (data.is_complete) {
            document.getElementById('messageInput').disabled = true;
            document.getElementById('sendBtn').disabled = true;
            document.getElementById('sendBtn').innerHTML = '<i class="fas fa-check-circle"></i> Complete';
            document.getElementById('completionSection').classList.remove('hidden');
        } else {
            document.getElementById('completionSection').classList.add('hidden');
        }
    } catch (error) {
        console.error('Error loading chat:', error);
    }
}

function renderMessages(messages) {
    const container = document.getElementById('chatMessages');
    if (!messages || messages.length === 0) {
        container.innerHTML = '<div class="text-center text-gray-500">No messages yet. Start the conversation!</div>';
        return;
    }
    container.innerHTML = messages.map((msg, idx) => {
        const isUser = msg.role === 'user';
        const bubbleClass = isUser ? 'bg-gradient-to-r from-purple-600 to-blue-600 text-white ml-auto' : 'bg-white border border-gray-200';
        const alignment = isUser ? 'justify-end' : 'justify-start';
        let content = msg.content;
        if (!isUser) {
            content = content.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
            content = content.replace(/\n/g, '<br>');
        }
        let feedbackHtml = '';
        if (!isUser && !sessionStorage.getItem(`feedback_${idx}`) && !document.getElementById('sendBtn').disabled) {
            feedbackHtml = `<div class="flex gap-2 mt-2 text-xs">
                <button onclick="submitFeedback(${idx}, 'up')" class="text-green-600 hover:text-green-800"><i class="fas fa-thumbs-up"></i> Helpful</button>
                <button onclick="submitFeedback(${idx}, 'down')" class="text-red-600 hover:text-red-800"><i class="fas fa-thumbs-down"></i> Not helpful</button>
            </div>`;
        }
        return `
            <div class="flex ${alignment} animate-fadeIn">
                <div class="message-bubble ${bubbleClass} rounded-2xl px-4 py-3 shadow-sm max-w-[80%]">
                    ${!isUser ? '<div class="flex items-center mb-2"><i class="fas fa-user-md text-purple-600 mr-2"></i><span class="text-xs font-semibold text-purple-600">Dr. Physio (PT)</span></div>' : ''}
                    <div class="${isUser ? 'text-white' : 'text-gray-800'} leading-relaxed">${content}</div>
                    <div class="text-xs ${isUser ? 'text-purple-200' : 'text-gray-400'} mt-1">
                        ${new Date().toLocaleTimeString()}
                    </div>
                    ${feedbackHtml}
                </div>
            </div>
        `;
    }).join('');
    container.scrollTop = container.scrollHeight;
}

function updateProgress(step) {
    const stepSpan = document.getElementById('currentStep');
    const progressBar = document.getElementById('progressBar');
    if (stepSpan && progressBar) {
        stepSpan.innerText = step;
        const total = parseInt(stepSpan.getAttribute('data-max') || 12);
        const percent = (step / total) * 100;
        progressBar.style.width = percent + '%';
    }
}

async function submitFeedback(messageIndex, rating) {
    try {
        await fetch('/submit_feedback', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ consultation_id: currentConsultationId, message_index: messageIndex, rating: rating, comment: '' })
        });
        alert('Thank you for your feedback!');
        sessionStorage.setItem(`feedback_${messageIndex}`, true);
        loadChatState();
    } catch (error) {
        console.error('Feedback error:', error);
    }
}

async function sendMessage() {
    if (isProcessing) return;
    const input = document.getElementById('messageInput');
    const message = input.value.trim();
    if (!message) return;
    
    isProcessing = true;
    const sendBtn = document.getElementById('sendBtn');
    const originalBtnText = sendBtn.innerHTML;
    sendBtn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Sending...';
    sendBtn.disabled = true;
    input.value = '';
    
    showTypingIndicator();
    const timeoutPromise = new Promise((_, reject) => setTimeout(() => reject(new Error('Timeout')), 30000));
    try {
        const fetchPromise = fetch('/send_answer', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ answer: message })
        });
        const response = await Promise.race([fetchPromise, timeoutPromise]);
        const data = await response.json();
        hideTypingIndicator();
        if (data.error) {
            alert(data.error);
            sendBtn.innerHTML = originalBtnText;
            sendBtn.disabled = false;
            input.value = message;
        } else {
            await loadChatState();
            if (data.current_step) updateProgress(data.current_step);
            if (data.is_complete) {
                sendBtn.innerHTML = '<i class="fas fa-check-circle"></i> Complete';
                input.disabled = true;
                document.getElementById('completionSection').classList.remove('hidden');
            } else {
                sendBtn.innerHTML = originalBtnText;
                sendBtn.disabled = false;
            }
        }
    } catch (error) {
        hideTypingIndicator();
        alert('Network error. Please try again.');
        sendBtn.innerHTML = originalBtnText;
        sendBtn.disabled = false;
        input.value = message;
    } finally {
        isProcessing = false;
        if (!input.disabled) input.focus();
    }
}

if ('webkitSpeechRecognition' in window) {
    const SpeechRecognition = window.webkitSpeechRecognition;
    const recognition = new SpeechRecognition();
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.onresult = (event) => {
        document.getElementById('messageInput').value = event.results[0][0].transcript;
        sendMessage();
    };
    const voiceBtn = document.createElement('button');
    voiceBtn.innerHTML = '<i class="fas fa-microphone"></i>';
    voiceBtn.className = 'bg-gray-200 hover:bg-gray-300 text-gray-700 px-4 py-3 rounded-lg';
    voiceBtn.onclick = () => recognition.start();
    const buttonContainer = document.querySelector('.flex.gap-3');
    if (buttonContainer) buttonContainer.appendChild(voiceBtn);
}

document.addEventListener('DOMContentLoaded', () => {
    loadChatState();
    const sendBtn = document.getElementById('sendBtn');
    const input = document.getElementById('messageInput');
    if (sendBtn) sendBtn.addEventListener('click', sendMessage);
    if (input) input.addEventListener('keypress', (e) => {
        if (e.key === 'Enter' && !isProcessing) sendMessage();
    });
    document.addEventListener('keydown', function(e) {
        if (e.ctrlKey && e.key === 'Enter') {
            e.preventDefault();
            sendMessage();
        }
        if (e.ctrlKey && e.shiftKey && e.key === 'P') {
            e.preventDefault();
            window.print();
        }
    });
});