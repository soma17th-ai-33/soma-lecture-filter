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

      // content.js가 아직 주입 안 됐을 수 있으므로 먼저 강제 주입
      // (이미 주입된 경우 content.js의 __somaLoaded 가드가 재실행을 막음)
      chrome.scripting.executeScript(
        { target: { tabId: tabs[0].id }, files: ['content.js'] },
        () => {
          void chrome.runtime.lastError; // 주입 실패(비소마 페이지 등)는 무시
          chrome.tabs.sendMessage(tabs[0].id, { type: 'CRAWL_LECTURES' }, (res) => {
            if (chrome.runtime.lastError) {
              console.error(chrome.runtime.lastError);
              setStatus('error', '새로고침(F5) 후 다시 시도해주세요.');
              sendBtn.disabled = false;
              resolve([]);
              return;
            }

            if (res?.ok) {
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
            } else {
              setStatus('error', '크롤링 실패');
              resolve([]);
            }
          });
        }
      );
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
const LOADING_MESSAGES = [
  '사용자의 의도를 파악하는 중',
  '맞는 강의를 찾는 중',
  '생각하는 중',
  '답변을 보기좋게 변환 중',
];

let typingInterval = null;

function showTyping() {
  const wrapper = document.createElement('div');
  wrapper.className = 'message assistant';
  wrapper.id = 'typing';

  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = 'A';

  const bubble = document.createElement('div');
  bubble.className = 'bubble';

  const label = document.createElement('span');
  label.className = 'typing-label';
  label.textContent = LOADING_MESSAGES[0];

  bubble.innerHTML = `
    <div class="typing-indicator">
      <div class="typing-dot"></div>
      <div class="typing-dot"></div>
      <div class="typing-dot"></div>
    </div>`;
  const indicator = bubble.querySelector('.typing-indicator');
  indicator.prepend(label);

  wrapper.appendChild(avatar);
  wrapper.appendChild(bubble);
  messagesEl.appendChild(wrapper);
  messagesEl.scrollTop = messagesEl.scrollHeight;

  let idx = 0;
  typingInterval = setInterval(() => {
    idx = (idx + 1) % LOADING_MESSAGES.length;
    label.classList.remove('typing-label');
    void label.offsetWidth; // reflow로 애니메이션 재실행
    label.classList.add('typing-label');
    label.textContent = LOADING_MESSAGES[idx];
  }, 4800);
}

function removeTyping() {
  clearInterval(typingInterval);
  typingInterval = null;
  document.getElementById('typing')?.remove();
}

// assistant 말풍선 생성 후 bubble 요소 반환
function createAssistantBubble() {
  emptyEl?.remove();

  const wrapper = document.createElement('div');
  wrapper.className = 'message assistant';

  const avatar = document.createElement('div');
  avatar.className = 'avatar';
  avatar.textContent = 'A';

  const bubble = document.createElement('div');
  bubble.className = 'bubble';

  wrapper.appendChild(avatar);
  wrapper.appendChild(bubble);
  messagesEl.appendChild(wrapper);

  return bubble;
}

let typewriterAborted = false;
let isTyping = false;

// 텍스트를 글자 단위로 순차 렌더링 (ESC 또는 정지 버튼으로 중단 가능)
async function typewriter(bubble, text, speedMs = 3) {
  typewriterAborted = false;
  let displayed = '';
  for (const char of text) {
    if (typewriterAborted) return;
    displayed += char;
    bubble.innerHTML = marked.parse(displayed);
    messagesEl.scrollTop = messagesEl.scrollHeight;
    await new Promise(r => setTimeout(r, speedMs));
  }
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && isTyping) typewriterAborted = true;
});

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
    agent_used: data.agent_used || [],
  };
}

const LECTURE_BATCH_SIZE = 5;

function lectureCardHtml(lec) {
  return `
    <a href="${lec.url}" target="_blank" class="lecture-card">
      <div class="lec-title">${lec.title}</div>
      <div class="lec-meta">${lec.dateStr} ${lec.timeRangeStr} · ${lec.author}</div>
      <div class="lec-status ${lec.is_open ? 'open' : 'closed'}">${lec.is_open ? '접수중' : '마감'}</div>
    </a>`;
}

function renderLectureList(bubble, lectureList) {
  const cards = document.createElement('div');
  cards.className = 'lecture-cards';
  bubble.appendChild(cards);

  const moreBtn = document.createElement('button');
  moreBtn.className = 'show-more-btn';
  moreBtn.type = 'button';

  let shown = 0;
  const renderBatch = () => {
    const next = lectureList.slice(shown, shown + LECTURE_BATCH_SIZE);
    cards.insertAdjacentHTML('beforeend', next.map(lectureCardHtml).join(''));
    shown += next.length;
    const remaining = lectureList.length - shown;
    if (remaining <= 0) {
      moreBtn.remove();
    } else {
      moreBtn.textContent = `+${Math.min(LECTURE_BATCH_SIZE, remaining)}개 더보기 (${remaining}개 남음)`;
    }
    messagesEl.scrollTop = messagesEl.scrollHeight;
  };

  renderBatch();

  if (shown < lectureList.length) {
    moreBtn.addEventListener('click', renderBatch);
    bubble.appendChild(moreBtn);
  }
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

    const bubble = createAssistantBubble();

    if (reply.agent_used.length === 0 || reply.lectures.length === 0) {
      // Clarification(agent_used=0) 또는 결과 없음(lectures=0): message를 typewriter로 표시
      const replyText = typeof reply.message === 'string' ? reply.message : reply.message.join('\n');
      isTyping = true;
      sendBtn.textContent = '■';
      sendBtn.classList.add('stopping');
      sendBtn.disabled = false;
      await typewriter(bubble, replyText);
    } else {
      // Agent 호출 + 결과 있음: message는 무시하고 lecture 카드만 (초과분은 더보기)
      renderLectureList(bubble, reply.lectures);
    }

    messagesEl.scrollTop = messagesEl.scrollHeight;
  } catch (err) {
    removeTyping();
    appendMessage('assistant', `⚠️ 오류: ${err.message}`, []);
  } finally {
    isTyping = false;
    sendBtn.textContent = '↑';
    sendBtn.classList.remove('stopping');
    sendBtn.disabled = false;
    textarea.focus();
  }
}

sendBtn.addEventListener('click', () => {
  if (isTyping) {
    typewriterAborted = true;
  } else {
    handleSend();
  }
});

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