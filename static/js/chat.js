let isProcessing = false;
let finalReportHtml = null;

async function loadChatState() {
    try {
        const response = await fetch('/get_chat_state');
        const data = await response.json();
        
        if (data.error) {
            window.location.href = '/';
            return;
        }
        
        renderMessages(data.messages);
        
        if (data.is_complete) {
            document.getElementById('messageInput').disabled = true;
            document.getElementById('sendBtn').disabled = true;
            document.getElementById('sendBtn').innerHTML = '<i class="fas fa-check-circle"></i> Complete';
            const lastMsg = data.messages[data.messages.length - 1];
            if (lastMsg && lastMsg.role === 'assistant') {
                finalReportHtml = lastMsg.content;
                addActionButtons();
            }
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
    
    container.innerHTML = messages.map(msg => {
        const isUser = msg.role === 'user';
        const bubbleClass = isUser 
            ? 'bg-gradient-to-r from-purple-600 to-blue-600 text-white ml-auto' 
            : 'bg-white border border-gray-200';
        const alignment = isUser ? 'justify-end' : 'justify-start';
        
        let content = msg.content;
        if (!isUser) {
            content = content.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
            content = content.replace(/\n/g, '<br>');
            content = content.replace(/•/g, '•');
        }
        
        return `
            <div class="flex ${alignment} animate-fadeIn">
                <div class="message-bubble ${bubbleClass} rounded-2xl px-4 py-3 shadow-sm max-w-[80%]">
                    ${!isUser ? '<div class="flex items-center mb-2"><i class="fas fa-robot text-purple-600 mr-2"></i><span class="text-xs font-semibold text-purple-600">Physio AI</span></div>' : ''}
                    <div class="${isUser ? 'text-white' : 'text-gray-800'} leading-relaxed">${content}</div>
                    <div class="text-xs ${isUser ? 'text-purple-200' : 'text-gray-400'} mt-1">
                        ${new Date().toLocaleTimeString()}
                    </div>
                </div>
            </div>
        `;
    }).join('');
    
    // ✅ AUTO-SCROLL TO BOTTOM
    container.scrollTop = container.scrollHeight;
}

function addActionButtons() {
    const container = document.getElementById('chatMessages');
    const existing = document.querySelector('.action-buttons');
    if (existing) existing.remove();
    
    const buttonsDiv = document.createElement('div');
    buttonsDiv.className = 'action-buttons flex justify-center gap-4 mt-4 mb-2';
    buttonsDiv.innerHTML = `
        <button onclick="window.downloadPDFFromChat()" class="bg-purple-600 text-white px-4 py-2 rounded-lg hover:bg-purple-700 transition-all">
            <i class="fas fa-download mr-2"></i>Download PDF
        </button>
        <button id="whatsappShareBtn" class="bg-green-500 text-white px-4 py-2 rounded-lg hover:bg-green-600 transition-all">
            <i class="fab fa-whatsapp mr-2"></i>Share on WhatsApp
        </button>
    `;
    container.appendChild(buttonsDiv);
    
    document.getElementById('whatsappShareBtn').addEventListener('click', () => {
        const phoneMatch = document.querySelector('.bg-gray-50 .text-sm')?.innerText?.match(/\d{10}/);
        const phone = phoneMatch ? phoneMatch[0] : '';
        if (phone) {
            const text = encodeURIComponent(finalReportHtml || 'Prescription from Physio AI Assistant');
            window.open(`https://wa.me/+91${phone}?text=${text}`, '_blank');
        } else {
            alert('Patient phone number not found.');
        }
    });
}

window.downloadPDFFromChat = function() {
    if (finalReportHtml) {
        const div = document.createElement('div');
        div.style.padding = '20px';
        div.style.fontFamily = 'Arial';
        div.innerHTML = `<h2 style="color:#7c3aed;">Physio AI Prescription</h2><div>${finalReportHtml.replace(/\n/g, '<br>').replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')}</div>`;
        document.body.appendChild(div);
        html2pdf().from(div).set({
            margin: [0.5, 0.5, 0.5, 0.5],
            filename: 'physio_prescription.pdf',
            html2canvas: { scale: 2 },
            jsPDF: { unit: 'in', format: 'a4', orientation: 'portrait' }
        }).save().then(() => {
            document.body.removeChild(div);
        }).catch(err => {
            console.error('PDF error:', err);
            alert('Error generating PDF. Please try again.');
            document.body.removeChild(div);
        });
    } else {
        alert('No report available yet. Complete the consultation first.');
    }
};

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
    
    try {
        const response = await fetch('/send_answer', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ answer: message })
        });
        
        const data = await response.json();
        
        if (data.error) {
            alert(data.error);
        } else {
            await loadChatState();
            
            if (data.is_complete) {
                sendBtn.innerHTML = '<i class="fas fa-check-circle"></i> Complete';
                input.disabled = true;
            }
        }
    } catch (error) {
        console.error('Error sending message:', error);
        alert('Error sending message. Please check network and try again.');
        input.value = message;
    } finally {
        isProcessing = false;
        if (!document.getElementById('sendBtn').disabled || (typeof data !== 'undefined' && !data?.is_complete)) {
            sendBtn.innerHTML = originalBtnText;
            sendBtn.disabled = false;
            input.focus();
        }
    }
}

document.addEventListener('DOMContentLoaded', () => {
    loadChatState();
    const sendBtn = document.getElementById('sendBtn');
    const input = document.getElementById('messageInput');
    if (sendBtn) sendBtn.addEventListener('click', sendMessage);
    if (input) input.addEventListener('keypress', (e) => {
        if (e.key === 'Enter' && !isProcessing) sendMessage();
    });
});