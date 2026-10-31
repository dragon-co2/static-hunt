// Dragon CO2 - the users of the Auto features (Auto hunt, Monk, Heal, Mail & Bank Compose).
// A Cloudflare Worker with a KV namespace bound as USERS and a secret ADMIN_KEY.
//
//   POST /check  {user, password, device}   -> {ok, user, expires} | {ok: false, error}
//        the first PC a username is used on is kept: another PC is refused (admin: reset)
//   POST /admin  {op, ...}  with  Authorization: Bearer <ADMIN_KEY>
//        add {user, password, days}   password {user, password}   remove {user}
//        extend {user, days}   until {user, date: 'YYYY-MM-DD' - the last day it works}
//        reset {user}   disable {user}   enable {user}   list {}
//
// Checked against PBKDF2-SHA256 with a salt of their own; kept too AES-GCM-encrypted with a key
// made from ADMIN_KEY - so the admin's list shows them, and only the admin's.

const ITERATIONS = 100000;          // the most Workers' PBKDF2 allows
const LOCK_AFTER = 8;               // wrong passwords in a row...
const LOCK_MINUTES = 15;            // ...lock the username this long

const enc = s => new TextEncoder().encode(s);
const hex = buf => [...new Uint8Array(buf)].map(b => b.toString(16).padStart(2, '0')).join('');
const json = (obj, status = 200) => new Response(JSON.stringify(obj), {
  status, headers: {'content-type': 'application/json'}});
const now = () => Date.now();
const day = 24 * 3600 * 1000;

async function hash(password, salt) {
  const key = await crypto.subtle.importKey('raw', enc(password), 'PBKDF2', false, ['deriveBits']);
  const bits = await crypto.subtle.deriveBits(
    {name: 'PBKDF2', salt: enc(salt), iterations: ITERATIONS, hash: 'SHA-256'}, key, 256);
  return hex(bits);
}

async function sealKey(env) {
  const raw = await crypto.subtle.digest('SHA-256', enc('dragon-co2 passwords|' + env.ADMIN_KEY));
  return crypto.subtle.importKey('raw', raw, 'AES-GCM', false, ['encrypt', 'decrypt']);
}

async function seal(env, password) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const data = await crypto.subtle.encrypt({name: 'AES-GCM', iv}, await sealKey(env), enc(password));
  return hex(iv) + ':' + hex(data);
}

async function unseal(env, sealed) {
  if (!sealed) return '';
  try {
    const [iv, data] = sealed.split(':').map(h => new Uint8Array(h.match(/../g).map(b => parseInt(b, 16))));
    return new TextDecoder().decode(await crypto.subtle.decrypt({name: 'AES-GCM', iv}, await sealKey(env), data));
  } catch (e) { return ''; }
}

async function setPassword(env, rec, password) {
  rec.salt = crypto.randomUUID();
  rec.hash = await hash(password, rec.salt);
  rec.sealed = await seal(env, password);
  rec.fails = 0; rec.locked_until = 0;
}

function same(a, b) {                // compared in constant time
  if (typeof a !== 'string' || typeof b !== 'string' || a.length !== b.length) return false;
  let d = 0;
  for (let i = 0; i < a.length; i++) d |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return d === 0;
}

const keyOf = user => 'user:' + String(user || '').trim().toLowerCase();
const load = async (env, user) => JSON.parse(await env.USERS.get(keyOf(user)) || 'null');
const save = (env, user, rec) => env.USERS.put(keyOf(user), JSON.stringify(rec));
const shown = r => ({user: r.user, expires: new Date(r.expires).toISOString().slice(0, 10),
                     device: r.device ? r.device.slice(0, 8) : '', disabled: !!r.disabled,
                     last_seen: r.last_seen ? new Date(r.last_seen).toISOString().slice(0, 16) : ''});

async function check(env, body) {
  const {user, password, device} = body;
  if (!user || !password || !device) return json({ok: false, error: 'Type the username and password'});
  const rec = await load(env, user);
  if (!rec) return json({ok: false, error: 'Wrong username or password'});
  if (rec.locked_until && rec.locked_until > now())
    return json({ok: false, error: 'Too many wrong passwords - try again in a few minutes'});
  if (!same(await hash(password, rec.salt), rec.hash)) {
    rec.fails = (rec.fails || 0) + 1;
    if (rec.fails >= LOCK_AFTER) { rec.locked_until = now() + LOCK_MINUTES * 60000; rec.fails = 0; }
    await save(env, user, rec);
    return json({ok: false, error: 'Wrong username or password'});
  }
  if (rec.disabled) return json({ok: false, error: 'This username is turned off'});
  if (rec.expires < now())
    return json({ok: false, error: 'Expired on ' + new Date(rec.expires).toISOString().slice(0, 10)});
  if (rec.device && rec.device !== device)
    return json({ok: false, error: 'This username is in use on another PC'});
  rec.device = rec.device || device;
  rec.fails = 0; rec.locked_until = 0; rec.last_seen = now();
  await save(env, user, rec);
  return json({ok: true, user: rec.user, expires: new Date(rec.expires).toISOString().slice(0, 10)});
}

async function admin(env, body) {
  const {op, user, password} = body;
  const days = Number(body.days) || 30;
  if (op === 'list') {
    const out = [];
    for (const k of (await env.USERS.list({prefix: 'user:'})).keys) {
      const r = JSON.parse(await env.USERS.get(k.name) || 'null');
      if (r) out.push({...shown(r), password: await unseal(env, r.sealed)});
    }
    return json({ok: true, users: out});
  }
  if (!user) return json({ok: false, error: 'no user'});
  const rec = await load(env, user);
  if (op === 'add') {
    if (rec && !body.force) return json({ok: false, error: 'already there (force to replace)'});
    if (!password || password.length < 6) return json({ok: false, error: 'a password of 6 or more'});
    const r = {user: String(user).trim(), expires: now() + days * day, device: '', created: now()};
    await setPassword(env, r, password);
    await save(env, user, r);
    return json({ok: true, ...shown(r)});
  }
  if (!rec) return json({ok: false, error: 'no such user'});
  if (op === 'remove') { await env.USERS.delete(keyOf(user)); return json({ok: true}); }
  if (op === 'password') {
    if (!password || password.length < 6) return json({ok: false, error: 'a password of 6 or more'});
    await setPassword(env, rec, password);
  }
  else if (op === 'extend') rec.expires = Math.max(rec.expires, now()) + days * day;
  else if (op === 'until') {
    const end = Date.parse(String(body.date || '') + 'T23:59:59Z');      // through that whole day
    if (isNaN(end)) return json({ok: false, error: 'a date as YYYY-MM-DD'});
    rec.expires = end;
  }
  else if (op === 'reset') rec.device = '';
  else if (op === 'disable') rec.disabled = true;
  else if (op === 'enable') rec.disabled = false;
  else return json({ok: false, error: 'unknown op'});
  await save(env, user, rec);
  return json({ok: true, ...shown(rec)});
}

export default {
  async fetch(request, env) {
    if (request.method !== 'POST') return json({ok: true, service: 'dragon-co2 users'});
    let body;
    try { body = await request.json(); } catch (e) { return json({ok: false, error: 'bad request'}, 400); }
    const path = new URL(request.url).pathname;
    if (path === '/check') return check(env, body);
    if (path === '/admin') {
      const auth = request.headers.get('authorization') || '';
      if (!env.ADMIN_KEY || !same(auth, 'Bearer ' + env.ADMIN_KEY))
        return json({ok: false, error: 'not allowed'}, 403);
      return admin(env, body);
    }
    return json({ok: false, error: 'not found'}, 404);
  },
};
