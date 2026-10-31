"""Kokoro 82M Multilingual TTS synthesizer for PULSE.

Optimized for Hindi / Hinglish / English on-device synthesis:
- Uses Kokoro-82M ONNX runtime with zero cloud dependencies.
- Automatically manages model cache under ~/.pulse/cache/models/kokoro/.
- High-fidelity 24kHz audio synthesis.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional
from pulse_constants import get_pulse_home

logger = logging.getLogger("tools.kokoro_synth")

KOKORO_MODEL_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
KOKORO_VOICES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"

DEFAULT_VOICE = "hf_alpha"  # Hindi Female Alpha (Optimized for Hinglish)


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


def synthesize_kokoro(
    text: str,
    output_path: str,
    voice: str = DEFAULT_VOICE,
    speed: float = 1.0,
    lang: str = "en-us",
) -> str:
    """Synthesize text into speech using Kokoro 82M."""
    from kokoro_onnx import Kokoro
    import soundfile as sf

    model_path, voices_path = ensure_kokoro_models()
    espeak_cfg = get_espeak_config()

    kokoro = Kokoro(
        model_path=str(model_path),
        voices_path=str(voices_path),
        espeak_config=espeak_cfg,
    )

    # For Hindi voices, allow phonetic Hindi / Hinglish processing
    selected_voice = voice if voice else DEFAULT_VOICE
    selected_lang = "hi" if selected_voice.startswith("h") and lang == "hi" else lang

    samples, sample_rate = kokoro.create(
        text=text,
        voice=selected_voice,
        speed=speed,
        lang=selected_lang,
    )

    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_p), samples, sample_rate)
    logger.info("Synthesized %d samples to %s with voice '%s'", len(samples), out_p, selected_voice)
    return str(out_p)


def main():
    parser = argparse.ArgumentParser(description="Kokoro-82M Multilingual Synthesizer")
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
        text=text.strip(),
        output_path=args.output_file,
        voice=args.voice,
        speed=args.speed,
        lang=args.lang,
    )


if __name__ == "__main__":
    main()
