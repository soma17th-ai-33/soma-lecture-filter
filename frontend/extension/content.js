function parseHtmlDocument(html) {
  return new DOMParser().parseFromString(html, "text/html");
}

function getTodayStr() {
  const today = new Date();
  const y = today.getFullYear();
  const m = String(today.getMonth() + 1).padStart(2, '0');
  const d = String(today.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

function extractLecturesFromHTML(html) {
  const doc = parseHtmlDocument(html);
  const lectures = [];
  const rows = doc.querySelectorAll('.boardlist.mt50 table tbody tr');

  for (const row of rows) {
    const tds = row.querySelectorAll('td');
    if (tds.length < 8) continue;

    const titleEl = row.querySelector('td.tit a');
    if (!titleEl) continue;

    const title = titleEl.textContent?.trim();
    const url   = titleEl.getAttribute('href');
    if (!title || !url) continue;

    const fullUrl = url.startsWith('http') ? url : `https://www.swmaestro.ai${url}`;

    const dateTimeRaw = tds[3]?.textContent?.replace(/\u00a0/g, ' ').trim() || '';
    const parts = dateTimeRaw.split('\n').map(s => s.trim()).filter(Boolean);
    const dateStr      = parts[0] || '';
    const timeRangeStr = parts[1] || '';

    const peopleRaw   = tds[4]?.textContent?.trim() || '';
    const peopleMatch = peopleRaw.match(/(\d+)\s*\/\s*(\d+)/);
    const appliedCount = peopleMatch ? parseInt(peopleMatch[1]) : null;
    const totalCount   = peopleMatch ? parseInt(peopleMatch[2]) : null;

    const statusText = tds[6]?.textContent?.trim() || '';
    const is_open    = !statusText.includes('마감');

    const author = tds[7]?.textContent?.trim() || '';

    lectures.push({
      title,
      url: fullUrl,
      author,
      dateStr,
      timeRangeStr,
      appliedCount,
      totalCount,
      is_open,
    });
  }

  return lectures;
}

async function getTotalPages(today) {
  const baseUrl = `https://www.swmaestro.ai/sw/mypage/mentoLec/list.do` +
    `?menuNo=200046&scdate=${today}&ecdate=2026-12-31&pageIndex=1`;

  const res = await fetch(baseUrl, { credentials: 'include' });
  const html = await res.text();
  const doc  = parseHtmlDocument(html);

  const pageLinks = doc.querySelectorAll('.pagination a, .paging a, a[href*="pageIndex"]');
  let maxPage = 1;
  for (const a of pageLinks) {
    const match = a.href?.match(/pageIndex=(\d+)/);
    if (match) maxPage = Math.max(maxPage, parseInt(match[1]));
  }

  const totalEl = doc.querySelector('.bbs-total strong, .total strong, strong.color-blue');
  if (totalEl) {
    const total = parseInt(totalEl.nextSibling?.textContent?.replace(/\D/g, '') || '0');
    if (total > 0) return Math.ceil(total / 10);
  }

  return maxPage;
}

async function getAllLectures() {
  const lectures = [];
  const today = getTodayStr();
  const BASE = `https://www.swmaestro.ai/sw/mypage/mentoLec/list.do` +
    `?menuNo=200046&scdate=${today}&ecdate=2026-12-31`;

  const totalPages = await getTotalPages(today);
  console.log(`[소마 Agent] 총 ${totalPages}페이지 크롤링 시작 (${today} 이후)`);

  for (let page = 1; page <= totalPages; page++) {
    const res = await fetch(`${BASE}&pageIndex=${page}`, { credentials: 'include' });
    const html = await res.text();
    const pageLectures = extractLecturesFromHTML(html);
    lectures.push(...pageLectures);
    console.log(`[소마 Agent] ${page}/${totalPages} 페이지 — ${pageLectures.length}개`);
  }

  return lectures;
}

async function crawlAndStore() {
  try {
    console.log('[소마 Agent] 크롤링 시작...');
    const lectures = await getAllLectures();
    chrome.storage.local.set({ lectures, lecturesFetchedAt: Date.now() });
    console.log(`[소마 Agent] 완료 — 총 ${lectures.length}개 저장`);
  } catch (err) {
    console.error('[소마 Agent] 실패:', err);
    chrome.storage.local.set({ lectures: [], lecturesFetchedAt: Date.now() });
  }
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === 'CRAWL_LECTURES') {
    crawlAndStore().then(() => sendResponse({ ok: true }));
    return true;
  }
});