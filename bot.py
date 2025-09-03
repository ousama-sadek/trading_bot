# -*- coding: utf-8 -*-
"""
بوت إشارات فوركس شبيه Noro — رسائل منفصلة، تجهيز قبل دقيقة ثم إشارة دخول.
مصدر البيانات: Twelve Data (interval=1min)
أوامر التلغرام: /start /help /setpairs /startscan /stop /pair EUR/USD
"""

import time
import datetime as dt
import requests
import pandas as pd

# ========== إعدادات التلغرام (مدموجة كما طلبت) ==========
TELEGRAM_TOKEN = "7566190734:AAHdK0wsdXAHunDSDD5W-ZdBE1qQ4zjHDpQ"
TELEGRAM_CHAT_ID = 8197627648  # ID الذي أعطيتني إياه

# ========== إعدادات Twelve Data (مدموج) ==========
TD_API_KEY = "559e62a292ff4377bbdf5d2ab7431bb9"  # مفتاحك
TD_BASE = "https://api.twelvedata.com/time_series"  # 1min candles

# ========== إعدادات عامة ==========
INTERVAL = "1min"     # إطار التحليل
OUTPUTSIZE = 120       # عدد الشموع المسترجعة
PREP_DELAY = 60        # ثانية: تجهيز قبل دقيقة
COOLDOWN_BETWEEN_PAIRS = 2  # تهدئة بين رسائل الأزواج

# أزواج افتراضية (تقدر تغيّرها من /setpairs)
PAIRS = ["EUR/USD", "GBP/USD", "USD/JPY", "EUR/GBP", "AUD/USD"]

# حالة التشغيل التلقائي
AUTO_SCAN = False


# ========== دوال مساعدة ==========
def tg_send(text):
    """إرسال رسالة تليجرام نصية"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        requests.post(url, data=data, timeout=15)
    except Exception as e:
        print("Telegram send error:", e)


def tg_get_updates(offset=None, timeout=25):
    """استقبال أوامر تليجرام (Polling)"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    try:
        r = requests.get(url, params=params, timeout=timeout + 5)
        return r.json().get("result", [])
    except Exception:
        return []


def get_series(symbol: str, interval=INTERVAL, outputsize=OUTPUTSIZE) -> pd.DataFrame | None:
    """جلب شموع دقيقة من Twelve Data لزوج بالشكل 'EUR/USD'"""
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": outputsize,
        "format": "JSON",
        "apikey": TD_API_KEY,
        "order": "ASC",
    }
    try:
        r = requests.get(TD_BASE, params=params, timeout=20)
        js = r.json()
        if "values" not in js:
            # رسالة خطأ من المزود
            return None
        rows = js["values"]
        df = pd.DataFrame(rows)
        # أعمدة: datetime, open, high, low, close, volume (كنصوص)
        for col in ("open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.sort_values("datetime").reset_index(drop=True)
        return df
    except Exception as e:
        print("Fetch error", symbol, e)
        return None


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = (delta.clip(lower=0)).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50)


def build_signal(df: pd.DataFrame) -> tuple[str, str]:
    """
    يُرجع (التجهيز, الإشارة النهائية)
    المنطق:
      - ترند: EMA20 مقابل EMA50
      - زخم: RSI(14)
      - تأكيد: كسر/ارتداد بسيط من نطاق الحركة
    """
    close = df["close"]
    df["EMA20"] = ema(close, 20)
    df["EMA50"] = ema(close, 50)
    df["RSI"] = rsi(close, 14)

    # نطاق حركة بسيط (انحراف معياري على 20)
    df["SMA20"] = close.rolling(20).mean()
    df["STD20"] = close.rolling(20).std()
    df["UP"] = df["SMA20"] + 2 * df["STD20"]
    df["DN"] = df["SMA20"] - 2 * df["STD20"]

    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else last

    # --- رسالة تجهيز (تلميح مسبق للصفقة بعد دقيقة) ---
    prep_hint = "⏳ تجهيز: ترقب إشارة بعد دقيقة."
    if last["EMA20"] > last["EMA50"] and last["RSI"] <= 40:
        prep_hint = "⏳ تجهيز شراء محتمل (ترند صاعد + RSI منخفض)."
    elif last["EMA20"] < last["EMA50"] and last["RSI"] >= 60:
        prep_hint = "⏳ تجهيز بيع محتمل (ترند هابط + RSI مرتفع)."

    # --- قرار نهائي بعد دقيقة ---
    # قاعدة بسيطة متينة:
    # شراء: ترند صاعد (EMA20>EMA50) + RSI<50 + ارتداد من DN أو اختراق SMA20 لأعلى
    # بيع : ترند هابط (EMA20<EMA50) + RSI>50 + ارتداد من UP أو كسر SMA20 لأسفل
    decision = "⚖️ محايد"
    if last["EMA20"] > last["EMA50"]:
        if last["RSI"] < 50 and (last["close"] <= last["DN"] or last["close"] > last["SMA20"] > prev["SMA20"]):
            decision = "⬆️ شراء"
    elif last["EMA20"] < last["EMA50"]:
        if last["RSI"] > 50 and (last["close"] >= last["UP"] or last["close"] < last["SMA20"] < prev["SMA20"]):
            decision = "⬇️ بيع"

    # معلومات إضافية مختصرة
    info = f"(RSI={last['RSI']:.1f} | EMA20{'>' if last['EMA20']>last['EMA50'] else '<'}EMA50)"
    return prep_hint, f"{decision} {info}"


def analyze_pair_once(symbol: str, prepare_only: bool = False):
    """يرسل تجهيز ثم (اختياريًا) ينتظر دقيقة ويرسل الإشارة النهائية"""
    df = get_series(symbol)
    if df is None or len(df) < 60:
        tg_send(f"⚠️ {symbol}: تعذّر جلب البيانات الآن.")
        return

    price = df["close"].iloc[-1]
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    prep_txt, final_txt = build_signal(df)

    # رسالة تجهيز فورية
    tg_send(f"🔔 {symbol}\n⏰ {now}\nآخر سعر: {price}\n{prep_txt}\n(الإشارة بعد 1 دقيقة)")

    if prepare_only:
        return

    # انتظر دقيقة
    time.sleep(PREP_DELAY)

    # جلب آخر بيانات بعد دقيقة لاتخاذ القرار النهائي
    df2 = get_series(symbol)
    if df2 is None or len(df2) < 60:
        tg_send(f"⚠️ {symbol}: لم أستطع تحديث الشموع بعد دقيقة.")
        return

    price2 = df2["close"].iloc[-1]
    now2 = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _, final_txt2 = build_signal(df2)
    tg_send(f"✅ {symbol} — إشارة الدخول\n⏰ {now2}\nآخر سعر: {price2}\n{final_txt2}")


# ========== أوامر التلغرام ==========
HELP = (
    "🤖 أوامر البوت:\n"
    "/start — بدء وتشغيل البوت\n"
    "/help — هذه المساعدة\n"
    "/setpairs EUR/USD,GBP/USD,USD/JPY — تغيير الأزواج\n"
    "/startscan — بدء المسح التلقائي (كل زوج تجهيز ثم دخول بعد دقيقة)\n"
    "/stop — إيقاف المسح التلقائي\n"
    "/pair EUR/USD — تحليل زوج واحد الآن (تجهيز + دخول بعد دقيقة)\n"
)

def handle_command(text: str) -> str | None:
    global PAIRS, AUTO_SCAN
    cmd = text.strip()

    if cmd.startswith("/start"):
        AUTO_SCAN = False
        return "🚀 البوت شغّال. استخدم /help للأوامر."

    if cmd.startswith("/help"):
        return HELP

    if cmd.startswith("/setpairs"):
        # مثال: /setpairs EUR/USD,GBP/USD,USD/JPY
        body = cmd[len("/setpairs"):].strip()
        if not body:
            return "اكتب هكذا: /setpairs EUR/USD,GBP/USD"
        pairs = [p.strip().upper().replace("-", "/") for p in body.split(",") if "/" in p]
        if not pairs:
            return "تنسيق غير صحيح. مثال: /setpairs EUR/USD,GBP/USD"
        PAIRS = pairs
        return f"✅ تم ضبط الأزواج: {', '.join(PAIRS)}"

    if cmd.startswith("/startscan"):
        AUTO_SCAN = True
        return f"✅ بدء المسح. الأزواج: {', '.join(PAIRS)}"

    if cmd.startswith("/stop"):
        AUTO_SCAN = False
        return "⛔ تم إيقاف المسح التلقائي."

    if cmd.lower().startswith("/pair"):
        # /pair EUR/USD
        parts = cmd.split(None, 1)
        if len(parts) != 2:
            return "اكتب: /pair EUR/USD"
        symbol = parts[1].strip().upper().replace("-", "/")
        if "/" not in symbol:
            return "صيغة الزوج غير صحيحة. مثال: /pair EUR/USD"
        # تنفيذ التحليل لهذا الزوج (تجهيز + دخول بعد دقيقة) في نفس الخيط
        analyze_pair_once(symbol, prepare_only=False)
        return None  # أرسلنا الردود داخل الدالة

    return "أمر غير معروف. اكتب /help."


# ========== الحلقة الرئيسية ==========
def main():
    tg_send("✅ تم تشغيل البوت. اكتب /help للمساعدة.")
    offset = None
    last_cycle = 0

    while True:
        now = time.time()

        # 1) لو المسح التلقائي مفعّل: نمشي على الأزواج واحد واحد
        if AUTO_SCAN and now - last_cycle >= 1:  # نتحكم بالتوقيت من داخل الدالة عبر sleep دقيقة
            last_cycle = now
            for sym in PAIRS:
                analyze_pair_once(sym, prepare_only=False)
                time.sleep(COOLDOWN_BETWEEN_PAIRS)

        # 2) استقبل أوامر التلغرام
        updates = tg_get_updates(offset=offset, timeout=20)
        for u in updates:
            offset = u["update_id"] + 1
            msg = u.get("message") or {}
            chat = msg.get("chat") or {}
            text = msg.get("text") or ""
            chat_id = chat.get("id")

            # نقبل أوامر صاحب الـID فقط
            if chat_id != TELEGRAM_CHAT_ID:
                continue

            reply = handle_command(text)
            if reply:
                tg_send(reply)

        time.sleep(1)


if __name__ == "__main__":
    main()
