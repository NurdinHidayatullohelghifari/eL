const SPREADSHEET_ID = '1i-yhEqt3nA3vP4PQfF00yWC-om_TLHUcCfkgK9Ckb0Q';
const DATA_SHEET_NAME = 'site_data';
const DATA_FOLDER_NAME = 'EL-GHIFARI Website Data';
const BACKUP_FILE_NAME = 'el-ghifari-site-data.json';
const MAX_CHUNK_LENGTH = 30000;
const MAX_ARTICLES = 100;
const MAX_DATA_BYTES = 1000000;
const LINK_KEYS = ['indonesia', 'technology', 'business', 'sports'];
const ARTICLE_CATEGORIES = ['Teknologi', 'Berita Indonesia', 'Bisnis', 'Olahraga'];

function doGet(e) {
  const callback = e && e.parameter ? e.parameter.callback : '';
  if (!/^[A-Za-z_$][A-Za-z0-9_$]{0,80}$/.test(callback)) {
    return ContentService.createTextOutput('Invalid callback').setMimeType(ContentService.MimeType.TEXT);
  }

  const result = JSON.stringify(readSiteData());
  return ContentService.createTextOutput(callback + '(' + result + ');')
    .setMimeType(ContentService.MimeType.JAVASCRIPT);
}

function doPost(e) {
  const properties = PropertiesService.getScriptProperties();
  const expectedKey = properties.getProperty('ADMIN_KEY');
  if (!expectedKey) return jsonResponse({ ok: false, error: 'ADMIN_KEY belum diatur di Script Properties.' });

  let request;
  try {
    request = JSON.parse(e && e.postData ? e.postData.contents : '');
  } catch (error) {
    return jsonResponse({ ok: false, error: 'Format JSON permintaan tidak valid.' });
  }

  if (!request || !constantTimeEquals(String(request.key || ''), expectedKey)) {
    return jsonResponse({ ok: false, error: 'Kunci admin tidak valid.' });
  }

  const data = validateSiteData(request.data);
  if (!data) return jsonResponse({ ok: false, error: 'Data link atau artikel tidak valid.' });
  data.updatedAt = new Date().toISOString();

  const serialized = JSON.stringify(data);
  if (Utilities.newBlob(serialized).getBytes().length > MAX_DATA_BYTES) {
    return jsonResponse({ ok: false, error: 'Ukuran data melewati batas 1 MB.' });
  }

  try {
    writeSiteData(serialized);
    return jsonResponse({ ok: true, updatedAt: data.updatedAt });
  } catch (error) {
    return jsonResponse({ ok: false, error: 'Gagal menyimpan ke spreadsheet atau backup Drive: ' + error.message });
  }
}

function validateSiteData(data) {
  if (!data || typeof data !== 'object' || !data.links || typeof data.links !== 'object') return null;

  const links = {};
  for (const key of LINK_KEYS) {
    const url = data.links[key];
    if (!isHttpsUrl(url)) return null;
    links[key] = url;
  }

  if (!Array.isArray(data.articles) || data.articles.length > MAX_ARTICLES) return null;
  const articles = [];
  for (const article of data.articles) {
    if (!article || typeof article !== 'object') return null;
    if (!isText(article.id, 100) || !isText(article.title, 120)) return null;
    if (!ARTICLE_CATEGORIES.includes(article.category)) return null;
    if (!isText(article.excerpt, 240) || !isText(article.body, 20000)) return null;
    if (article.imageUrl && !isAllowedImage(article.imageUrl)) return null;
    if (article.sourceUrl && !isHttpsUrl(article.sourceUrl)) return null;
    articles.push({
      id: article.id,
      category: article.category,
      title: article.title,
      excerpt: article.excerpt,
      body: article.body,
      imageUrl: article.imageUrl || '',
      sourceUrl: article.sourceUrl || '',
      createdAt: isText(article.createdAt, 40) ? article.createdAt : new Date().toISOString()
    });
  }

  return {
    version: 1,
    updatedAt: isText(data.updatedAt, 40) ? data.updatedAt : new Date().toISOString(),
    backupUpdatedAt: '',
    links: links,
    articles: articles
  };
}

function isText(value, maxLength) {
  return typeof value === 'string' && value.trim().length > 0 && value.length <= maxLength;
}

function isHttpsUrl(value) {
  return typeof value === 'string' && value.length <= 2048 && /^https:\/\/[^\s]+$/i.test(value);
}

function isAllowedImage(value) {
  if (isHttpsUrl(value)) return true;
  return typeof value === 'string' &&
    value.length <= 600000 &&
    /^data:image\/jpeg;base64,[A-Za-z0-9+/]+={0,2}$/.test(value);
}

function constantTimeEquals(a, b) {
  if (a.length !== b.length) return false;
  let difference = 0;
  for (let i = 0; i < a.length; i++) difference |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return difference === 0;
}

function readSiteData() {
  const sheet = getDataSheet();
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return emptySiteData();

  try {
    const rows = sheet.getRange(2, 1, lastRow - 1, 4).getValues()
      .filter(function (row) { return row[0] === 'site_data'; })
      .sort(function (a, b) { return Number(a[2]) - Number(b[2]); });
    if (!rows.length) return emptySiteData();
    const parsed = JSON.parse(rows.map(function (row) { return row[3]; }).join(''));
    const data = validateSiteData(parsed) || emptySiteData();
    data.backupUpdatedAt = PropertiesService.getScriptProperties().getProperty('BACKUP_UPDATED_AT') || '';
    return data;
  } catch (error) {
    console.error('Tidak dapat membaca data website dari spreadsheet: ' + error.message);
    return emptySiteData();
  }
}

function writeSiteData(serialized) {
  const lock = LockService.getScriptLock();
  lock.waitLock(10000);
  try {
    const sheet = getDataSheet();
    const chunks = [];
    for (let index = 0; index < serialized.length; index += MAX_CHUNK_LENGTH) {
      chunks.push(serialized.slice(index, index + MAX_CHUNK_LENGTH));
    }
    if (!chunks.length) chunks.push('');
    const timestamp = new Date().toISOString();
    const rows = [['record', 'updatedAt', 'chunkIndex', 'jsonChunk']];
    chunks.forEach(function (chunk, index) {
      rows.push(['site_data', timestamp, index, chunk]);
    });
    sheet.clearContents();
    sheet.getRange(1, 1, rows.length, 4).setValues(rows);
    backupToDrive(serialized);
    PropertiesService.getScriptProperties().setProperty('BACKUP_UPDATED_AT', JSON.parse(serialized).updatedAt);
  } finally {
    lock.releaseLock();
  }
}

function getDataSheet() {
  const spreadsheet = SpreadsheetApp.openById(SPREADSHEET_ID);
  let sheet = spreadsheet.getSheetByName(DATA_SHEET_NAME);
  if (!sheet) sheet = spreadsheet.insertSheet(DATA_SHEET_NAME);
  return sheet;
}

function backupToDrive(serialized) {
  const folder = getDataFolder();
  const files = folder.getFilesByName(BACKUP_FILE_NAME);
  if (files.hasNext()) {
    files.next().setContent(serialized);
  } else {
    folder.createFile(BACKUP_FILE_NAME, serialized, MimeType.JSON);
  }
}

function getDataFolder() {
  const folders = DriveApp.getFoldersByName(DATA_FOLDER_NAME);
  return folders.hasNext() ? folders.next() : DriveApp.createFolder(DATA_FOLDER_NAME);
}

function emptySiteData() {
  return { version: 1, updatedAt: '', backupUpdatedAt: '', links: {}, articles: [] };
}

function jsonResponse(value) {
  return ContentService.createTextOutput(JSON.stringify(value))
    .setMimeType(ContentService.MimeType.JSON);
}
