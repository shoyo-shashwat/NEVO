# whatsapp/routes.py
#
# Twilio WhatsApp citizen-intake webhook.
#
# Flow: Twilio inbound message -> signature verification -> dedupe by
# MessageSid -> (voice) ElevenLabs transcription -> Groq structured
# extraction -> Cohere embedding + pgvector similarity match
# (app/services/demand_matching.py, the SAME service the citizen web app
# uses) -> a real Report/DemandCluster/Contribution -> TwiML confirmation.
#
# This channel makes one automatic join-vs-new-cluster decision instead of
# the web app's manual "join / show candidates / start new" screen, because
# plain WhatsApp text has no equivalent interactive picker:
#   auto_suggest tier (very high similarity)  -> join that cluster automatically
#   show_candidates / no_match tier           -> start a new cluster
# That is the one deliberate UX simplification here — the AI extraction,
# embedding, similarity search, and clustering underneath are unchanged from
# the real pipeline (no fake/stubbed matching).
#
# Twilio credentials required for REAL delivery: TWILIO_ACCOUNT_SID,
# TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_NUMBER (see .env.example). Without a
# TWILIO_AUTH_TOKEN, the webhook still runs but cannot verify the caller is
# actually Twilio — see _validate_twilio_signature(). Use /whatsapp/test-send
# (gated behind WHATSAPP_TEST_ADAPTER=1) to exercise the real intake
# pipeline locally without any Twilio credentials at all.

import hashlib
import logging
import os
import re
from datetime import datetime, timedelta, timezone

from flask import request, Response, jsonify
from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse

from app.whatsapp import whatsapp_bp
from app.extensions import db, csrf
from app.models.shared import Country, AdministrativeRegion, Category, EventLog
from app.models.citizen_models import Report, Contribution, Evidence
from app.models.demand_cluster import DemandCluster
from app.models.whatsapp_models import WhatsAppMessageLog
from app.models.ai_models import log_ai_call
from app.services import groq_client
from app.services.demand_matching import find_similar_clusters, store_cluster_embedding

logger = logging.getLogger(__name__)

# How long an unfinished (Draft) conversation stays open for a follow-up
# reply to continue, instead of starting a brand-new, unrelated report.
CONVERSATION_WINDOW_MINUTES = 45

# After this many total messages in one conversation without resolving both
# category and location, stop asking and save what we have for a human
# reviewer — never loop forever re-asking the same question (see the live
# repro: "what type of problem" / "where is it" alternating indefinitely
# because each reply used to be extracted in total isolation from the last).
MAX_CLARIFICATION_TURNS = 2


# ---------------------------------------------------------------------------
# Multilingual bot replies — English, Hindi, Marathi, Kannada, Gujarati,
# Tamil, Bengali. A contact's preferred language (set explicitly via "reply
# in Hindi" etc., persisted on WhatsAppMessageLog.preferred_language) picks
# which of these is used; falling back to whatever language Groq detected
# in the citizen's own message, then English — never a raw English string
# silently shown to someone who explicitly asked for another language.
# ---------------------------------------------------------------------------

SUPPORTED_LANGUAGES = ("en", "hi", "mr", "kn", "gu", "ta", "bn")

_LANGUAGE_ALIASES = {
    "english": "en", "eng": "en", "angrezi": "en",
    "hindi": "hi", "हिंदी": "hi", "हिन्दी": "hi",
    "marathi": "mr", "मराठी": "mr",
    "kannada": "kn", "ಕನ್ನಡ": "kn",
    "gujarati": "gu", "ગુજરાતી": "gu",
    "tamil": "ta", "தமிழ்": "ta",
    "bengali": "bn", "bangla": "bn", "বাংলা": "bn",
}

_GREETING_WORDS = {
    "hi", "hii", "hiii", "hello", "helo", "hey", "yo", "start", "help",
    "menu", "namaste", "namaskar", "vanakkam",
}

_LANG_SWITCH_RE = re.compile(
    r"(?:speak|reply|talk|respond|answer)\s+(?:to\s+me\s+)?(?:back\s+)?in\s+([a-zA-Zऀ-ॿಀ-೿઀-૿஀-௿ঀ-৿]+)"
    r"|switch\s+to\s+([a-zA-Z]+)"
    r"|^([a-zA-Zऀ-ॿ]+)\s+(?:me\s+)?(?:bolo|bol|bat|jawab)",
    re.IGNORECASE,
)


def _detect_language_switch(text: str) -> str | None:
    """A contact explicitly asking to change reply language ("reply in
    Hindi", "switch to Tamil", "hindi me bolo") -> ISO code, else None.
    Deliberately narrow (a real command, not just mentioning a language
    name in a report) so it never misfires on an actual issue description."""
    m = _LANG_SWITCH_RE.search(text.strip())
    if not m:
        return None
    candidate = next((g for g in m.groups() if g), "").strip().lower()
    return _LANGUAGE_ALIASES.get(candidate)


def _is_greeting(text: str) -> bool:
    """A bare greeting/help request, not an actual issue report — short
    message whose words are entirely greeting/help vocabulary."""
    t = text.strip().lower().rstrip("!.?")
    if not t or len(t) > 25:
        return False
    words = re.split(r"[\s,]+", t)
    return all(w in _GREETING_WORDS for w in words)


def _get_preferred_language(from_number_hash: str) -> str | None:
    """The most recent explicit language choice this contact made, if any."""
    row = (
        WhatsAppMessageLog.query
        .filter(
            WhatsAppMessageLog.from_number_hash == from_number_hash,
            WhatsAppMessageLog.preferred_language.isnot(None),
        )
        .order_by(WhatsAppMessageLog.created_at.desc())
        .first()
    )
    return row.preferred_language if row else None


_WELCOME_MESSAGES = {
    "en": (
        "👋 *Welcome to NEVO!*\n\n"
        "Report local civic issues in seconds — no forms, no app, no login.\n\n"
        "📝 Just *describe your problem* in text, 🎙️ a voice note, or 📸 a photo with a caption:\n"
        "  💧 Water & Sanitation   🕳️ Roads & Transport\n"
        "  🏥 Healthcare Access   💡 Electricity & Utilities\n"
        "  📚 Education Access   🗑️ Waste & Environment\n\n"
        "We'll ask for your *location* if needed, then confirm once it's reported ✅\n\n"
        "🌐 Message me in *any Indian language* — Hindi, Marathi, Kannada, Gujarati, Tamil, "
        "Bengali, or English. Say \"reply in Hindi\" (or any language) any time to switch.\n\n"
        "Just type your issue to get started 👇"
    ),
    "hi": (
        "👋 *NEVO में आपका स्वागत है!*\n\n"
        "स्थानीय नागरिक समस्याएं सेकंडों में दर्ज करें — कोई फॉर्म नहीं, कोई ऐप नहीं, कोई लॉगिन नहीं।\n\n"
        "📝 अपनी समस्या टेक्स्ट में बताएं, 🎙️ वॉइस नोट भेजें, या 📸 कैप्शन के साथ फोटो भेजें:\n"
        "  💧 पानी और स्वच्छता   🕳️ सड़क और परिवहन\n"
        "  🏥 स्वास्थ्य सेवा   💡 बिजली और उपयोगिताएँ\n"
        "  📚 शिक्षा   🗑️ कचरा और पर्यावरण\n\n"
        "ज़रूरत पड़ने पर हम आपका *स्थान* पूछेंगे, फिर रिपोर्ट दर्ज होने की पुष्टि करेंगे ✅\n\n"
        "🌐 मुझे *किसी भी भारतीय भाषा* में लिखें — हिंदी, मराठी, कन्नड़, गुजराती, तमिल, बंगाली या अंग्रेज़ी। "
        "भाषा बदलने के लिए कभी भी \"हिंदी में जवाब दो\" लिखें।\n\n"
        "शुरू करने के लिए अपनी समस्या टाइप करें 👇"
    ),
    "mr": (
        "👋 *NEVO मध्ये आपले स्वागत आहे!*\n\n"
        "स्थानिक नागरी समस्या काही सेकंदांत नोंदवा — कोणताही फॉर्म नाही, अ‍ॅप नाही, लॉगिन नाही.\n\n"
        "📝 तुमची समस्या मजकुरात सांगा, 🎙️ व्हॉइस नोट पाठवा, किंवा 📸 कॅप्शनसह फोटो पाठवा:\n"
        "  💧 पाणी व स्वच्छता   🕳️ रस्ते व वाहतूक\n"
        "  🏥 आरोग्य सेवा   💡 वीज व सुविधा\n"
        "  📚 शिक्षण   🗑️ कचरा व पर्यावरण\n\n"
        "गरज असल्यास आम्ही तुमचे *ठिकाण* विचारू, नंतर तक्रार नोंदवल्याची पुष्टी करू ✅\n\n"
        "🌐 मला *कोणत्याही भारतीय भाषेत* लिहा — हिंदी, मराठी, कन्नड, गुजराती, तमिळ, बंगाली किंवा इंग्रजी. "
        "भाषा बदलण्यासाठी कधीही \"मराठीत उत्तर द्या\" असे लिहा.\n\n"
        "सुरू करण्यासाठी तुमची समस्या टाइप करा 👇"
    ),
    "kn": (
        "👋 *NEVO ಗೆ ಸ್ವಾಗತ!*\n\n"
        "ಸ್ಥಳೀಯ ನಾಗರಿಕ ಸಮಸ್ಯೆಗಳನ್ನು ಸೆಕೆಂಡುಗಳಲ್ಲಿ ವರದಿ ಮಾಡಿ — ಯಾವುದೇ ಫಾರ್ಮ್ ಇಲ್ಲ, ಆಪ್ ಇಲ್ಲ, ಲಾಗಿನ್ ಇಲ್ಲ.\n\n"
        "📝 ನಿಮ್ಮ ಸಮಸ್ಯೆಯನ್ನು ಪಠ್ಯದಲ್ಲಿ ಬರೆಯಿರಿ, 🎙️ ಧ್ವನಿ ಸಂದೇಶ ಕಳುಹಿಸಿ, ಅಥವಾ 📸 ಶೀರ್ಷಿಕೆಯೊಂದಿಗೆ ಫೋಟೋ ಕಳುಹಿಸಿ:\n"
        "  💧 ನೀರು ಮತ್ತು ನೈರ್ಮಲ್ಯ   🕳️ ರಸ್ತೆ ಮತ್ತು ಸಾರಿಗೆ\n"
        "  🏥 ಆರೋಗ್ಯ ಸೇವೆ   💡 ವಿದ್ಯುತ್ ಮತ್ತು ಸೌಲಭ್ಯಗಳು\n"
        "  📚 ಶಿಕ್ಷಣ   🗑️ ತ್ಯಾಜ್ಯ ಮತ್ತು ಪರಿಸರ\n\n"
        "ಅಗತ್ಯವಿದ್ದರೆ ನಾವು ನಿಮ್ಮ *ಸ್ಥಳ* ಕೇಳುತ್ತೇವೆ, ನಂತರ ವರದಿಯಾಗಿದೆ ಎಂದು ಖಚಿತಪಡಿಸುತ್ತೇವೆ ✅\n\n"
        "🌐 ನನಗೆ *ಯಾವುದೇ ಭಾರತೀಯ ಭಾಷೆಯಲ್ಲಿ* ಬರೆಯಿರಿ — ಹಿಂದಿ, ಮರಾಠಿ, ಕನ್ನಡ, ಗುಜರಾತಿ, ತಮಿಳು, ಬಂಗಾಳಿ ಅಥವಾ ಇಂಗ್ಲಿಷ್. "
        "ಭಾಷೆ ಬದಲಾಯಿಸಲು ಯಾವಾಗ ಬೇಕಾದರೂ \"ಕನ್ನಡದಲ್ಲಿ ಉತ್ತರಿಸಿ\" ಎಂದು ಬರೆಯಿರಿ.\n\n"
        "ಪ್ರಾರಂಭಿಸಲು ನಿಮ್ಮ ಸಮಸ್ಯೆಯನ್ನು ಟೈಪ್ ಮಾಡಿ 👇"
    ),
    "gu": (
        "👋 *NEVO માં તમારું સ્વાગત છે!*\n\n"
        "સ્થાનિક નાગરિક સમસ્યાઓ સેકંડોમાં નોંધાવો — કોઈ ફોર્મ નહીં, એપ નહીં, લોગિન નહીં.\n\n"
        "📝 તમારી સમસ્યા ટેક્સ્ટમાં લખો, 🎙️ વોઇસ નોટ મોકલો, અથવા 📸 કેપ્શન સાથે ફોટો મોકલો:\n"
        "  💧 પાણી અને સ્વચ્છતા   🕳️ રસ્તા અને પરિવહન\n"
        "  🏥 આરોગ્ય સેવા   💡 વીજળી અને સુવિધાઓ\n"
        "  📚 શિક્ષણ   🗑️ કચરો અને પર્યાવરણ\n\n"
        "જરૂર પડે તો અમે તમારું *સ્થાન* પૂછીશું, પછી નોંધાયાની પુષ્ટિ કરીશું ✅\n\n"
        "🌐 મને *કોઈપણ ભારતીય ભાષામાં* લખો — હિન્દી, મરાઠી, કન્નડ, ગુજરાતી, તમિલ, બંગાળી અથવા અંગ્રેજી. "
        "ભાષા બદલવા માટે કોઈપણ સમયે \"ગુજરાતીમાં જવાબ આપો\" લખો.\n\n"
        "શરૂ કરવા માટે તમારી સમસ્યા ટાઈપ કરો 👇"
    ),
    "ta": (
        "👋 *NEVO-வுக்கு வரவேற்கிறோம்!*\n\n"
        "உள்ளூர் குடிமக்கள் பிரச்சினைகளை நொடிகளில் புகார் செய்யுங்கள் — படிவம் இல்லை, ஆப் இல்லை, லாகின் இல்லை.\n\n"
        "📝 உங்கள் பிரச்சினையை உரையாக எழுதுங்கள், 🎙️ குரல் குறிப்பு அனுப்புங்கள், அல்லது 📸 தலைப்புடன் புகைப்படம் அனுப்புங்கள்:\n"
        "  💧 நீர் & சுகாதாரம்   🕳️ சாலை & போக்குவரத்து\n"
        "  🏥 சுகாதார அணுகல்   💡 மின்சாரம் & பயன்பாடுகள்\n"
        "  📚 கல்வி   🗑️ கழிவு & சுற்றுச்சூழல்\n\n"
        "தேவைப்பட்டால் உங்கள் *இடம்* கேட்போம், பின்னர் புகார் பதிவானதை உறுதிசெய்வோம் ✅\n\n"
        "🌐 எனக்கு *எந்த இந்திய மொழியிலும்* எழுதுங்கள் — இந்தி, மராத்தி, கன்னடம், குஜராத்தி, தமிழ், பெங்காலி அல்லது ஆங்கிலம். "
        "மொழியை மாற்ற எப்போது வேண்டுமானாலும் \"தமிழில் பதில் சொல்\" என எழுதுங்கள்.\n\n"
        "தொடங்க உங்கள் பிரச்சினையை டைப் செய்யுங்கள் 👇"
    ),
    "bn": (
        "👋 *NEVO-তে স্বাগতম!*\n\n"
        "স্থানীয় নাগরিক সমস্যা সেকেন্ডে রিপোর্ট করুন — কোনো ফর্ম নেই, অ্যাপ নেই, লগইন নেই।\n\n"
        "📝 আপনার সমস্যা টেক্সটে লিখুন, 🎙️ ভয়েস নোট পাঠান, বা 📸 ক্যাপশন দিয়ে ছবি পাঠান:\n"
        "  💧 পানি ও স্যানিটেশন   🕳️ রাস্তা ও পরিবহন\n"
        "  🏥 স্বাস্থ্যসেবা   💡 বিদ্যুৎ ও ইউটিলিটি\n"
        "  📚 শিক্ষা   🗑️ বর্জ্য ও পরিবেশ\n\n"
        "প্রয়োজনে আমরা আপনার *অবস্থান* জিজ্ঞাসা করব, তারপর রিপোর্ট হয়েছে তা নিশ্চিত করব ✅\n\n"
        "🌐 আমাকে *যেকোনো ভারতীয় ভাষায়* লিখুন — হিন্দি, মারাঠি, কন্নড়, গুজরাটি, তামিল, বাংলা বা ইংরেজি। "
        "ভাষা পরিবর্তনের জন্য যেকোনো সময় \"বাংলায় উত্তর দাও\" লিখুন।\n\n"
        "শুরু করতে আপনার সমস্যাটি টাইপ করুন 👇"
    ),
}

_LANGUAGE_SELF_NAMES = {
    "en": "English", "hi": "हिंदी", "mr": "मराठी", "kn": "ಕನ್ನಡ",
    "gu": "ગુજરાતી", "ta": "தமிழ்", "bn": "বাংলা",
}

_BOT_MESSAGES = {
    "lang_switched": {
        "en": "👍 Okay, I'll reply in {lang} from now on.",
        "hi": "👍 ठीक है, अब मैं {lang} में जवाब दूंगा।",
        "mr": "👍 ठीक आहे, आता मी {lang} मध्ये उत्तर देईन.",
        "kn": "👍 ಸರಿ, ಇನ್ನು ಮುಂದೆ ನಾನು {lang} ನಲ್ಲಿ ಉತ್ತರಿಸುತ್ತೇನೆ.",
        "gu": "👍 ભલે, હવે હું {lang} માં જવાબ આપીશ.",
        "ta": "👍 சரி, இப்போதிலிருந்து நான் {lang} இல் பதிலளிக்கிறேன்.",
        "bn": "👍 ঠিক আছে, এখন থেকে আমি {lang} এ উত্তর দেব।",
    },
    "need_description": {
        "en": "📝 Please describe the community need you'd like to report — in text, a voice note, or a photo with a short caption.",
        "hi": "📝 कृपया वह समस्या बताएं जो आप दर्ज करना चाहते हैं — टेक्स्ट, वॉइस नोट, या कैप्शन के साथ फोटो में।",
        "mr": "📝 कृपया तुम्ही नोंदवायची असलेली समस्या सांगा — मजकूर, व्हॉइस नोट, किंवा कॅप्शनसह फोटोमध्ये.",
        "kn": "📝 ದಯವಿಟ್ಟು ನೀವು ವರದಿ ಮಾಡಲು ಬಯಸುವ ಸಮಸ್ಯೆಯನ್ನು ವಿವರಿಸಿ — ಪಠ್ಯ, ಧ್ವನಿ ಸಂದೇಶ, ಅಥವಾ ಶೀರ್ಷಿಕೆಯೊಂದಿಗೆ ಫೋಟೋದಲ್ಲಿ.",
        "gu": "📝 કૃપા કરી તમે નોંધાવવા માંગતા હો તે સમસ્યા જણાવો — ટેક્સ્ટ, વોઇસ નોટ, અથવા કેપ્શન સાથે ફોટોમાં.",
        "ta": "📝 நீங்கள் புகார் செய்ய விரும்பும் பிரச்சினையை விவரிக்கவும் — உரை, குரல் குறிப்பு, அல்லது தலைப்புடன் புகைப்படத்தில்.",
        "bn": "📝 আপনি যে সমস্যাটি রিপোর্ট করতে চান তা বর্ণনা করুন — টেক্সট, ভয়েস নোট, বা ক্যাপশন সহ ছবিতে।",
    },
    "unsupported_media": {
        "en": "We can read text, photos, or voice notes right now. Please send your community need as a text, photo, or voice message.",
        "hi": "हम अभी टेक्स्ट, फोटो या वॉइस नोट पढ़ सकते हैं। कृपया अपनी समस्या टेक्स्ट, फोटो या वॉइस मैसेज के रूप में भेजें।",
        "mr": "आम्ही आता मजकूर, फोटो किंवा व्हॉइस नोट वाचू शकतो. कृपया तुमची समस्या मजकूर, फोटो किंवा व्हॉइस मेसेज म्हणून पाठवा.",
        "kn": "ನಾವು ಈಗ ಪಠ್ಯ, ಫೋಟೋ ಅಥವಾ ಧ್ವನಿ ಸಂದೇಶಗಳನ್ನು ಓದಬಹುದು. ದಯವಿಟ್ಟು ನಿಮ್ಮ ಸಮಸ್ಯೆಯನ್ನು ಪಠ್ಯ, ಫೋಟೋ ಅಥವಾ ಧ್ವನಿ ಸಂದೇಶವಾಗಿ ಕಳುಹಿಸಿ.",
        "gu": "અમે અત્યારે ટેક્સ્ટ, ફોટો અથવા વોઇસ નોટ વાંચી શકીએ છીએ. કૃપા કરી તમારી સમસ્યા ટેક્સ્ટ, ફોટો અથવા વોઇસ મેસેજ તરીકે મોકલો.",
        "ta": "நாங்கள் இப்போது உரை, புகைப்படம் அல்லது குரல் குறிப்புகளைப் படிக்க முடியும். உங்கள் பிரச்சினையை உரை, புகைப்படம் அல்லது குரல் செய்தியாக அனுப்பவும்.",
        "bn": "আমরা এখন টেক্সট, ছবি বা ভয়েস নোট পড়তে পারি। আপনার সমস্যাটি টেক্সট, ছবি বা ভয়েস মেসেজ হিসেবে পাঠান।",
    },
    "processing_error": {
        "en": "😕 Sorry, something went wrong processing your message. Please try again shortly.",
        "hi": "😕 माफ़ करें, आपका मैसेज प्रोसेस करने में कुछ गड़बड़ हुई। कृपया थोड़ी देर बाद फिर से प्रयास करें।",
        "mr": "😕 माफ करा, तुमचा मेसेज प्रोसेस करताना काहीतरी चुकले. कृपया थोड्या वेळाने पुन्हा प्रयत्न करा.",
        "kn": "😕 ಕ್ಷಮಿಸಿ, ನಿಮ್ಮ ಸಂದೇಶವನ್ನು ಪ್ರಕ್ರಿಯೆಗೊಳಿಸುವಲ್ಲಿ ಏನೋ ತಪ್ಪಾಗಿದೆ. ದಯವಿಟ್ಟು ಸ್ವಲ್ಪ ಸಮಯದ ನಂತರ ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ.",
        "gu": "😕 માફ કરશો, તમારો સંદેશ પ્રોસેસ કરવામાં કંઈક ગડબડ થઈ. કૃપા કરી થોડી વાર પછી ફરી પ્રયાસ કરો.",
        "ta": "😕 மன்னிக்கவும், உங்கள் செய்தியை செயலாக்குவதில் ஏதோ தவறு நடந்தது. சிறிது நேரம் கழித்து மீண்டும் முயற்சிக்கவும்.",
        "bn": "😕 দুঃখিত, আপনার বার্তা প্রক্রিয়া করতে সমস্যা হয়েছে। অনুগ্রহ করে কিছুক্ষণ পরে আবার চেষ্টা করুন।",
    },
    "join_existing": {
        "en": "✅ *Thank you!* This matches a community issue already reported by *{n}* other people nearby. You've been added to it — we'll keep this linked to your number so you can be updated.",
        "hi": "✅ *धन्यवाद!* यह पास के *{n}* अन्य लोगों द्वारा पहले से दर्ज एक सामुदायिक समस्या से मेल खाता है। आपको इसमें जोड़ दिया गया है — हम इसे आपके नंबर से जोड़ रखेंगे ताकि आपको सूचित किया जा सके।",
        "mr": "✅ *धन्यवाद!* हे जवळपासच्या *{n}* इतर लोकांनी आधीच नोंदवलेल्या सामुदायिक समस्येशी जुळते. तुम्हाला त्यात जोडले आहे — पुढील माहितीसाठी हे तुमच्या नंबरशी जोडलेले राहील.",
        "kn": "✅ *ಧನ್ಯವಾದಗಳು!* ಇದು ಹತ್ತಿರದ *{n}* ಇತರ ಜನರು ಈಗಾಗಲೇ ವರದಿ ಮಾಡಿದ ಸಮುದಾಯ ಸಮಸ್ಯೆಗೆ ಹೊಂದಿಕೆಯಾಗುತ್ತದೆ. ನಿಮ್ಮನ್ನು ಅದಕ್ಕೆ ಸೇರಿಸಲಾಗಿದೆ — ಅಪ್‌ಡೇಟ್‌ಗಳಿಗಾಗಿ ಇದನ್ನು ನಿಮ್ಮ ಸಂಖ್ಯೆಗೆ ಲಿಂಕ್ ಮಾಡಿ ಇಡುತ್ತೇವೆ.",
        "gu": "✅ *આભાર!* આ નજીકના *{n}* અન્ય લોકો દ્વારા પહેલેથી નોંધાયેલ સામુદાયિક સમસ્યા સાથે મેળ ખાય છે. તમને તેમાં ઉમેરવામાં આવ્યા છે — અપડેટ માટે આ તમારા નંબર સાથે જોડાયેલું રાખીશું.",
        "ta": "✅ *நன்றி!* இது அருகிலுள்ள *{n}* மற்றவர்களால் ஏற்கனவே புகாரளிக்கப்பட்ட ஒரு சமூகப் பிரச்சினையுடன் பொருந்துகிறது. நீங்கள் அதில் சேர்க்கப்பட்டுள்ளீர்கள் — புதுப்பிப்புகளுக்காக இதை உங்கள் எண்ணுடன் இணைத்து வைப்போம்.",
        "bn": "✅ *ধন্যবাদ!* এটি কাছাকাছি *{n}* জন অন্য মানুষ ইতিমধ্যে রিপোর্ট করা একটি সম্প্রদায় সমস্যার সাথে মিলে যায়। আপনাকে এতে যুক্ত করা হয়েছে — আপডেটের জন্য এটি আপনার নম্বরের সাথে যুক্ত রাখা হবে।",
    },
    "new_cluster": {
        "en": "✅ *Thank you!* Your report has been recorded as a new community issue. We'll let you know if other people report the same problem.",
        "hi": "✅ *धन्यवाद!* आपकी रिपोर्ट एक नई सामुदायिक समस्या के रूप में दर्ज कर ली गई है। यदि अन्य लोग भी यही समस्या बताते हैं, तो हम आपको सूचित करेंगे।",
        "mr": "✅ *धन्यवाद!* तुमची तक्रार नवीन सामुदायिक समस्या म्हणून नोंदवली गेली आहे. इतर लोकांनी हीच समस्या नोंदवली तर आम्ही तुम्हाला कळवू.",
        "kn": "✅ *ಧನ್ಯವಾದಗಳು!* ನಿಮ್ಮ ವರದಿಯನ್ನು ಹೊಸ ಸಮುದಾಯ ಸಮಸ್ಯೆಯಾಗಿ ದಾಖಲಿಸಲಾಗಿದೆ. ಇತರರು ಅದೇ ಸಮಸ್ಯೆಯನ್ನು ವರದಿ ಮಾಡಿದರೆ ನಾವು ನಿಮಗೆ ತಿಳಿಸುತ್ತೇವೆ.",
        "gu": "✅ *આભાર!* તમારો રિપોર્ટ નવી સામુદાયિક સમસ્યા તરીકે નોંધાયો છે. જો અન્ય લોકો પણ આ જ સમસ્યા નોંધાવે, તો અમે તમને જણાવીશું.",
        "ta": "✅ *நன்றி!* உங்கள் அறிக்கை புதிய சமூகப் பிரச்சினையாக பதிவு செய்யப்பட்டுள்ளது. மற்றவர்களும் இதே பிரச்சினையை புகாரளித்தால் நாங்கள் உங்களுக்குத் தெரிவிப்போம்.",
        "bn": "✅ *ধন্যবাদ!* আপনার রিপোর্টটি একটি নতুন সম্প্রদায় সমস্যা হিসেবে রেকর্ড করা হয়েছে। অন্য কেউ একই সমস্যা রিপোর্ট করলে আমরা আপনাকে জানাব।",
    },
    "give_up": {
        "en": "✅ Thank you — we've recorded what you've shared so far. A reviewer will follow up if we need anything else.",
        "hi": "✅ धन्यवाद — आपने अब तक जो बताया है वह दर्ज कर लिया गया है। ज़रूरत होने पर एक रिव्यूअर आपसे संपर्क करेगा।",
        "mr": "✅ धन्यवाद — तुम्ही आतापर्यंत जे सांगितले ते नोंदवले गेले आहे. आणखी काही हवे असल्यास एक रिव्ह्यूअर संपर्क करेल.",
        "kn": "✅ ಧನ್ಯವಾದಗಳು — ನೀವು ಇಲ್ಲಿಯವರೆಗೆ ಹಂಚಿಕೊಂಡದ್ದನ್ನು ದಾಖಲಿಸಲಾಗಿದೆ. ಇನ್ನೇನಾದರೂ ಬೇಕಿದ್ದರೆ ಪರಿಶೀಲಕರು ಸಂಪರ್ಕಿಸುತ್ತಾರೆ.",
        "gu": "✅ આભાર — તમે અત્યાર સુધી જે જણાવ્યું તે નોંધાયું છે. વધુ જરૂર પડે તો રિવ્યુઅર તમારો સંપર્ક કરશે.",
        "ta": "✅ நன்றி — நீங்கள் இதுவரை பகிர்ந்ததை பதிவு செய்துள்ளோம். மேலும் தேவைப்பட்டால் ஒரு மதிப்பாய்வாளர் தொடர்பு கொள்வார்.",
        "bn": "✅ ধন্যবাদ — আপনি এখন পর্যন্ত যা শেয়ার করেছেন তা রেকর্ড করা হয়েছে। আরও প্রয়োজন হলে একজন পর্যালোচক যোগাযোগ করবেন।",
    },
}


def _bot_msg(key: str, reply_lang: str, **kwargs) -> str:
    d = _BOT_MESSAGES.get(key, {})
    template = d.get(reply_lang) or d.get("en") or ""
    return template.format(**kwargs) if kwargs else template


def _resolve_reply_language(preferred_language: str | None, detected_language: str | None) -> str:
    if preferred_language in SUPPORTED_LANGUAGES:
        return preferred_language
    if detected_language in SUPPORTED_LANGUAGES:
        return detected_language
    return "en"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _phone_hash(phone: str) -> str:
    return hashlib.sha256(phone.encode("utf-8")).hexdigest()[:32]


def _twiml(message: str) -> Response:
    resp = MessagingResponse()
    resp.message(message)
    return Response(str(resp), mimetype="text/xml")


def _validate_twilio_signature() -> bool:
    """
    True if this request is verified as genuinely from Twilio, OR if no
    TWILIO_AUTH_TOKEN is configured at all (local/dev — where the /test-send
    adapter is the supported way to simulate messages instead). False ONLY
    when an auth token IS configured and the signature check actually fails
    — that is the one case a request must be rejected outright, so a forged
    POST can never create a Report.
    """
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    if not auth_token:
        logger.warning("TWILIO_AUTH_TOKEN not set — WhatsApp webhook signature NOT verified (dev mode).")
        return True
    signature = request.headers.get("X-Twilio-Signature", "")
    validator = RequestValidator(auth_token)
    return validator.validate(request.url, request.form.to_dict(), signature)


def _download_media(media_url: str) -> tuple[bytes, str]:
    """Downloads any WhatsApp media file (Twilio-authenticated). Returns (bytes, content_type)."""
    import httpx

    account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    resp = httpx.get(media_url, auth=(account_sid, auth_token), timeout=30.0)
    resp.raise_for_status()
    return resp.content, (resp.headers.get("content-type") or "")


def _transcribe_voice_media(media_url: str) -> str:
    """Downloads a WhatsApp voice-note media file and transcribes it with the
    same ElevenLabs pipeline the web report flow uses."""
    from app.services.elevenlabs_client import transcribe_audio

    data, content_type = _download_media(media_url)
    result = transcribe_audio(data, mime_type=content_type or "audio/ogg")
    return result["text"]


class _InMemoryFile:
    """Minimal werkzeug-FileStorage-alike so a downloaded WhatsApp image can
    be handed to evidence_storage.save_evidence_photo() unchanged — that
    module deliberately only depends on `.mimetype`/`.read()`, never on
    Flask's request object, so this is the correct adapter rather than
    reshaping evidence_storage.py around a non-request caller."""

    def __init__(self, data: bytes, mimetype: str):
        self._data = data
        self.mimetype = mimetype

    def read(self) -> bytes:
        return self._data


def _attach_photo_evidence(report_id: str, uploaded_by: str, image_bytes: bytes, content_type: str) -> None:
    from app.services.evidence_storage import save_evidence_photo, EvidenceUploadError

    try:
        photo_url = save_evidence_photo(_InMemoryFile(image_bytes, content_type))
        db.session.add(Evidence(
            type="photo", url=photo_url, uploaded_by=uploaded_by,
            attached_to="Report", attached_to_id=report_id, report_id=report_id,
        ))
    except EvidenceUploadError as e:
        logger.warning("WhatsApp evidence photo rejected: %s", e)
    except Exception as e:
        logger.warning("WhatsApp evidence photo upload failed, continuing without it: %s", e)


def _find_open_draft(anonymous_token: str):
    """The most recent still-open (Draft) report from this WhatsApp number,
    if any, within CONVERSATION_WINDOW_MINUTES — the conversation this new
    message is most likely continuing, not starting fresh."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=CONVERSATION_WINDOW_MINUTES)
    return (
        Report.query
        .filter(
            Report.anonymous_token == anonymous_token,
            Report.status == "Draft",
            Report.created_at >= cutoff,
        )
        .order_by(Report.created_at.desc())
        .first()
    )


# ---------------------------------------------------------------------------
# Real intake pipeline — shared by the live Twilio webhook and the local
# test adapter, so both exercise the exact same extraction/matching logic.
# ---------------------------------------------------------------------------

def _process_intake_message(
    raw_text: str, from_number: str, log_row: WhatsAppMessageLog,
    image_bytes: bytes | None = None, image_content_type: str | None = None,
    preferred_language: str | None = None,
) -> str:
    country = Country.query.filter_by(code="IN").first()
    country_id = country.id if country else None
    anonymous_token = f"whatsapp:{log_row.from_number_hash}"

    # Continue an open conversation instead of starting a new, amnesiac one
    # each reply — this is the fix for a real bug caught live: a citizen
    # answering "where is it" then "what type of problem" got asked the
    # SAME two questions on an endless loop, because every message used to
    # be extracted alone, with zero memory of what they'd already said.
    existing_draft = _find_open_draft(anonymous_token)
    if existing_draft:
        conversation_text = (existing_draft.original_raw_input or "") + "\n" + raw_text
        prior_turns = WhatsAppMessageLog.query.filter(
            WhatsAppMessageLog.report_id == existing_draft.id
        ).count()
    else:
        conversation_text = raw_text
        prior_turns = 0
    turn_number = prior_turns + 1

    try:
        extracted = groq_client.extract_report_fields(conversation_text)
    except Exception as e:
        logger.warning("WhatsApp Groq extraction failed, falling back to Draft: %s", e)
        extracted = {
            "category": None, "location": None, "severity": None, "duration": None,
            "affected_group": None, "problem_summary": None, "problem_summary_en": None,
            "language_detected": None, "meta": {"complete": False, "missing_fields": ["category", "location"]},
        }
    meta = extracted.get("meta", {})

    category_id = None
    if extracted.get("category"):
        cat = Category.query.filter_by(code=extracted["category"]).first()
        category_id = cat.id if cat else None

    region_id = None
    if extracted.get("location") and country_id:
        region = (
            AdministrativeRegion.query.filter_by(country_id=country_id)
            .filter(AdministrativeRegion.name.ilike(f"%{extracted['location']}%"))
            .first()
        )
        region_id = region.id if region else None

    complete = bool(meta.get("complete"))
    give_up = (not complete) and turn_number >= MAX_CLARIFICATION_TURNS
    status = "Unclustered" if (complete or give_up) else "Draft"

    if existing_draft:
        report = existing_draft
        # original_raw_input stays exactly as first written — write-once,
        # same invariant as the web route — only the structured fields
        # accumulate as more of the conversation arrives.
        report.category_id = category_id or report.category_id
        report.region_id = region_id or report.region_id
        report.severity = extracted.get("severity") or report.severity
        report.duration = extracted.get("duration") or report.duration
        report.affected_group = extracted.get("affected_group") or report.affected_group
        report.problem_summary_en = extracted.get("problem_summary_en") or report.problem_summary_en
        report.original_language = report.original_language or extracted.get("language_detected")
        report.status = status
        category_id = report.category_id     # carry forward whatever earlier turns already resolved
        region_id = report.region_id
        problem_summary = extracted.get("problem_summary") or report.problem_summary_en or conversation_text
    else:
        report = Report(
            anonymous_token=anonymous_token,
            consent_given_at=datetime.now(timezone.utc),
            country_id=country_id or "country-in",
            region_id=region_id,
            category_id=category_id,
            original_raw_input=raw_text,              # write-once, same invariant as the web route
            original_language=extracted.get("language_detected"),
            problem_summary_en=extracted.get("problem_summary_en"),
            channel="messaging",
            severity=extracted.get("severity"),
            duration=extracted.get("duration"),
            affected_group=extracted.get("affected_group"),
            status=status,
        )
        db.session.add(report)
        problem_summary = extracted.get("problem_summary") or raw_text
    db.session.flush()
    log_row.report_id = report.id

    if image_bytes:
        _attach_photo_evidence(report.id, anonymous_token, image_bytes, image_content_type or "")

    log_ai_call(report_id=report.id, stage="extraction", provider="groq", model=_groq_model_name(),
                success=bool(extracted.get("category") or extracted.get("problem_summary")),
                structured_output={k: v for k, v in extracted.items() if k != "meta"})
    if not existing_draft:
        db.session.add(EventLog(report_id=report.id, stage="Submitted"))
    db.session.add(EventLog(report_id=report.id, stage="AIUnderstood",
                             metadata_={"summary": extracted.get("problem_summary", "")}))

    reply_lang = _resolve_reply_language(preferred_language, extracted.get("language_detected"))

    if status == "Draft":
        try:
            clarification = groq_client.ask_clarification(
                raw_text, meta.get("missing_fields") or [], preferred_language=preferred_language,
            )
        except Exception:
            clarification = _bot_msg("need_description", reply_lang)
        db.session.commit()
        return clarification

    if not category_id:
        # Gave up after MAX_CLARIFICATION_TURNS with no category resolved —
        # a DemandCluster requires one, so there is nothing to cluster into.
        # Save what we have for a human reviewer rather than keep looping
        # or silently dropping the citizen's report.
        db.session.commit()
        return _bot_msg("give_up", reply_lang)

    match_result = None
    if category_id and country_id:
        try:
            match_result = find_similar_clusters(
                report_text=problem_summary,
                category_id=category_id, country_id=country_id,
            )
        except Exception as e:
            logger.warning("WhatsApp demand matching failed, will start a new cluster: %s", e)

    if match_result and match_result.tier == "auto_suggest":
        cluster_id = match_result.matches[0].cluster_id
        db.session.add(Contribution(
            report_id=report.id, anonymous_token=anonymous_token,
            demand_cluster_id=cluster_id, type="joined",
        ))
        report.status = "Clustered"
        db.session.add(EventLog(report_id=report.id, demand_cluster_id=cluster_id, stage="JoinedDemand"))
        db.session.commit()
        cluster = db.session.get(DemandCluster, cluster_id)
        contributors = cluster.unique_contributors if cluster else None
        return _bot_msg("join_existing", reply_lang, n=contributors or "several")

    cluster = DemandCluster(
        country_id=country_id, region_ids=[region_id] if region_id else [],
        category_id=category_id, affected_localities=[], trend="stable", confidence="low",
        active_status="Active", review_status="NotReviewed",
    )
    db.session.add(cluster)
    db.session.flush()
    try:
        store_cluster_embedding(cluster.id, problem_summary)
    except Exception as e:
        logger.warning("store_cluster_embedding failed for WhatsApp cluster (still created): %s", e)
    db.session.add(Contribution(
        report_id=report.id, anonymous_token=anonymous_token,
        demand_cluster_id=cluster.id, type="joined",
    ))
    report.status = "Clustered"
    db.session.add(EventLog(report_id=report.id, demand_cluster_id=cluster.id, stage="JoinedDemand"))
    db.session.commit()
    return _bot_msg("new_cluster", reply_lang)


def _groq_model_name() -> str:
    return os.environ.get("GROQ_MODEL", "qwen/qwen3.8-27b")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@whatsapp_bp.route("/webhook", methods=["POST"])
@csrf.exempt
def webhook():
    """Production Twilio WhatsApp inbound-message webhook."""
    if not _validate_twilio_signature():
        logger.warning("Rejected WhatsApp webhook — invalid Twilio signature.")
        return Response(status=403)

    message_sid = request.form.get("MessageSid", "")
    from_number = request.form.get("From", "")
    body = (request.form.get("Body") or "").strip()
    num_media = int(request.form.get("NumMedia", "0") or "0")

    if not message_sid or not from_number:
        return Response(status=400)

    # Duplicate-delivery guard — Twilio may retry a webhook that didn't get a
    # fast 2xx response. A second delivery of the same MessageSid must never
    # create a second Report.
    if WhatsAppMessageLog.query.filter_by(message_sid=message_sid).first() is not None:
        logger.info("Duplicate WhatsApp delivery for %s — not reprocessing.", message_sid)
        return _twiml("We already received this message — thank you.")

    from_hash = _phone_hash(from_number)
    log_row = WhatsAppMessageLog(
        message_sid=message_sid, from_number_hash=from_hash, status="received",
    )
    db.session.add(log_row)
    db.session.commit()
    logger.info("WhatsApp message received: sid=%s media=%s", message_sid, num_media)

    preferred_language = _get_preferred_language(from_hash)

    # Explicit language-switch command ("reply in Hindi") — never runs the
    # report pipeline at all, so it can't be misread as an issue description.
    new_lang = _detect_language_switch(body) if body else None
    if new_lang:
        log_row.preferred_language = new_lang
        log_row.status = "processed"
        db.session.commit()
        return _twiml(_bot_msg("lang_switched", new_lang, lang=_LANGUAGE_SELF_NAMES[new_lang]))

    # Bare greeting ("hi", "help", "menu") — send the welcome/help message
    # instead of trying (and failing) to extract a report from "hi".
    if body and _is_greeting(body):
        log_row.status = "processed"
        db.session.commit()
        reply_lang = preferred_language or "en"
        return _twiml(_WELCOME_MESSAGES.get(reply_lang, _WELCOME_MESSAGES["en"]))

    try:
        raw_text = body
        image_bytes, image_content_type = None, None

        if num_media > 0:
            media_type = request.form.get("MediaContentType0") or ""
            media_url = request.form.get("MediaUrl0") or ""
            if media_type.startswith("audio") and media_url and not raw_text:
                raw_text = _transcribe_voice_media(media_url)
            elif media_type.startswith("image") and media_url:
                try:
                    image_bytes, image_content_type = _download_media(media_url)
                except Exception as e:
                    logger.warning("WhatsApp image download failed: %s", e)
            elif not raw_text:
                log_row.status = "failed"
                log_row.error_message = f"Unsupported media type: {media_type or 'unknown'}"
                db.session.commit()
                return _twiml(_bot_msg("unsupported_media", preferred_language or "en"))

        anonymous_token = f"whatsapp:{log_row.from_number_hash}"
        if not raw_text and image_bytes and _find_open_draft(anonymous_token):
            # A caption-less photo sent mid-conversation ("here's a photo of
            # it") — attach it to whatever's already open rather than
            # demanding new text just because this particular message had none.
            raw_text = "(photo attached)"

        if not raw_text:
            log_row.status = "failed"
            log_row.error_message = "Empty message"
            db.session.commit()
            return _twiml(_bot_msg("need_description", preferred_language or "en"))

        reply_text = _process_intake_message(
            raw_text, from_number, log_row, image_bytes=image_bytes, image_content_type=image_content_type,
            preferred_language=preferred_language,
        )
        log_row.status = "processed"
        db.session.commit()
        return _twiml(reply_text)

    except Exception as e:
        logger.exception("WhatsApp webhook processing failed for sid=%s", message_sid)
        log_row.status = "failed"
        log_row.error_message = f"{type(e).__name__}: {str(e)[:500]}"
        db.session.commit()
        return _twiml(_bot_msg("processing_error", preferred_language or "en"))


@whatsapp_bp.route("/test-send", methods=["POST"])
@csrf.exempt
def test_send():
    """
    LOCAL/DEV TEST ADAPTER — NOT real WhatsApp delivery.

    Simulates one inbound WhatsApp message by calling the exact same
    _process_intake_message() pipeline the real Twilio webhook uses (real
    Groq extraction, real Cohere embedding, real pgvector matching, real
    Report/DemandCluster/Contribution rows) without needing Twilio
    credentials, a public webhook URL, or a signature.

    Gated behind WHATSAPP_TEST_ADAPTER=1 so it can never be reachable in a
    deployment that hasn't explicitly opted in — this is a safety gate, not
    a default-on endpoint.

    POST JSON: {"from": "whatsapp:+91XXXXXXXXXX" (optional), "text": "..."}
    """
    if os.environ.get("WHATSAPP_TEST_ADAPTER") != "1":
        return Response(status=404)

    data = request.get_json(silent=True) or {}
    from_number = (data.get("from") or "whatsapp:+919999999999").strip()
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400

    from_hash = _phone_hash(from_number)
    fake_sid = "test-" + hashlib.sha256((from_number + text + str(datetime.now(timezone.utc))).encode()).hexdigest()[:24]
    log_row = WhatsAppMessageLog(
        message_sid=fake_sid, from_number_hash=from_hash, status="received",
    )
    db.session.add(log_row)
    db.session.commit()

    preferred_language = _get_preferred_language(from_hash)

    new_lang = _detect_language_switch(text)
    if new_lang:
        log_row.preferred_language = new_lang
        log_row.status = "processed"
        db.session.commit()
        reply = _bot_msg("lang_switched", new_lang, lang=_LANGUAGE_SELF_NAMES[new_lang])
        return jsonify({"reply": reply, "report_id": None, "message_sid": fake_sid})

    if _is_greeting(text):
        log_row.status = "processed"
        db.session.commit()
        reply_lang = preferred_language or "en"
        reply = _WELCOME_MESSAGES.get(reply_lang, _WELCOME_MESSAGES["en"])
        return jsonify({"reply": reply, "report_id": None, "message_sid": fake_sid})

    try:
        reply = _process_intake_message(text, from_number, log_row, preferred_language=preferred_language)
        log_row.status = "processed"
        db.session.commit()
        return jsonify({"reply": reply, "report_id": log_row.report_id, "message_sid": fake_sid})
    except Exception as e:
        logger.exception("WhatsApp test-send failed")
        log_row.status = "failed"
        log_row.error_message = str(e)[:500]
        db.session.commit()
        return jsonify({"error": str(e)}), 500


@whatsapp_bp.route("/status-callback", methods=["POST"])
@csrf.exempt
def status_callback():
    """
    Twilio delivery-status callback — a separate webhook from /webhook,
    with a different payload (MessageStatus: queued/sent/delivered/read/
    failed/undelivered, no Body/NumMedia). Twilio only requires a 200
    response here; this just logs the status against the matching
    WhatsAppMessageLog row (by MessageSid) for observability. Never treats
    a status ping as an inbound citizen message — pointing Twilio's "When a
    message comes in" webhook at this route (or vice versa) would be wrong.
    """
    if not _validate_twilio_signature():
        logger.warning("Rejected WhatsApp status callback — invalid Twilio signature.")
        return Response(status=403)

    message_sid = request.form.get("MessageSid", "")
    message_status = request.form.get("MessageStatus", "")
    error_code = request.form.get("ErrorCode") or None

    logger.info("WhatsApp delivery status: sid=%s status=%s error_code=%s",
                message_sid, message_status, error_code)

    log_row = WhatsAppMessageLog.query.filter_by(message_sid=message_sid).first()
    if log_row is not None and message_status in ("failed", "undelivered") and error_code:
        log_row.error_message = f"Delivery {message_status}: Twilio error {error_code}"
        db.session.commit()

    return Response(status=200)
