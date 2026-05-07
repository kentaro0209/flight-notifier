/**
 * LINE webhook receiver for registering flights.
 *
 * Deploy this file as a Google Apps Script web app and set the web app URL as
 * the LINE Messaging API webhook URL.
 *
 * Script properties required:
 * - GITHUB_TOKEN: GitHub token that can dispatch workflows
 * - LINE_CHANNEL_ACCESS_TOKEN: LINE Messaging API channel access token
 *
 * LINE message format:
 *   追加 JL006 2026-05-10
 *   JL006 2026-05-10
 */

const REPO_OWNER = 'kentaro0209';
const REPO_NAME = 'flight-notifier';
const WORKFLOW_FILE = 'add-flight.yml';
const REF = 'main';

function doPost(e) {
  const body = e.postData.contents;
  const payload = JSON.parse(body);
  for (const event of payload.events || []) {
    if (event.type !== 'message' || event.message.type !== 'text') {
      continue;
    }

    const parsed = parseFlightMessage_(event.message.text);
    if (!parsed) {
      replyLine_(event.replyToken, '登録形式: 追加 JL006 2026-05-10');
      continue;
    }

    try {
      dispatchAddFlight_(parsed.flight_iata, parsed.flight_date);
      replyLine_(
        event.replyToken,
        `登録を開始しました\n便名: ${parsed.flight_iata}\n日付: ${parsed.flight_date}`
      );
    } catch (error) {
      replyLine_(event.replyToken, `登録に失敗しました\n${error.message}`);
    }
  }

  return jsonResponse_({ ok: true }, 200);
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
  if (!/^[A-Z0-9]{2,3}\d{1,4}[A-Z]?$/.test(flight)) {
    return null;
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) {
    return null;
  }

  return { flight_iata: flight, flight_date: date };
}

function dispatchAddFlight_(flightIata, flightDate) {
  const token = PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN');
  const url = `https://api.github.com/repos/${REPO_OWNER}/${REPO_NAME}/actions/workflows/${WORKFLOW_FILE}/dispatches`;
  const response = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
    },
    payload: JSON.stringify({
      ref: REF,
      inputs: {
        flight_iata: flightIata,
        flight_date: flightDate,
      },
    }),
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
    return;
  }

  UrlFetchApp.fetch('https://api.line.me/v2/bot/message/reply', {
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
}

function jsonResponse_(payload, status) {
  return ContentService
    .createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}
