const SOMA_URL_PATTERN = "https://www.swmaestro.ai/*";

async function injectContentScript(tabId) {
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ["content.js"],
    });
  } catch (err) {
    console.log("[background] inject 스킵:", err.message);
  }
}

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (
    changeInfo.status === "complete" &&
    tab.url?.startsWith("https://www.swmaestro.ai/")
  ) {
    injectContentScript(tabId);
  }
});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "REQUEST_CRAWL") {
    chrome.tabs.query({ url: SOMA_URL_PATTERN }, (tabs) => {
      if (tabs.length === 0) {
        sendResponse({ ok: false, reason: "소마 탭 없음" });
        return;
      }
      chrome.tabs.sendMessage(tabs[0].id, { type: "CRAWL_LECTURES" }, () => {
        sendResponse({ ok: true });
      });
    });
    return true;
  }
});

chrome.action.onClicked.addListener((tab) => {
  chrome.sidePanel.open({ tabId: tab.id });
});