const messagesEl = document.getElementById('messages');
const emptyEl    = document.getElementById('empty');
const textarea   = document.getElementById('user-input');
const sendBtn    = document.getElementById('send-btn');
const statusEl   = document.getElementById('status');
const statusText = document.getElementById('status-text');

let history  = [];
let lectures = null;

function setStatus(state, text) {
  statusEl.className = `header-status ${state}`;
  statusText.textContent = text;
}

async function loadLectures() {
  setStatus('', '크롤링 중...');
  sendBtn.disabled = true;

  return new Promise((resolve) => {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      if (tabs.length === 0 || !tabs[0].url.includes("swmaestro.ai")) {
        setStatus('error', '소마 탭을 열어주세요');
        sendBtn.disabled = false;
        resolve([]);
        return;
      }

      chrome.tabs.sendMessage(tabs[0].id, { type: 'CRAWL_LECTURES' }, (res) => {
        if (chrome.runtime.lastError) {
          console.error(chrome.runtime.lastError);
          setStatus('error', '새로고침(F5) 후 다시 시도해주세요.');
          sendBtn.disabled = false;
          resolve([]);
          return;
        }

        if (res?.ok) {
          setTimeout(() => {
            chrome.storage.local.get(['lectures'], (r) => {
              lectures = r.lectures || [];
              if (lectures.length > 0) {
                setStatus('ready', `강의 ${lectures.length}개 로드됨`);
              } else {
                setStatus('error', '강의 없음 (소마 로그인 확인)');
              }
              sendBtn.disabled = false;
              resolve(lectures);
            });
          }, 2000);
        } else {
          setStatus('error', '크롤링 실패');
          resolve([]);
        }
      });
    });
  });
}

function appendMessage(role, data, lectureList = []) {
  emptyEl?.remove();

  const wrapper = document.createElement('div');
  wrapper.className = `message ${role}`;

  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = role === 'user' ? 'U' : 'A';

  const bubble = document.createElement('div');
  bubble.className = 'bubble';

  if (role === 'assistant') {
    const text = typeof data === 'string' ? data : data.join('\n');
    bubble.innerHTML = marked.parse(text);

    if (lectureList.length > 0) {
      const cards = document.createElement('div');
      cards.className = 'lecture-cards';
      lectureList.forEach(lec => {
        cards.innerHTML += `
          <a href="${lec.url}" target="_blank" class="lecture-card">
            <div class="lec-title">${lec.title}</div>
            <div class="lec-meta">${lec.dateStr} ${lec.timeRangeStr} · ${lec.author}</div>
            <div class="lec-status ${lec.is_open ? 'open' : 'closed'}">${lec.is_open ? '접수중' : '마감'}</div>
          </a>`;
      });
      bubble.appendChild(cards);
    }
  } else {
    bubble.textContent = Array.isArray(data) ? data.join('\n') : data;
  }

  wrapper.appendChild(avatar);
  wrapper.appendChild(bubble);
  messagesEl.appendChild(wrapper);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}
function showTyping() {
  const wrapper = document.createElement('div');
  wrapper.className = 'message assistant';
  wrapper.id = 'typing';

  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = 'A';

  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  bubble.innerHTML = `
    <div class="typing-indicator">
      <div class="typing-dot"></div>
      <div class="typing-dot"></div>
      <div class="typing-dot"></div>
    </div>`;

  wrapper.appendChild(avatar);
  wrapper.appendChild(bubble);
  messagesEl.appendChild(wrapper);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function removeTyping() {
  document.getElementById('typing')?.remove();
}

async function callBackend(message) {
  const body = {
    message,
    history,
    lectures: lectures || [],
  };

  const res = await fetch('http://localhost:8000/agent/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  history = data.history;

  return {
    message: data.message,
    lectures: data.lectures || [],
  };
}

async function handleSend() {
  const text = textarea.value.trim();
  if (!text || sendBtn.disabled) return;

  appendMessage('user', text);
  textarea.value = '';
  textarea.style.height = 'auto';
  sendBtn.disabled = true;
  showTyping();

  try {
    const reply = await callBackend(text);
    removeTyping();
    appendMessage('assistant', reply.message, reply.lectures);
  } catch (err) {
    removeTyping();
    appendMessage('assistant', `⚠️ 오류: ${err.message}`, []);
  } finally {
    sendBtn.disabled = false;
    textarea.focus();
  }
}

sendBtn.addEventListener('click', handleSend);

textarea.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    handleSend();
  }
});

textarea.addEventListener('input', () => {
  textarea.style.height = 'auto';
  textarea.style.height = Math.min(textarea.scrollHeight, 80) + 'px';
});

loadLectures().then(() => {
  textarea.focus();
});