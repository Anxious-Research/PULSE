"""Kokoro 82M Multilingual Expressive TTS Synthesizer for PULSE.

Optimized for Hindi / Hinglish / English on-device synthesis:
- Expressive prosody: converts reaction markers (*smiles*, *laughs*) into natural breath pauses.
- Strips emojis and disruptive markdown symbols so they aren't spoken as literal words.
- Natural voice blending: mixes native Hindi accents (hf_alpha/hm_omega) with warm prosody styles.
- High-fidelity 24kHz audio synthesis with zero cloud dependencies.
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

DEFAULT_VOICE = "hf_alpha"  # Hindi Female Alpha (Optimized for Hinglish)

# Broad emoji cleanup regex to prevent TTS reading "grinning face with smiling eyes"
_EMOJI_PATTERN = re.compile(
    "[\U0001F1E6-\U0001F1FF\U0001F300-\U0001F5FF\U0001F600-\U0001F64F\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F\U0001F780-\U0001F7FF\U0001F800-\U0001F8FF\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FAFF\u2600-\u26FF\u2700-\u27BF]+",
    flags=re.UNICODE,
)


def clean_expressive_text(text: str) -> str:
    """Prepare text for expressive synthesis: strip symbols/emojis and preserve prosodic rhythm."""
    if not text:
        return ""

    # 1. Hyphenated compound words like kaha-kaha, chhoti-chhoti -> space separation
    text = re.sub(r"(\w+)-(\w+)", r"\1 \2", str(text))

    # 2. Standalone dashes / hyphens become soft phrase pauses
    text = re.sub(r"\s*[-—–]+\s*", ", ", text)

    # 3. Strip roleplay/reaction action markers into natural prosody pauses
    # e.g. *smiles*, *laughs*, *chuckles*, *sighs* -> natural pause "..."
    text = re.sub(r"\*[a-zA-Z\s_\-]+\*", "... ", text)
    text = re.sub(r"\([a-zA-Z\s_\-]{3,25}\)", ", ", text)

    # 4. Strip emojis completely
    text = _EMOJI_PATTERN.sub("", text)

    # 5. Strip code blocks and technical noise
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)

    # 6. Strip markdown formatting syntax
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    text = re.sub(r"#+\s*", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^\s*[-*•]\s+", ", ", text, flags=re.MULTILINE)
    text = re.sub(r"\|[^|\n]+\|", " ", text)  # tables

    # 7. Remove disruptive punctuation characters that cause literal word reading
    text = re.sub(r"[_~|\\/^<>{}=+@#$]", " ", text)

    # 8. Smooth out consecutive punctuation and spaces
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
    lang: str = "en-us",
) -> str:
    """Synthesize text into expressive speech using Kokoro 82M."""
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

    # Convert text to IPA phonemes using Latin script Hinglish phonemizer
    phonemes = kokoro.tokenizer.phonemize(clean_text, lang="en-us")

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
    parser = argparse.ArgumentParser(description="Kokoro-82M Multilingual Expressive Synthesizer")
    parser.add_argument("--text", type=str, help="Text to speak")
    parser.add_argument("--in", dest="input_file", type=str, help="Path to input text file")
    parser.add_argument("--out", dest="output_file", type=str, required=True, help="Path to output audio file (.wav)")
    parser.add_argument("--voice", type=str, default=DEFAULT_VOICE, help="Voice name (e.g. hf_alpha, hm_omega, af_heart)")
    parser.add_argument("--speed", type=float, default=1.0, help="Speech speed multiplier")
    parser.add_argument("--lang", type=str, default="en-us", help="Language code (en-us, hi)")

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
