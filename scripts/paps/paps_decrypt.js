// run_paps2.js — XXTEA 库 + paps 混淆代码 → 解密出 m3u8
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const xxteaCode = fs.readFileSync(path.join(__dirname, 'xxtea_lib.js'), 'utf8');
const obfCode = fs.readFileSync(path.join(__dirname, 'paps_obf.js'), 'utf8');
let enc = '';
try {
  const input = JSON.parse(fs.readFileSync(0, 'utf8'));
  enc = String(input.enc || '');
} catch (e) {
  process.stderr.write('stdin err: ' + e.message + '\n');
}
process.stderr.write('enc_len: ' + enc.length + '\n');

const sandbox = {
  window: null,
  document: {
    write: () => {}, writeln: () => {},
    referrer: 'http://play.sportsteam53.com/play/steam825430.html',
    getElementById: () => ({ style: {}, innerHTML: '', appendChild: () => {}, setAttribute: () => {} }),
    createElement: () => ({ style: {}, setAttribute: () => {}, appendChild: () => {} }),
    getElementsByTagName: () => [],
    body: { appendChild: () => {} },
    addEventListener: () => {},
    cookie: '',
  },
  navigator: { userAgent: 'Mozilla/5.0 (iPhone; Mobile Safari)', maxTouchPoints: 0 },
  location: { href: 'https://demo.songzenet.com/paps.html?id=' + enc, protocol: 'https:', host: 'demo.songzenet.com', hostname: 'demo.songzenet.com', pathname: '/paps.html', search: '?id=' + enc, hash: '', origin: 'https://demo.songzenet.com' },
  console: { log: () => {}, error: () => {}, warn: () => {} },
  atob: (s) => Buffer.from(s, 'base64').toString('binary'),
  btoa: (s) => Buffer.from(s, 'binary').toString('base64'),
  setTimeout: () => 0,
  setInterval: () => 0,
  clearTimeout: () => {}, clearInterval: () => {},
  alert: () => {}, unescape, escape, decodeURIComponent, encodeURIComponent,
  Date, Math, JSON,
  addEventListener: () => {},
  localStorage: { getItem: () => null, setItem: () => {} },
  screen: { width: 390, height: 844 },
};
sandbox.document.location = sandbox.location;
sandbox.self = sandbox;
sandbox.top = sandbox;
sandbox.parent = sandbox;
sandbox.frames = sandbox;
sandbox.window = sandbox;
vm.createContext(sandbox);

// 1. XXTEA 库
try { vm.runInContext(xxteaCode, sandbox, { timeout: 5000 }); }
catch (e) { console.log('[xxtea err]', e.message); }
console.error('XXTEA available:', vm.runInContext('typeof XXTEA', sandbox));

// 2. paps 混淆代码
try { vm.runInContext(obfCode, sandbox, { timeout: 8000 }); }
catch (e) { console.log('[paps err]', e.message); }

const out = vm.runInContext(
  "typeof originalUrl !== 'undefined' ? originalUrl : 'NOTFOUND'",
  sandbox, { timeout: 3000 },
);
if (typeof out === 'string' && out.startsWith('http')) process.stdout.write(out);
else process.stderr.write('RESULT: ' + String(out).slice(0,120) + '\n');