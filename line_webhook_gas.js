/**
 * LINE webhook receiver for registering JAL flights.
 *
 * Script properties required:
 * - GITHUB_TOKEN: GitHub token that can dispatch workflows
 * - LINE_CHANNEL_ACCESS_TOKEN: LINE Messaging API channel access token
 *
 * LINE message format:
 *   追加 JL567 2026-05-20
 *   JL567 2026-05-20
 *   一覧
 */

const REPO_OWNER = 'kentaro0209';
const REPO_NAME = 'flight-notifier';
const ADD_WORKFLOW_FILE = 'add-flight.yml';
const LIST_WORKFLOW_FILE = 'list-flights.yml';
const REF = 'main';

function doGet() {
  return okResponse_({ ok: true, app: 'flight-notifier-line-webhook' });
}

function doPost(e) {
  const body = e.postData.contents;
  console.log(body);
  const payload = JSON.parse(body);

  for (const event of payload.events || []) {
    if (event.type !== 'message' || event.message.type !== 'text') {
      continue;
    }

    const text = event.message.text.trim();
    if (text === '一覧') {
      try {
        dispatchWorkflow_(LIST_WORKFLOW_FILE, {});
        replyLine_(event.replyToken, '監視便一覧を送信します');
      } catch (error) {
        console.error(error);
        replyLine_(event.replyToken, `一覧取得に失敗しました\n${error.message}`);
      }
      continue;
    }

    const parsed = parseFlightMessage_(text);
    if (!parsed) {
      replyLine_(event.replyToken, '登録形式: 追加 JL567 2026-05-20\n一覧確認: 一覧');
      continue;
    }

    try {
      dispatchWorkflow_(ADD_WORKFLOW_FILE, {
        flight_number: parsed.flight_number,
        flight_date: parsed.flight_date,
      });
      replyLine_(
        event.replyToken,
        `登録を開始しました\n便名: ${parsed.flight_number}\n日付: ${parsed.flight_date}`
      );
    } catch (error) {
      console.error(error);
      replyLine_(event.replyToken, `登録に失敗しました\n${error.message}`);
    }
  }

  return okResponse_({ ok: true });
}

function parseFlightMessage_(text) {
  const normalized = text.trim().replace(/\s+/g, ' ');
  const parts = normalized.split(' ');
  const values = parts[0] === '追加' ? parts.slice(1) : parts;
  if (values.length < 2) {
    return null;
  }

  const flight = values[0].toUpperCase().replace(/\s/g, '');
  const date = values[1];
  if (!/^JL0?\d{1,4}$/.test(flight)) {
    return null;
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    return null;
  }

  return {
    flight_number: flight.replace(/^JL0*(\d+)$/, 'JL$1'),
    flight_date: date,
  };
}

function dispatchWorkflow_(workflowFile, inputs) {
  const token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  const url = `https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/actions/workflows/${workflowFile}/dispatches`;
  const response = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
    },
    payload: JSON.stringify({ ref: REF, inputs }),
    muteHttpExceptions: true,
  });

  const status = response.getResponseCode();
  if (status < 200 || status >= 300) {
    throw new Error(`GitHub API ${status}: ${response.getContentText()}`);
  }
}

function replyLine_(replyToken, text) {
  const token = PropertiesService.getScriptProperties().getProperty('LINE_CHANNEL_ACCESS_TOKEN');
  if (!token || !replyToken) {
    console.log(`skip reply: token=${Boolean(token)} replyToken=${Boolean(replyToken)}`);
    return;
  }

  const response = UrlFetchApp.fetch('https://api.line.me/v2/bot/message/reply', {
    method: 'post',
    contentType: 'application/json',
    headers: {
      Authorization: `Bearer ${token}`,
    },
    payload: JSON.stringify({
      replyToken,
      messages: [{ type: 'text', text }],
    }),
    muteHttpExceptions: true,
  });
  console.log(`LINE reply: ${response.getResponseCode()} ${response.getContentText()}`);
}

function okResponse_(payload) {
  return HtmlService
    .createHtmlOutput(JSON.stringify(payload))
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}
