"""Kokoro 82M Multilingual Semantic Hinglish TTS Synthesizer for PULSE.

Optimized for authentic Hindi & Hinglish synthesis:
- Semantic language detection: transliterates Romanized Hinglish words into Devanagari
  so phonemization follows Hindi grammar and native accent rather than broken English.
- Natural number expansion: converts numeric digits (1, 2, 5, 10, 22, 50k) into spoken
  Hindi counting words (एक, दो, पाँच, दस, बाईस, पचास हज़ार).
- Expressive prosody: turns reaction markers (*smiles*, *laughs*) into breath pauses.
- Strips emojis and technical markdown artifacts.
- Zero-leak IPA phoneme pipeline with no "(en)/(hi)" phonetic artifacts.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional
import numpy as np
from pulse_constants import get_pulse_home

logger = logging.getLogger("tools.kokoro_synth")

KOKORO_MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
KOKORO_VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"

DEFAULT_VOICE = "hm_omega:0.65,am_onyx:0.35"  # Deep Grounded Male Voice (Hindi + Onyx Baritone)

# Broad emoji cleanup regex
_EMOJI_PATTERN = re.compile(
    "[\U0001F1E6-\U0001F1FF\U0001F300-\U0001F5FF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F\U0001F780-\U0001F7FF\U0001F800-\U0001F8FF\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF\u2600-\u26FF\u2700-\u27BF]+",
    flags=re.UNICODE,
)

# Number dictionary for Hindi counting
HINDI_NUMBERS = {
    0: "शून्य", 1: "एक", 2: "दो", 3: "तीन", 4: "चार", 5: "पाँच",
    6: "छह", 7: "सात", 8: "आठ", 9: "नौ", 10: "दस",
    11: "ग्यारह", 12: "बारह", 13: "तेरह", 14: "चौदह", 15: "पंद्रह",
    16: "सोलह", 17: "सत्रह", 18: "अठारह", 19: "उन्नीस", 20: "बीस",
    21: "इक्कीस", 22: "बाईस", 23: "तेईस", 24: "चौबीस", 25: "पच्चीस",
    26: "छब्बीस", 27: "सत्ताईस", 28: "अट्ठाईस", 29: "उनतीस", 30: "तीस",
    31: "इकत्तीस", 32: "बत्तीस", 33: "तैंतीस", 34: "चौंतीस", 35: "पैंतीस",
    36: "छत्तीस", 37: "सैंतीस", 38: "अड़तीस", 39: "उनतालीस", 40: "चालीस",
    41: "इकतालीस", 42: "बयालीस", 43: "तैंतालीस", 44: "चवालीस", 45: "पैंतालीस",
    46: "छियालीस", 47: "सैंतालीस", 48: "अड़तालीस", 49: "उनचास", 50: "पचास",
    51: "इक्यावन", 52: "बावन", 53: "तिरेपन", 54: "चौवन", 55: "पचपन",
    56: "छप्पन", 57: "सत्तावन", 58: "अट्ठावन", 59: "उनसठ", 60: "साठ",
    61: "इकसठ", 62: "बासठ", 63: "तिरसठ", 64: "चौंसठ", 65: "पैंसठ",
    66: "छियासठ", 67: "सरसठ", 68: "अड़सठ", 69: "उनहत्तर", 70: "सत्तर",
    71: "इकहत्तर", 72: "बहत्तर", 73: "तिहत्तर", 74: "चौहत्तर", 75: "पचहत्तर",
    76: "छिहत्तर", 77: "सतहत्तर", 78: "अठहत्तर", 79: "उनासी", 80: "अस्सी",
    81: "इक्यासी", 82: "बयासी", 83: "तिरासी", 84: "चौरासी", 85: "पचासी",
    86: "छियासी", 87: "सत्तासी", 88: "अट्ठासी", 89: "नवासी", 90: "नब्बे",
    91: "इक्यानवे", 92: "बानवे", 93: "तिरानवे", 94: "चौरानवे", 95: "पचानवे",
    96: "छियानवे", 97: "सत्तानवे", 98: "अट्ठानवे", 99: "निन्यानवे", 100: "सौ",
}

# Semantic Hinglish vocabulary: converts Romanized Hindi functional words to Devanagari
# so phonetic rules pronounce them as authentic Indian Hindi instead of English.
HINGLISH_LEXICON = {
    # Pronouns
    "main": "मैं", "mein": "में", "me": "में", "hum": "हम", "aap": "आप", "tum": "तुम",
    "tu": "तू", "ye": "ये", "wo": "वो", "voh": "वो", "unhe": "उन्हें", "hume": "हमें",
    "mujhe": "मुझे", "tujhe": "तुझे", "mera": "मेरा", "meri": "मेरी", "mere": "मेरे",
    "apna": "अपना", "apni": "अपनी", "apne": "अपने", "unka": "उनका", "unki": "उनकी",
    "unke": "उनके", "iska": "इसका", "iski": "इसकी", "iske": "इसके", "inka": "इनका",
    "inki": "इनकी", "inke": "इनके", "kiska": "किसका", "kiski": "किसकी", "kiske": "किसके",
    "kisi": "किसी", "sab": "सब", "sabhi": "सभी", "kuch": "कुछ", "koi": "कोई",

    # Prepositions & Postpositions
    "se": "से", "ko": "को", "ka": "का", "ke": "के", "ki": "की", "par": "पर", "pe": "पे",
    "tak": "तक", "liye": "लिए", "saath": "साथ", "bina": "बिना",

    # Conjunctions & Particles
    "aur": "और", "or": "और", "to": "तो", "bhi": "भी", "lekin": "लेकिन", "magar": "मगर",
    "parantu": "परंतु", "ya": "या", "isliye": "इसलिए", "kyunki": "क्योंकि", "kyoki": "क्योंकि",
    "agar": "अगर", "yadi": "यदि", "jabki": "जबकि", "warna": "वरना",

    # Question words
    "kya": "क्या", "kyu": "क्यों", "kyun": "क्यों", "kaise": "कैसे", "kaisi": "कैसी",
    "kaisa": "कैसा", "kaha": "कहाँ", "kahan": "कहाँ", "kab": "कब", "kitne": "कितने",
    "kitna": "कितना", "kitni": "कितनी", "kaun": "कौन",

    # Auxiliaries & Verbs
    "hai": "है", "hain": "हैं", "ho": "हो", "hoon": "हूँ", "hun": "हूँ", "tha": "था",
    "thi": "थी", "the": "थे", "raha": "रहा", "rahe": "रहे", "rahi": "रही",
    "kar": "कर", "karo": "करो", "karna": "करना", "karne": "करने", "karte": "करते",
    "karta": "करता", "karti": "करती", "kiya": "किया", "kiye": "किए", "kijiye": "कीजिए",
    "de": "दे", "do": "दो", "dena": "देना", "dene": "देने", "diya": "दिया", "diye": "दिए",
    "le": "ले", "lo": "लो", "lena": "लेना", "lene": "लेने", "liya": "लिया", "liye": "लिए",
    "ja": "जा", "jao": "जाओ", "jana": "जाना", "jane": "जाने", "jata": "जाता", "jate": "जाते",
    "gaya": "गया", "gaye": "गए", "gayi": "गई", "aana": "आना", "aao": "आओ", "aata": "आता",
    "aate": "आते", "aaye": "आए", "aayi": "आई", "aa": "आ",
    "hoga": "होगा", "hogi": "होगी", "honge": "होंगे", "sakta": "सकता", "sakte": "सकते",
    "sakti": "सकती", "chahiye": "चाहिए", "padega": "पड़ेगा", "padenge": "पड़ेंगे",
    "dekh": "देख", "dekho": "देखो", "dekhna": "देखना", "bol": "बोल", "bolo": "बोलो",
    "bolna": "बोलना", "bolti": "बोलती", "bolta": "बोलता", "bolte": "बोलते",

    # Adverbs, Adjectives & Common expressions
    "ab": "अब", "tab": "तब", "jab": "जब", "aaj": "आज", "kal": "कल", "parso": "परसों",
    "pehle": "पहले", "baad": "बाद", "yaha": "यहाँ", "yahan": "यहाँ", "waha": "वहाँ",
    "wahan": "वहाँ", "jaha": "जहाँ", "jahan": "जहाँ",
    "aise": "ऐसे", "waise": "वैसे", "jaise": "जैसे", "kaise": "कैसे",
    "jitne": "जितने", "jitna": "जितना", "jitni": "जितनी", "utna": "उतना", "itna": "इतना",
    "bahut": "बहुत", "bohot": "बहुत", "kam": "कम", "zyada": "ज़्यादा", "jyada": "ज़्यादा",
    "sirf": "सिर्फ", "bilkul": "बिल्कुल", "ekdum": "एकदम", "achha": "अच्छा", "accha": "अच्छा",
    "theek": "ठीक", "thik": "ठीक", "sahi": "सही", "galat": "गलत", "badiya": "बढ़िया",
    "badhiya": "बढ़िया", "saare": "सारे", "sari": "सारी", "saara": "सारा",
    "chhoti": "छोटी", "chhota": "छोटा", "chote": "छोटे", "bada": "बड़ा", "badi": "बड़ी",
    "bade": "बड़े", "samajh": "समझ", "baat": "बात", "baatein": "बातें", "baaton": "बातों",
    "nahi": "नहीं", "nhi": "नहीं", "na": "ना", "haan": "हाँ", "namaste": "नमस्ते",
    "shukriya": "शुक्रिया", "dhanyawad": "धन्यवाद", "dost": "दोस्त", "bhai": "भाई",
}


def expand_numbers_to_hindi(text: str) -> str:
    """Convert digit numbers and scale multipliers (e.g. 5, 22, 50k, 1000) to spoken Hindi words."""
    # Scale abbreviations: 50k -> पचास हज़ार, 10k -> दस हज़ार
    text = re.sub(
        r"\b(\d+)\s*[kK]\b",
        lambda m: f"{HINDI_NUMBERS.get(int(m.group(1)), m.group(1))} हज़ार",
        text,
    )

    # Standard integers 0 to 100
    def replace_num(match):
        num_str = match.group(0)
        try:
            val = int(num_str)
            if val in HINDI_NUMBERS:
                return HINDI_NUMBERS[val]
            elif val == 1000:
                return "एक हज़ार"
            elif val == 100000:
                return "एक लाख"
            elif val == 10000000:
                return "एक करोड़"
            return num_str
        except ValueError:
            return num_str

    text = re.sub(r"\b\d+\b", replace_num, text)
    return text


def normalize_hinglish_to_devanagari(text: str) -> str:
    """Transliterate Romanized Hinglish functional words into Devanagari

    Preserves English technical terms (code, text, system, agent) in Latin script
    so they are pronounced in English, while Hindi words sound authentically Indian.
    """
    words = text.split()
    converted = []
    for w in words:
        # Match leading punctuation, word core, and trailing punctuation
        m = re.match(r"^([^\w]*)([\w]+)([^\w]*)$", w)
        if m:
            pre, core, post = m.groups()
            low = core.lower()
            trans = HINGLISH_LEXICON.get(low, core)
            converted.append(f"{pre}{trans}{post}")
        else:
            converted.append(w)
    return " ".join(converted)


def clean_expressive_text(text: str) -> str:
    """Prepare text for expressive synthesis: strip symbols/emojis, transliterate Hinglish, and expand numbers."""
    if not text:
        return ""

    # 1. Expand numbers to Hindi counting words
    text = expand_numbers_to_hindi(text)

    # 2. Hyphenated compound words like kaha-kaha, chhoti-chhoti -> space separation
    text = re.sub(r"(\w+)-(\w+)", r"\1 \2", str(text))

    # 3. Standalone dashes / hyphens become soft phrase pauses
    text = re.sub(r"\s*[-—–]+\s*", ", ", text)

    # 4. Strip roleplay/reaction action markers into natural prosody pauses
    # e.g. *smiles*, *laughs*, *chuckles*, *sighs* -> natural pause "..."
    text = re.sub(r"\*[a-zA-Z\s_\-]+\*", "... ", text)
    text = re.sub(r"\([a-zA-Z\s_\-]{3,25}\)", ", ", text)

    # 5. Strip emojis completely
    text = _EMOJI_PATTERN.sub("", text)

    # 6. Strip code blocks and technical noise
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)

    # 7. Strip markdown formatting syntax
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"#+\s*", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^\s*[-*•]\s+", ", ", text, flags=re.MULTILINE)
    text = re.sub(r"\|[^|\n]+\|", " ", text)  # tables

    # 8. Remove disruptive punctuation characters that cause literal word reading
    text = re.sub(r"[_~|\\/^<>{}=+@#$]", " ", text)

    # 9. Transliterate Romanized Hinglish functional words to Devanagari
    text = normalize_hinglish_to_devanagari(text)

    # 10. Smooth out consecutive punctuation and spaces
    text = re.sub(r"\.{4,}", "...", text)
    text = re.sub(r",\s*,+", ",", text)
    text = re.sub(r"\s+", " ", text).strip()

    return text


def get_kokoro_model_paths() -> tuple[Path, Path]:
    models_dir = get_pulse_home() / "cache" / "models" / "kokoro"
    models_dir.mkdir(parents=True, exist_ok=True)
    model_path = models_dir / "kokoro-v1.0.onnx"
    voices_path = models_dir / "voices-v1.0.bin"
    return model_path, voices_path


def ensure_kokoro_models() -> tuple[Path, Path]:
    import urllib.request
    model_path, voices_path = get_kokoro_model_paths()

    if not model_path.exists():
        logger.info("Downloading Kokoro ONNX model (82M)...")
        urllib.request.urlretrieve(KOKORO_MODEL_URL, model_path)

    if not voices_path.exists():
        logger.info("Downloading Kokoro voices library...")
        urllib.request.urlretrieve(KOKORO_VOICES_URL, voices_path)

    return model_path, voices_path


def get_espeak_config():
    """Resolve espeak-ng library and data paths safely for macOS/Linux."""
    try:
        import espeakng_loader
        pkg_dir = Path(espeakng_loader.__file__).parent
        data_dir = pkg_dir / "espeak-ng-data"
        if data_dir.exists():
            for f in data_dir.iterdir():
                target = pkg_dir / f.name
                if not target.exists():
                    try:
                        target.symlink_to(f)
                    except Exception:
                        pass
        from kokoro_onnx.config import EspeakConfig
        from phonemizer.backend.espeak.wrapper import EspeakWrapper
        EspeakWrapper.set_data_path(str(pkg_dir))
        EspeakWrapper.set_library(str(espeakng_loader.get_library_path()))
        return EspeakConfig(lib_path=str(espeakng_loader.get_library_path()), data_path=str(pkg_dir))
    except Exception as exc:
        logger.debug("espeakng_loader init note: %s", exc)
        return None


def resolve_voice_style(kokoro: Any, voice_spec: str) -> Any:
    """Resolve voice or blend multiple voice styles for warmer expressive prosody."""
    if not voice_spec:
        voice_spec = DEFAULT_VOICE

    # Custom multi-voice blend syntax: "hf_alpha,af_heart" or "hf_alpha:0.7,af_heart:0.3"
    if "," in voice_spec:
        parts = voice_spec.split(",")
        styles = []
        weights = []
        for p in parts:
            if ":" in p:
                name, w = p.split(":", 1)
                styles.append(kokoro.get_voice_style(name.strip()))
                weights.append(float(w))
            else:
                styles.append(kokoro.get_voice_style(p.strip()))
                weights.append(1.0)
        total_w = sum(weights)
        norm_weights = [w / total_w for w in weights]
        blended = sum(s * w for s, w in zip(styles, norm_weights))
        return blended

    # Default Expressive Blending for Hindi voices:
    # 75% Hindi phonetic accuracy + 25% American Heart warmth & expressive prosody
    if voice_spec == "hf_alpha":
        v_hindi = kokoro.get_voice_style("hf_alpha")
        v_warmth = kokoro.get_voice_style("af_heart")
        return (v_hindi * 0.75) + (v_warmth * 0.25)
    elif voice_spec == "hm_omega":
        v_hindi = kokoro.get_voice_style("hm_omega")
        v_warmth = kokoro.get_voice_style("am_adam")
        return (v_hindi * 0.75) + (v_warmth * 0.25)

    return voice_spec


def synthesize_kokoro(
    text: str,
    output_path: str,
    voice: str = DEFAULT_VOICE,
    speed: float = 1.0,
    lang: str = "hi",
) -> str:
    """Synthesize text into expressive speech using Kokoro 82M with authentic Hindi phonetics."""
    from kokoro_onnx import Kokoro
    import soundfile as sf

    clean_text = clean_expressive_text(text)
    if not clean_text:
        clean_text = "..."

    model_path, voices_path = ensure_kokoro_models()
    espeak_cfg = get_espeak_config()

    kokoro = Kokoro(
        model_path=str(model_path),
        voices_path=str(voices_path),
        espeak_config=espeak_cfg,
    )

    resolved_voice = resolve_voice_style(kokoro, voice)

    # Phonemize with Hindi backend so Devanagari transliterations produce authentic Indian IPA
    phonemes = kokoro.tokenizer.phonemize(clean_text, lang="hi")

    # Strip any leaked internal language switch markers emitted by espeak-ng:
    # e.g. (en), (hi), (es) which get read aloud as "nos" or "en"
    phonemes = re.sub(r"\([a-zA-Z]{2,4}\)", "", phonemes)
    # Strip any dangling hyphens or raw symbols in phoneme stream
    phonemes = re.sub(r"\s*[-—–_]+\s*", " ", phonemes).strip()

    samples, sample_rate = kokoro.create(
        phonemes,
        voice=resolved_voice,
        speed=speed,
        is_phonemes=True,
    )

    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_p), samples, sample_rate)
    logger.info("Synthesized %d samples to %s (voice='%s')", len(samples), out_p, voice)
    return str(out_p)


def main():
    parser = argparse.ArgumentParser(description="Kokoro-82M Multilingual Semantic Synthesizer")
    parser.add_argument("--text", type=str, help="Text to speak")
    parser.add_argument("--in", dest="input_file", type=str, help="Path to input text file")
    parser.add_argument("--out", dest="output_file", type=str, required=True, help="Path to output audio file (.wav)")
    parser.add_argument("--voice", type=str, default=DEFAULT_VOICE, help="Voice name (e.g. hf_alpha, hm_omega, af_heart)")
    parser.add_argument("--speed", type=float, default=1.0, help="Speech speed multiplier")
    parser.add_argument("--lang", type=str, default="hi", help="Language code (hi, en-us)")

    args = parser.parse_args()

    if args.input_file:
        text = Path(args.input_file).read_text(encoding="utf-8")
    elif args.text:
        text = args.text
    else:
        text = sys.stdin.read()

    synthesize_kokoro(
        text=text,
        output_path=args.output_file,
        voice=args.voice,
        speed=args.speed,
        lang=args.lang,
    )


if __name__ == "__main__":
    main()
