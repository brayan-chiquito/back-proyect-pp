/**
 * Bridge Baileys (tenants configurables) → backend FastAPI.
 * Un puerto BRIDGE_PORT; reconexión salvo loggedOut (401).
 */
import { config as loadEnv } from "dotenv";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

// Carga el .env de la raíz del repo independientemente del cwd
const __dirnameTop = dirname(fileURLToPath(import.meta.url));
loadEnv({ path: resolve(__dirnameTop, "../.env") });

import { Boom } from "@hapi/boom";
import makeWASocket, {
  Browsers,
  DisconnectReason,
  fetchLatestBaileysVersion,
  useMultiFileAuthState,
} from "@whiskeysockets/baileys";
import express from "express";
import { mkdir } from "fs/promises";
import { join } from "path";
import pino from "pino";

const __dirname = dirname(fileURLToPath(import.meta.url));

// Solo pastelería por ahora; descomenta para levantar más sesiones/QR.
const TENANTS = [
  "pasteleria",
  // "pijamas",
  // "comida",
];

const bridgeLog = pino({
  level: process.env.LOG_LEVEL || "info",
  name: "bridge",
});

/** Sockets Baileys activos por tenant (null tras close hasta reconectar). */
const socks = new Map();

/** Evita colas de reconexión duplicadas por tenant. */
const reconnectTimers = new Map();

function backendMessageUrl() {
  const base = (process.env.BACKEND_URL || "http://localhost:8000").replace(
    /\/$/,
    "",
  );
  return `${base}/message`;
}

async function forwardInboundMessage(tenant, from, text) {
  const secret = process.env.BRIDGE_SECRET || "";
  const res = await fetch(backendMessageUrl(), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(secret ? { Authorization: `Bearer ${secret}` } : {}),
    },
    body: JSON.stringify({ tenant, from, text }),
  });
  if (!res.ok) {
    const body = await res.text();
    bridgeLog.warn(
      { tenant, status: res.status, body },
      "backend /message error",
    );
  }
}

function messagePlainText(message) {
  const m = message?.message;
  if (!m) return "";
  if (m.conversation) return m.conversation;
  if (m.extendedTextMessage?.text) return m.extendedTextMessage.text;
  if (m.imageMessage?.caption) return m.imageMessage.caption;
  if (m.videoMessage?.caption) return m.videoMessage.caption;
  if (m.documentMessage?.caption) return m.documentMessage.caption;
  return "";
}

function inboundFromJid(msg) {
  return msg.key.participant || msg.key.remoteJid || "";
}

function toWhatsAppJid(to) {
  if (String(to).includes("@")) return String(to);
  const digits = String(to).replace(/\D/g, "");
  return `${digits}@s.whatsapp.net`;
}

function disconnectStatusCode(lastDisconnect) {
  const err = lastDisconnect?.error;
  if (err instanceof Boom) return err.output?.statusCode ?? 500;
  return DisconnectReason.connectionClosed;
}

async function printQrTerminal(tenant, qr) {
  const QRCode = (await import("qrcode")).default;
  const ascii = await QRCode.toString(qr, { type: "terminal", small: true });
  console.log(`\n[${tenant}] Escanea el código QR:\n${ascii}`);
}

function scheduleReconnect(tenant) {
  if (reconnectTimers.has(tenant)) return;
  const id = setTimeout(() => {
    reconnectTimers.delete(tenant);
    startTenantBaileys(tenant).catch((err) => {
      bridgeLog.error({ err, tenant }, "reconnect start failed");
    });
  }, 3500);
  reconnectTimers.set(tenant, id);
}

async function startTenantBaileys(tenant) {
  const sessionDir = join(__dirname, "sessions", tenant);
  await mkdir(sessionDir, { recursive: true });

  const prev = socks.get(tenant);
  if (prev?.end) {
    try {
      prev.end(undefined);
    } catch {
      /* ya cerrado */
    }
  }

  const { version } = await fetchLatestBaileysVersion();
  const { state, saveCreds } = await useMultiFileAuthState(sessionDir);

  const baileysLogger = pino({ level: "silent" });

  const sock = makeWASocket({
    version,
    logger: baileysLogger,
    browser: Browsers.macOS("Desktop"),
    auth: state,
  });

  socks.set(tenant, sock);

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("messages.upsert", async (upsert) => {
    if (upsert.type !== "notify") return;
    for (const msg of upsert.messages) {
      if (msg.key.fromMe) continue;
      const text = messagePlainText(msg);
      if (!text) continue;
      const from = inboundFromJid(msg);
      if (!from) continue;
      try {
        await forwardInboundMessage(tenant, from, text);
      } catch (err) {
        bridgeLog.error({ err, tenant }, "forward to backend failed");
      }
    }
  });

  sock.ev.on("connection.update", async (update) => {
    const { connection, lastDisconnect, qr } = update;
    if (qr) {
      await printQrTerminal(tenant, qr);
    }
    if (connection === "open") {
      bridgeLog.info({ tenant }, "whatsapp connected");
    }
    if (connection === "close") {
      const code = disconnectStatusCode(lastDisconnect);
      const shouldReconnect = code !== DisconnectReason.loggedOut;
      bridgeLog.warn({ tenant, code, shouldReconnect }, "whatsapp closed");
      socks.set(tenant, null);
      if (shouldReconnect) {
        scheduleReconnect(tenant);
      } else {
        bridgeLog.info({ tenant }, "loggedOut: no auto-reconnect");
      }
    }
  });
}

function requireBridgeSecret(req, res, next) {
  const secret = process.env.BRIDGE_SECRET;
  if (!secret) {
    res.status(503).json({ error: "BRIDGE_SECRET not configured" });
    return;
  }
  const auth = req.headers.authorization || "";
  if (auth !== `Bearer ${secret}`) {
    res.status(401).json({ error: "unauthorized" });
    return;
  }
  next();
}

const app = express();
app.use(express.json());

const port = Number(process.env.BRIDGE_PORT ?? 3000);

app.get("/health", (_req, res) => {
  res.json({ status: "ok", service: "bridge" });
});

/** Delay aleatorio entre min y max milisegundos. */
function randomDelay(minMs, maxMs) {
  return new Promise((r) => setTimeout(r, minMs + Math.random() * (maxMs - minMs)));
}

app.post("/send", requireBridgeSecret, async (req, res) => {
  const { tenant, to, text } = req.body || {};
  if (!TENANTS.includes(tenant)) {
    res.status(400).json({ error: "invalid tenant" });
    return;
  }
  if (!to || typeof text !== "string") {
    res.status(400).json({ error: "missing to or text" });
    return;
  }
  const sock = socks.get(tenant);
  if (!sock?.user?.id) {
    res.status(503).json({ error: "whatsapp session not ready" });
    return;
  }
  try {
    const jid = toWhatsAppJid(to);
    // Muestra "escribiendo..." entre 3 y 5 segundos antes de enviar
    await sock.sendPresenceUpdate("composing", jid);
    await randomDelay(3000, 5000);
    await sock.sendPresenceUpdate("paused", jid);
    await sock.sendMessage(jid, { text });
    res.json({ ok: true });
  } catch (err) {
    bridgeLog.error({ err, tenant }, "sendMessage failed");
    res.status(500).json({ error: "send failed" });
  }
});

app.listen(port, () => {
  bridgeLog.info({ port }, "bridge http listening");
  for (const t of TENANTS) {
    startTenantBaileys(t).catch((err) => {
      bridgeLog.error({ err, tenant: t }, "initial baileys start failed");
    });
  }
});
