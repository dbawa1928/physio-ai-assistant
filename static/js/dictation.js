let recognition = null;
let isRecording = false;

function initSpeechRecognition(textareaId) {
    if (!('webkitSpeechRecognition' in window)) {
        console.warn('Speech recognition not supported');
        return;
    }
    recognition = new webkitSpeechRecognition();
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.lang = 'en-US';
    
    recognition.onresult = (event) => {
        let finalTranscript = '';
        for (let i = event.resultIndex; i < event.results.length; i++) {
            if (event.results[i].isFinal) {
                finalTranscript += event.results[i][0].transcript;
            }
        }
        const textarea = document.getElementById(textareaId);
        if (finalTranscript) {
            textarea.value += (textarea.value ? ' ' : '') + finalTranscript;
        }
    };
    recognition.onend = () => {
        isRecording = false;
        const btn = document.getElementById('dictateBtn');
        if (btn) btn.innerHTML = '<i class="fas fa-microphone"></i> Dictate';
    };
}

function startDictation(textareaId) {
    if (isRecording) {
        if (recognition) recognition.stop();
        return;
    }
    if (recognition) {
        recognition.start();
        isRecording = true;
        const btn = document.getElementById('dictateBtn');
        btn.innerHTML = '<i class="fas fa-stop-circle"></i> Stop';
    } else {
        alert('Speech recognition not supported in this browser. Try Chrome or Edge.');
    }
}


